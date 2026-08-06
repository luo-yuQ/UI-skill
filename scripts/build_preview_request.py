#!/usr/bin/env python3
"""Build a provider-neutral page preview request from UI compose contracts.

This adapter reads structured JSON only. It never opens source_ref targets,
performs image analysis, accesses a network, or invokes an image API.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any


PREVIEW_SCHEMA_VERSION = "0.1"
REQUIRED_PLAN_FIELDS = {
    "schema_version",
    "project_context",
    "visual_direction",
    "pages",
    "asset_usages",
    "component_tree",
    "layout_rules",
    "interactions",
    "navigation",
    "missing_assets",
    "assumptions",
    "warnings",
}
PREVIEW_REQUIRED_FIELDS = {
    "schema_version",
    "generation_intent",
    "source",
    "prompt",
    "reference_assets",
    "composition_requirements",
    "preserve_requirements",
    "allowed_changes",
    "avoid",
    "output_spec",
    "assumptions",
    "warnings",
}


def issue(path: str, message: str, code: str) -> dict[str, str]:
    return {"path": path, "message": message, "code": code}


def emit_error(
    errors: list[dict[str, str]], warnings: list[dict[str, Any]] | None = None
) -> None:
    print(
        json.dumps(
            {
                "status": "error",
                "error_code": "PREVIEW_REQUEST_BUILD_FAILED",
                "errors": errors,
                "warnings": warnings or [],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def load_json(path: Path, label: str) -> tuple[Any | None, list[dict[str, str]]]:
    if not path.exists() or not path.is_file():
        return None, [issue(f"$.{label}", f"File not found: {path}", "FILE_NOT_FOUND")]
    try:
        return json.loads(path.read_text(encoding="utf-8")), []
    except (OSError, json.JSONDecodeError) as exc:
        return None, [
            issue(f"$.{label}", f"Unable to read JSON: {exc}", "JSON_READ_ERROR")
        ]


def require_object(
    value: Any, path: str, errors: list[dict[str, str]]
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        errors.append(issue(path, "Expected an object", "TYPE_MISMATCH"))
        return None
    return value


def require_list(
    value: Any, path: str, errors: list[dict[str, str]]
) -> list[Any] | None:
    if not isinstance(value, list):
        errors.append(issue(path, "Expected an array", "TYPE_MISMATCH"))
        return None
    return value


def validate_top_level(input_data: Any, plan_data: Any) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    input_obj = require_object(input_data, "$.input", errors)
    plan_obj = require_object(plan_data, "$.plan", errors)

    if input_obj is not None:
        for field in ("schema_version", "request", "assets"):
            if field not in input_obj:
                errors.append(
                    issue(f"$.input.{field}", "Missing required field", "MISSING_REQUIRED_FIELD")
                )
        if "pics" in input_obj:
            errors.append(
                issue("$.input.pics", "Legacy pics input is forbidden", "LEGACY_PICS_FORBIDDEN")
            )
        if not isinstance(input_obj.get("schema_version"), str):
            errors.append(
                issue("$.input.schema_version", "Expected a string", "TYPE_MISMATCH")
            )
        require_object(input_obj.get("request"), "$.input.request", errors)
        require_list(input_obj.get("assets"), "$.input.assets", errors)

    if plan_obj is not None:
        for field in sorted(REQUIRED_PLAN_FIELDS):
            if field not in plan_obj:
                errors.append(
                    issue(f"$.plan.{field}", "Missing required field", "MISSING_REQUIRED_FIELD")
                )
        if not isinstance(plan_obj.get("schema_version"), str):
            errors.append(
                issue("$.plan.schema_version", "Expected a string", "TYPE_MISMATCH")
            )
        for field in (
            "project_context",
            "visual_direction",
        ):
            require_object(plan_obj.get(field), f"$.plan.{field}", errors)
        for field in (
            "pages",
            "asset_usages",
            "component_tree",
            "layout_rules",
            "interactions",
            "navigation",
            "missing_assets",
            "assumptions",
            "warnings",
        ):
            require_list(plan_obj.get(field), f"$.plan.{field}", errors)

    return deduplicate_issues(errors)


def build_input_asset_index(
    input_data: dict[str, Any], errors: list[dict[str, str]]
) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    assets = input_data.get("assets")
    if not isinstance(assets, list):
        return index

    for position, item in enumerate(assets):
        path = f"$.input.assets[{position}]"
        if not isinstance(item, dict):
            errors.append(issue(path, "Expected an object", "TYPE_MISMATCH"))
            continue
        analysis = item.get("asset_analysis")
        if not isinstance(analysis, dict):
            errors.append(
                issue(f"{path}.asset_analysis", "Missing asset_analysis object", "MISSING_ASSET_ANALYSIS")
            )
            continue
        asset_id = analysis.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id.strip():
            errors.append(
                issue(
                    f"{path}.asset_analysis.asset_id",
                    "asset_id must be a non-empty string",
                    "INVALID_ASSET_ID",
                )
            )
            continue
        if asset_id in index:
            errors.append(
                issue(f"{path}.asset_analysis.asset_id", f"Duplicate asset_id: {asset_id}", "DUPLICATE_ASSET_ID")
            )
            continue
        source_ref = item.get("source_ref")
        if not isinstance(source_ref, dict):
            errors.append(
                issue(f"{path}.source_ref", "Missing source_ref object", "MISSING_SOURCE_REF")
            )
            continue
        if not isinstance(source_ref.get("ref_type"), str) or not isinstance(
            source_ref.get("value"), str
        ) or not source_ref.get("value"):
            errors.append(
                issue(
                    f"{path}.source_ref",
                    "source_ref requires non-empty ref_type and value strings",
                    "INVALID_SOURCE_REF",
                )
            )
            continue
        index[asset_id] = {
            "source_ref": copy.deepcopy(source_ref),
            "asset_analysis": analysis,
        }
    return index


def select_page(
    plan_data: dict[str, Any], page_id: str, errors: list[dict[str, str]]
) -> dict[str, Any] | None:
    pages = plan_data.get("pages")
    if not isinstance(pages, list):
        return None
    matches = [page for page in pages if isinstance(page, dict) and page.get("page_id") == page_id]
    if not matches:
        errors.append(issue("$.page", f"Unknown page_id: {page_id}", "UNKNOWN_PAGE_ID"))
        return None
    if len(matches) > 1:
        errors.append(issue("$.plan.pages", f"Duplicate page_id: {page_id}", "DUPLICATE_PAGE_ID"))
        return None
    return matches[0]


def build_component_index(
    plan_data: dict[str, Any], page_id: str, errors: list[dict[str, str]]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    components: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    values = plan_data.get("component_tree")
    if not isinstance(values, list):
        return components, index
    for position, component in enumerate(values):
        if not isinstance(component, dict) or component.get("page_id") != page_id:
            continue
        component_id = component.get("component_id")
        if not isinstance(component_id, str) or not component_id:
            errors.append(
                issue(
                    f"$.plan.component_tree[{position}].component_id",
                    "component_id must be a non-empty string",
                    "INVALID_COMPONENT_ID",
                )
            )
            continue
        if component_id in index:
            errors.append(
                issue(
                    f"$.plan.component_tree[{position}].component_id",
                    f"Duplicate component_id in page: {component_id}",
                    "DUPLICATE_COMPONENT_ID",
                )
            )
            continue
        components.append(component)
        index[component_id] = component
    return components, index


def validate_page_references(
    plan_data: dict[str, Any],
    page: dict[str, Any],
    component_index: dict[str, dict[str, Any]],
    asset_index: dict[str, dict[str, Any]],
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    page_id = page["page_id"]
    root_component_id = page.get("root_component_id")
    if root_component_id not in component_index:
        errors.append(
            issue(
                "$.plan.pages.root_component_id",
                f"Unknown root component_id for page {page_id}: {root_component_id}",
                "UNKNOWN_COMPONENT_ID",
            )
        )

    usages: list[dict[str, Any]] = []
    values = plan_data.get("asset_usages")
    if isinstance(values, list):
        for position, usage in enumerate(values):
            if not isinstance(usage, dict) or usage.get("page_id") != page_id:
                continue
            usages.append(usage)
            asset_id = usage.get("asset_id")
            component_id = usage.get("component_id")
            if asset_id not in asset_index:
                errors.append(
                    issue(
                        f"$.plan.asset_usages[{position}].asset_id",
                        f"Unknown asset_id: {asset_id}",
                        "UNKNOWN_ASSET_ID",
                    )
                )
            if component_id not in component_index:
                errors.append(
                    issue(
                        f"$.plan.asset_usages[{position}].component_id",
                        f"Unknown component_id for page {page_id}: {component_id}",
                        "UNKNOWN_COMPONENT_ID",
                    )
                )

    layouts = plan_data.get("layout_rules")
    if isinstance(layouts, list):
        for position, layout in enumerate(layouts):
            if not isinstance(layout, dict) or layout.get("page_id") != page_id:
                continue
            component_id = layout.get("component_id")
            if component_id not in component_index:
                errors.append(
                    issue(
                        f"$.plan.layout_rules[{position}].component_id",
                        f"Unknown component_id for page {page_id}: {component_id}",
                        "UNKNOWN_COMPONENT_ID",
                    )
                )

    interactions = plan_data.get("interactions")
    if isinstance(interactions, list):
        for position, interaction in enumerate(interactions):
            if not isinstance(interaction, dict) or interaction.get("page_id") != page_id:
                continue
            component_id = interaction.get("trigger_component_id")
            if component_id not in component_index:
                errors.append(
                    issue(
                        f"$.plan.interactions[{position}].trigger_component_id",
                        f"Unknown component_id for page {page_id}: {component_id}",
                        "UNKNOWN_COMPONENT_ID",
                    )
                )

    return usages


def preservation_priority(visual_priority: Any) -> str:
    return {
        "critical": "critical",
        "primary": "high",
        "background": "high",
        "supporting": "medium",
    }.get(visual_priority, "medium")


def build_reference_assets(
    usages: list[dict[str, Any]], asset_index: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    seen: set[str] = set()
    for usage in usages:
        asset_id = usage["asset_id"]
        if asset_id in seen:
            continue
        seen.add(asset_id)
        references.append(
            {
                "order": len(references) + 1,
                "asset_id": asset_id,
                "source_ref": copy.deepcopy(asset_index[asset_id]["source_ref"]),
                "component_id": usage["component_id"],
                "usage": usage.get("usage_intent") or usage.get("semantic_role") or "Page reference asset",
                "preservation_priority": preservation_priority(usage.get("visual_priority")),
            }
        )
    return references


def format_number(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def component_label(component: dict[str, Any]) -> str:
    return str(component.get("name") or component.get("component_id") or "未命名组件")


def build_hierarchy_lines(
    components: list[dict[str, Any]], component_index: dict[str, dict[str, Any]]
) -> list[str]:
    lines: list[str] = []
    for component in sorted(components, key=lambda item: (item.get("order", 0), item.get("component_id", ""))):
        parent_id = component.get("parent_id")
        semantic_type = str(component.get("semantic_type") or "component")
        if parent_id is None:
            relation = "页面根节点"
        else:
            parent = component_index.get(parent_id)
            relation = f"隶属于 {component_label(parent) if parent else parent_id}"
        lines.append(f"{component_label(component)}（{semantic_type}，{relation}）")
    return lines


def build_layout_lines(
    plan_data: dict[str, Any], page_id: str, component_index: dict[str, dict[str, Any]]
) -> list[str]:
    lines: list[str] = []
    values = plan_data.get("layout_rules")
    if not isinstance(values, list):
        return lines
    for rule in values:
        if not isinstance(rule, dict) or rule.get("page_id") != page_id:
            continue
        component_id = rule.get("component_id")
        component = component_index.get(component_id, {})
        position = rule.get("position") if isinstance(rule.get("position"), dict) else {}
        dimensions = rule.get("dimensions") if isinstance(rule.get("dimensions"), dict) else {}
        anchor = str(rule.get("anchor") or "unspecified").replace("_", " ")
        relative_to = str(rule.get("relative_to") or "parent").replace("_", " ")
        width = format_number(dimensions.get("width", "?"))
        height = format_number(dimensions.get("height", "?"))
        unit = str(dimensions.get("unit") or "unspecified").replace("_", " ")
        x = format_number(position.get("x", "?"))
        y = format_number(position.get("y", "?"))
        lines.append(
            f"{component_label(component)}：相对 {relative_to} 使用 {anchor} 锚点，"
            f"归一化位置约为 ({x}, {y})，尺寸约为 {width} x {height}（{unit}）。"
        )
    return lines


def related_missing_assets(
    plan_data: dict[str, Any], relevant_ids: set[str]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    values = plan_data.get("missing_assets")
    if not isinstance(values, list):
        return result
    for item in values:
        if not isinstance(item, dict):
            continue
        needed_for = item.get("needed_for")
        if isinstance(needed_for, list) and relevant_ids.intersection(
            value for value in needed_for if isinstance(value, str)
        ):
            result.append(item)
    return result


def related_plan_warnings(
    plan_data: dict[str, Any], relevant_ids: set[str]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    values = plan_data.get("warnings")
    if not isinstance(values, list):
        return result
    for item in values:
        if not isinstance(item, dict):
            continue
        related_ids = item.get("related_ids")
        if not isinstance(related_ids, list) or not related_ids:
            result.append(item)
        elif relevant_ids.intersection(
            value for value in related_ids if isinstance(value, str)
        ):
            result.append(item)
    return result


def warning_code(value: Any, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value or fallback)).strip("_").upper()
    return text or fallback


def build_request_warnings(
    plan_warnings: list[dict[str, Any]],
    page_id: str,
    reference_assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for item in plan_warnings:
        warnings.append(
            {
                "code": warning_code(item.get("warning_id"), "PLAN_WARNING"),
                "severity": item.get("severity") if item.get("severity") in {"info", "warning", "error"} else "warning",
                "message": str(item.get("message") or "Plan warning"),
                "related_ids": [
                    value
                    for value in item.get("related_ids", [])
                    if isinstance(value, str)
                ],
            }
        )

    warnings.append(
        {
            "code": "CONCEPT_PREVIEW_NOT_PIXEL_ACCURATE",
            "severity": "info",
            "message": "这是概念预览，并非像素级工程实现截图。",
            "related_ids": [page_id],
        }
    )
    if not reference_assets:
        warnings.append(
            {
                "code": "NO_REFERENCE_ASSETS",
                "severity": "warning",
                "message": "当前页面没有引用素材，预览只能依据结构化计划进行纯文本概念生成。",
                "related_ids": [page_id],
            }
        )
    return warnings


def build_preserve_requirements(
    references: list[dict[str, Any]], usages: list[dict[str, Any]]
) -> list[str]:
    first_usage = {usage["asset_id"]: usage for usage in usages if isinstance(usage.get("asset_id"), str)}
    requirements: list[str] = []
    for reference in references:
        usage = first_usage[reference["asset_id"]]
        order = reference["order"]
        requirements.append(
            f"参考图 {order} 必须保持素材身份与核心轮廓，按其计划用途使用：{reference['usage']}"
        )
        resizing = usage.get("resizing_policy")
        if isinstance(resizing, dict):
            protected = resizing.get("protected_region_note")
            if protected:
                requirements.append(f"参考图 {order} 的受保护特征：{protected}")
            slice_scaling = resizing.get("slice_scaling")
            if slice_scaling == "manual_confirmation":
                requirements.append(
                    f"参考图 {order} 的边缘缩放方式尚需人工确认，预览中不得破坏装饰边缘。"
                )
        for note in usage.get("notes", []):
            if isinstance(note, str) and note and "reanalysis" not in note.lower():
                requirements.append(f"参考图 {order} 补充保留要求：{note}")
    if not requirements:
        requirements.append("当前页面没有参考素材，不得假称保留了任何外部素材特征。")
    return unique_strings(requirements)


def build_composition_requirements(
    page: dict[str, Any],
    hierarchy_lines: list[str],
    layout_lines: list[str],
    plan_data: dict[str, Any],
    page_id: str,
    missing_assets: list[dict[str, Any]],
) -> list[str]:
    requirements = [
        f"页面类型为 {page.get('page_type')}，用途为：{page.get('purpose')}",
        "保持计划中的组件数量、语义层级和主要视觉优先级。",
    ]
    requirements.extend(f"组件层级：{line}" for line in hierarchy_lines)
    requirements.extend(f"布局要求：{line}" for line in layout_lines)

    interactions = plan_data.get("interactions")
    if isinstance(interactions, list):
        for interaction in interactions:
            if isinstance(interaction, dict) and interaction.get("page_id") == page_id:
                requirements.append(
                    f"交互意图：{interaction.get('trigger_component_id')} 通过 {interaction.get('trigger')} "
                    f"触发 {interaction.get('action')}；该行为不得转化成额外可见控件。"
                )
    for item in missing_assets:
        fallback = item.get("fallback") or "保持结构性占位，不虚构素材"
        requirements.append(
            f"缺失素材 {item.get('missing_asset_id')}：{item.get('description')}；采用保守方案：{fallback}"
        )
    return unique_strings(requirements)


def build_prompt(
    *,
    project_context: dict[str, Any],
    visual_direction: dict[str, Any],
    page: dict[str, Any],
    reference_assets: list[dict[str, Any]],
    hierarchy_lines: list[str],
    layout_lines: list[str],
    composition_requirements: list[str],
    preserve_requirements: list[str],
    allowed_changes: list[str],
    avoid: list[str],
    assumptions: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> str:
    resolution = project_context.get("target_resolution", {})
    orientation = project_context.get("orientation", "unspecified")
    width = resolution.get("width", "?") if isinstance(resolution, dict) else "?"
    height = resolution.get("height", "?") if isinstance(resolution, dict) else "?"
    game_description = project_context.get("game_description", {})
    genre = game_description.get("genre", "未指定") if isinstance(game_description, dict) else "未指定"

    lines = [
        f"为游戏《{project_context.get('project_name', '未命名项目')}》生成 {page.get('page_type')} 页面概念预览。",
        f"页面用途：{page.get('purpose')}。游戏类型：{genre}。",
        f"画面方向为 {orientation}，目标分辨率为 {width} x {height}。",
        f"整体视觉方向：{visual_direction.get('summary', '遵循现有页面计划')}。",
        "",
        "参考素材：",
    ]

    if reference_assets:
        for reference in reference_assets:
            lines.append(
                f"- 参考图 {reference['order']}：素材 {reference['asset_id']}，用于组件 "
                f"{reference['component_id']}，用途为 {reference['usage']}，保留优先级为 "
                f"{reference['preservation_priority']}。"
            )
    else:
        lines.append("- 当前页面没有参考素材，只能依据结构化页面计划生成纯文本概念预览。")

    lines.extend(["", "主要组件层级："])
    lines.extend(f"- {line}" for line in hierarchy_lines)
    lines.extend(["", "关键布局："])
    lines.extend(f"- {line}" for line in layout_lines)
    lines.extend(["", "构图要求："])
    lines.extend(f"- {item}" for item in composition_requirements)
    lines.extend(["", "必须保留："])
    lines.extend(f"- {item}" for item in preserve_requirements)
    lines.extend(["", "允许补充："])
    lines.extend(f"- {item}" for item in allowed_changes)
    lines.extend(["", "禁止事项："])
    lines.extend(f"- {item}" for item in avoid)

    if assumptions:
        lines.extend(["", "规划假设："])
        lines.extend(
            f"- {item.get('statement')} 影响：{item.get('impact')}"
            for item in assumptions
        )
    if warnings:
        lines.extend(["", "风险提示："])
        lines.extend(f"- {item.get('message')}" for item in warnings)

    lines.extend(
        [
            "",
            "该结果是用于评审构图、层级和风格方向的概念预览，并非像素级工程实现截图。",
        ]
    )
    return "\n".join(lines)


def unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def deduplicate_issues(values: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        key = (value["path"], value["message"], value["code"])
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def validate_generated_request(request: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    missing = sorted(PREVIEW_REQUIRED_FIELDS - set(request))
    for field in missing:
        errors.append(issue(f"$.{field}", "Missing generated field", "INTERNAL_OUTPUT_ERROR"))
    references = request.get("reference_assets")
    prompt = request.get("prompt")
    if isinstance(references, list) and isinstance(prompt, str):
        expected_orders = list(range(1, len(references) + 1))
        observed_orders = [item.get("order") for item in references if isinstance(item, dict)]
        if observed_orders != expected_orders:
            errors.append(
                issue("$.reference_assets", "Reference order must be consecutive", "INVALID_REFERENCE_ORDER")
            )
        for order in expected_orders:
            if f"参考图 {order}" not in prompt:
                errors.append(
                    issue(
                        "$.prompt",
                        f"Missing prompt label for reference image {order}",
                        "REFERENCE_PROMPT_MISMATCH",
                    )
                )
    return errors


def build_preview_request(
    input_data: dict[str, Any], plan_data: dict[str, Any], page_id: str
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    errors = validate_top_level(input_data, plan_data)
    if errors:
        return None, errors

    asset_index = build_input_asset_index(input_data, errors)
    page = select_page(plan_data, page_id, errors)
    components, component_index = build_component_index(plan_data, page_id, errors)
    if page is None:
        return None, deduplicate_issues(errors)

    usages = validate_page_references(
        plan_data, page, component_index, asset_index, errors
    )
    if errors:
        return None, deduplicate_issues(errors)

    reference_assets = build_reference_assets(usages, asset_index)
    hierarchy_lines = build_hierarchy_lines(components, component_index)
    layout_lines = build_layout_lines(plan_data, page_id, component_index)

    relevant_ids = {page_id}
    relevant_ids.update(component_index)
    relevant_ids.update(
        usage.get("usage_id") for usage in usages if isinstance(usage.get("usage_id"), str)
    )
    relevant_ids.update(
        usage.get("asset_id") for usage in usages if isinstance(usage.get("asset_id"), str)
    )
    missing_assets = related_missing_assets(plan_data, relevant_ids)
    relevant_ids.update(
        item.get("missing_asset_id")
        for item in missing_assets
        if isinstance(item.get("missing_asset_id"), str)
    )
    plan_warnings = related_plan_warnings(plan_data, relevant_ids)
    request_warnings = build_request_warnings(plan_warnings, page_id, reference_assets)

    assumptions = [
        copy.deepcopy(item)
        for item in plan_data.get("assumptions", [])
        if isinstance(item, dict)
    ]
    preserve_requirements = build_preserve_requirements(reference_assets, usages)
    composition_requirements = build_composition_requirements(
        page,
        hierarchy_lines,
        layout_lines,
        plan_data,
        page_id,
        missing_assets,
    )
    visual_direction = plan_data["visual_direction"]
    allowed_changes = unique_strings(
        [
            f"可以补充与既定视觉方向一致的光影、氛围和空间层次：{visual_direction.get('summary')}",
            "可以在不改变组件数量、功能和层级的前提下优化细节表现与视觉统一性。",
        ]
    )
    avoid = [
        "不得新增计划中不存在的页面、按钮、复杂菜单、功能或交互。",
        "不得把点击、跳转或加载行为转化为静态画面中的额外控件。",
        "不得改变参考素材的身份、核心轮廓或计划用途。",
        "不得把概念预览伪装成像素级工程实现截图。",
    ]
    project_context = plan_data["project_context"]
    prompt = build_prompt(
        project_context=project_context,
        visual_direction=visual_direction,
        page=page,
        reference_assets=reference_assets,
        hierarchy_lines=hierarchy_lines,
        layout_lines=layout_lines,
        composition_requirements=composition_requirements,
        preserve_requirements=preserve_requirements,
        allowed_changes=allowed_changes,
        avoid=avoid,
        assumptions=assumptions,
        warnings=request_warnings,
    )

    resolution = project_context.get("target_resolution", {})
    request = {
        "schema_version": PREVIEW_SCHEMA_VERSION,
        "generation_intent": {
            "kind": "page_concept_preview",
            "fidelity": "concept",
            "description": f"Generate a concept preview for page {page_id} from the approved UI compose plan.",
        },
        "source": {
            "page_id": page_id,
            "input_schema_version": input_data["schema_version"],
            "plan_schema_version": plan_data["schema_version"],
            "project_name": project_context.get("project_name"),
        },
        "prompt": prompt,
        "reference_assets": reference_assets,
        "composition_requirements": composition_requirements,
        "preserve_requirements": preserve_requirements,
        "allowed_changes": allowed_changes,
        "avoid": avoid,
        "output_spec": {
            "orientation": project_context.get("orientation"),
            "target_width": resolution.get("width") if isinstance(resolution, dict) else None,
            "target_height": resolution.get("height") if isinstance(resolution, dict) else None,
        },
        "assumptions": assumptions,
        "warnings": request_warnings,
    }
    errors.extend(validate_generated_request(request))
    return (request if not errors else None), deduplicate_issues(errors)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a provider-neutral preview request for one planned UI page."
    )
    parser.add_argument("--input", required=True, help="Path to ui-compose-input JSON")
    parser.add_argument("--plan", required=True, help="Path to ui-compose-plan JSON")
    parser.add_argument("--page", required=True, help="Target page_id")
    parser.add_argument("--output", required=True, help="Destination preview-request JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    plan_path = Path(args.plan)
    output_path = Path(args.output)

    errors: list[dict[str, str]] = []
    try:
        output_resolved = output_path.resolve(strict=False)
        if output_resolved == input_path.resolve(strict=False):
            errors.append(issue("$.output", "Output path must not overwrite input", "OUTPUT_ALIASES_INPUT"))
        if output_resolved == plan_path.resolve(strict=False):
            errors.append(issue("$.output", "Output path must not overwrite plan", "OUTPUT_ALIASES_PLAN"))
    except OSError as exc:
        errors.append(issue("$.output", f"Unable to resolve output path: {exc}", "OUTPUT_PATH_ERROR"))

    input_data, input_errors = load_json(input_path, "input")
    plan_data, plan_errors = load_json(plan_path, "plan")
    errors.extend(input_errors)
    errors.extend(plan_errors)
    if errors:
        emit_error(deduplicate_issues(errors))
        return 2

    request, build_errors = build_preview_request(input_data, plan_data, args.page)
    if build_errors or request is None:
        emit_error(build_errors)
        return 2

    if not output_path.parent.exists() or not output_path.parent.is_dir():
        emit_error(
            [
                issue(
                    "$.output",
                    f"Output directory does not exist: {output_path.parent}",
                    "OUTPUT_DIRECTORY_NOT_FOUND",
                )
            ]
        )
        return 2

    try:
        output_path.write_text(
            json.dumps(request, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        emit_error([issue("$.output", f"Unable to write output: {exc}", "OUTPUT_WRITE_ERROR")])
        return 2

    print(
        json.dumps(
            {
                "status": "success",
                "output": str(output_path),
                "page_id": args.page,
                "reference_asset_count": len(request["reference_assets"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
