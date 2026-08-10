#!/usr/bin/env python3
"""Validate a Composer v2 ui-compose-plan JSON document."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from validate_input import LimitedLocalValidator, issue, json_path, load_json


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "ui-compose-plan.schema.json"


def schema_errors(data: Any, schema: dict[str, Any]) -> tuple[list[dict[str, str]], str, bool, str | None]:
    try:
        from jsonschema import Draft202012Validator
    except ModuleNotFoundError:
        notice = (
            "The jsonschema dependency is unavailable. Validation used the bundled local "
            "keyword-limited validator; it is not a general Draft 2020-12 implementation."
        )
        return LimitedLocalValidator(schema, {}).validate(data), "limited_local", False, notice

    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        errors = [
            issue(json_path(error.absolute_path), error.message, "SCHEMA_VALIDATION_ERROR")
            for error in sorted(validator.iter_errors(data), key=lambda item: json_path(item.absolute_path))
        ]
        return errors, "jsonschema_draft_2020_12", True, None
    except Exception as exc:  # pragma: no cover - package-version dependent
        return [issue("$", f"Unable to initialize output validation: {exc}", "VALIDATOR_SETUP_ERROR")], "jsonschema_draft_2020_12", False, None


def duplicate_errors(items: Any, field: str, path: str) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    values = [item.get(field) for item in items if isinstance(item, dict) and isinstance(item.get(field), str)]
    duplicates = sorted({value for value in values if values.count(value) > 1})
    return [issue(path, f"Duplicate {field}: {value}", "DUPLICATE_IDENTIFIER") for value in duplicates]


def semantic_errors(data: Any) -> list[dict[str, str]]:
    if not isinstance(data, dict):
        return []
    errors: list[dict[str, str]] = []
    pages = data.get("pages", [])
    components = data.get("component_tree", [])
    layouts = data.get("layout_rules", [])
    interactions = data.get("interactions", [])
    navigation = data.get("navigation", [])

    for items, field, path in (
        (pages, "page_id", "$.pages"),
        (components, "component_id", "$.component_tree"),
        (layouts, "rule_id", "$.layout_rules"),
        (interactions, "interaction_id", "$.interactions"),
        (navigation, "navigation_id", "$.navigation"),
    ):
        errors.extend(duplicate_errors(items, field, path))

    page_map = {item.get("page_id"): item for item in pages if isinstance(item, dict)}
    component_map = {item.get("component_id"): item for item in components if isinstance(item, dict)}
    interaction_map = {item.get("interaction_id"): item for item in interactions if isinstance(item, dict)}
    navigation_map = {item.get("navigation_id"): item for item in navigation if isinstance(item, dict)}

    scope = data.get("project_context", {}).get("page_scope", []) if isinstance(data.get("project_context"), dict) else []
    if isinstance(scope, list) and set(scope) != set(page_map):
        errors.append(issue("$.project_context.page_scope", "page_scope must match pages[].page_id", "PAGE_SCOPE_MISMATCH"))

    for index, page in enumerate(pages if isinstance(pages, list) else []):
        if not isinstance(page, dict):
            continue
        root_id = page.get("root_component_id")
        root = component_map.get(root_id)
        if root is None:
            errors.append(issue(f"$.pages[{index}].root_component_id", "Unknown root component", "UNKNOWN_COMPONENT"))
        elif root.get("parent_id") is not None or root.get("page_id") != page.get("page_id"):
            errors.append(issue(f"$.pages[{index}].root_component_id", "Page root must have null parent_id and matching page_id", "INVALID_PAGE_ROOT"))

    for index, component in enumerate(components if isinstance(components, list) else []):
        if not isinstance(component, dict):
            continue
        page_id = component.get("page_id")
        if page_id not in page_map:
            errors.append(issue(f"$.component_tree[{index}].page_id", "Unknown page_id", "UNKNOWN_PAGE"))
        parent_id = component.get("parent_id")
        if parent_id is not None:
            parent = component_map.get(parent_id)
            if parent is None:
                errors.append(issue(f"$.component_tree[{index}].parent_id", "Unknown parent component", "UNKNOWN_COMPONENT"))
            elif parent.get("page_id") != page_id:
                errors.append(issue(f"$.component_tree[{index}].parent_id", "Parent component belongs to another page", "CROSS_PAGE_PARENT"))

    for index, rule in enumerate(layouts if isinstance(layouts, list) else []):
        if not isinstance(rule, dict):
            continue
        component = component_map.get(rule.get("component_id"))
        if component is None:
            errors.append(issue(f"$.layout_rules[{index}].component_id", "Unknown component_id", "UNKNOWN_COMPONENT"))
        elif component.get("page_id") != rule.get("page_id"):
            errors.append(issue(f"$.layout_rules[{index}].page_id", "Layout page_id does not match component page", "PAGE_COMPONENT_MISMATCH"))
        for rel_index, relation in enumerate(rule.get("relationships", [])):
            if isinstance(relation, dict) and relation.get("target_component_id") not in component_map:
                errors.append(issue(f"$.layout_rules[{index}].relationships[{rel_index}].target_component_id", "Unknown target component", "UNKNOWN_COMPONENT"))

    for index, interaction in enumerate(interactions if isinstance(interactions, list) else []):
        if not isinstance(interaction, dict):
            continue
        component = component_map.get(interaction.get("trigger_component_id"))
        if component is None:
            errors.append(issue(f"$.interactions[{index}].trigger_component_id", "Unknown trigger component", "UNKNOWN_COMPONENT"))
        elif component.get("page_id") != interaction.get("page_id"):
            errors.append(issue(f"$.interactions[{index}].page_id", "Interaction page does not match trigger component", "PAGE_COMPONENT_MISMATCH"))
        nav_id = interaction.get("navigation_id")
        if nav_id is not None and nav_id not in navigation_map:
            errors.append(issue(f"$.interactions[{index}].navigation_id", "Unknown navigation_id", "UNKNOWN_NAVIGATION"))

    for index, nav in enumerate(navigation if isinstance(navigation, list) else []):
        if not isinstance(nav, dict):
            continue
        if nav.get("from_page_id") not in page_map:
            errors.append(issue(f"$.navigation[{index}].from_page_id", "Unknown source page", "UNKNOWN_PAGE"))
        if nav.get("to_page_id") not in page_map:
            errors.append(issue(f"$.navigation[{index}].to_page_id", "Unknown target page", "UNKNOWN_PAGE"))
        if nav.get("trigger_interaction_id") not in interaction_map:
            errors.append(issue(f"$.navigation[{index}].trigger_interaction_id", "Unknown trigger interaction", "UNKNOWN_INTERACTION"))

    reference = data.get("reference_application", {})
    style_decisions = reference.get("style", []) if isinstance(reference, dict) else []
    known_traits = {item.get("trait_id") for item in style_decisions if isinstance(item, dict)}
    visual = data.get("visual_direction", {})
    for index, directive in enumerate(visual.get("directives", []) if isinstance(visual, dict) else []):
        if not isinstance(directive, dict):
            continue
        sources = directive.get("source_trait_ids", [])
        if not directive.get("user_override") and not sources:
            errors.append(issue(f"$.visual_direction.directives[{index}].source_trait_ids", "Non-user directive must cite at least one B2 trait", "MISSING_STYLE_TRACE"))
        for trait_id in sources if isinstance(sources, list) else []:
            if trait_id not in known_traits:
                errors.append(issue(f"$.visual_direction.directives[{index}].source_trait_ids", f"Unknown style trait: {trait_id}", "UNKNOWN_STYLE_TRAIT"))

    constraints = data.get("generation_constraints", {})
    if isinstance(constraints, dict):
        for index, exact in enumerate(constraints.get("exact_counts", [])):
            if not isinstance(exact, dict):
                continue
            component = component_map.get(exact.get("component_id"))
            if component is None:
                errors.append(issue(f"$.generation_constraints.exact_counts[{index}].component_id", "Unknown component_id", "UNKNOWN_COMPONENT"))
                continue
            actual = component.get("repeat", {}).get("count", 1) if isinstance(component.get("repeat"), dict) else 1
            if exact.get("count") != actual:
                errors.append(issue(f"$.generation_constraints.exact_counts[{index}].count", f"Count does not match component repeat count {actual}", "COUNT_MISMATCH"))
        for index, component_id in enumerate(constraints.get("focal_hierarchy", [])):
            if component_id not in component_map:
                errors.append(issue(f"$.generation_constraints.focal_hierarchy[{index}]", "Unknown focal component", "UNKNOWN_COMPONENT"))

    return errors


def deduplicate(items: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        key = (item["path"], item["message"], item["code"])
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def validate_document(data: Any) -> tuple[list[dict[str, str]], str, bool, str | None]:
    schema = load_json(SCHEMA_PATH)
    contract_errors, mode, full, notice = schema_errors(data, schema)
    return deduplicate([*contract_errors, *semantic_errors(data)]), mode, full, notice


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"status": "error", "errors": [issue("$", "Usage: python scripts/validate_plan.py <plan.json>", "USAGE_ERROR")]}, indent=2))
        return 1
    path = Path(sys.argv[1])
    try:
        data = load_json(path)
        errors, mode, full, notice = validate_document(data)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "errors": [issue("$", str(exc), "JSON_READ_ERROR")]}, ensure_ascii=False, indent=2))
        return 2
    validator: dict[str, Any] = {"mode": mode, "full_draft_2020_12": full}
    if notice:
        validator["notice"] = notice
    result = {
        "status": "valid" if not errors else "error",
        "validator": validator,
        "errors": errors,
        "summary": {
            "schema_version": data.get("schema_version") if isinstance(data, dict) else None,
            "page_count": len(data.get("pages", [])) if isinstance(data, dict) and isinstance(data.get("pages"), list) else 0,
            "component_count": len(data.get("component_tree", [])) if isinstance(data, dict) and isinstance(data.get("component_tree"), list) else 0,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
