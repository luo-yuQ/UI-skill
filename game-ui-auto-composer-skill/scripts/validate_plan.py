#!/usr/bin/env python3
"""Validate a Composer v2.1.1 candidate plan deterministically."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from evidence_registry import build_evidence_registry, collect_a_ids, collect_b_traits
from validate_input import LimitedLocalValidator, issue, json_path, load_json


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "ui-compose-plan.schema.json"
DRIFT_TERMS = ("commission", "quest", "mission", "accept commission", "accept quest", "accept mission")


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
    except Exception as exc:  # pragma: no cover
        return [issue("$", f"Unable to initialize output validation: {exc}", "VALIDATOR_SETUP_ERROR")], "jsonschema_draft_2020_12", False, None


def duplicate_errors(items: Any, field: str, path: str) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    values = [item.get(field) for item in items if isinstance(item, dict) and isinstance(item.get(field), str)]
    duplicates = sorted({value for value in values if values.count(value) > 1})
    return [issue(path, f"Duplicate {field}: {value}", "DUPLICATE_IDENTIFIER") for value in duplicates]


def base_semantic_errors(data: dict[str, Any]) -> list[dict[str, str]]:
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

    scope = data.get("project_context", {}).get("page_scope", [])
    if isinstance(scope, list) and set(scope) != set(page_map):
        errors.append(issue("$.project_context.page_scope", "page_scope must match pages[].page_id", "PAGE_SCOPE_MISMATCH"))

    for index, page in enumerate(pages if isinstance(pages, list) else []):
        root = component_map.get(page.get("root_component_id")) if isinstance(page, dict) else None
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
        repeat = component.get("repeat")
        if isinstance(repeat, dict):
            columns, rows = repeat.get("columns"), repeat.get("rows")
            if repeat.get("arrangement") == "grid":
                if not isinstance(columns, int) or not isinstance(rows, int):
                    errors.append(issue(f"$.component_tree[{index}].repeat", "Grid repeat requires integer columns and rows", "GRID_DIMENSIONS_MISSING"))
                elif columns * rows != repeat.get("count"):
                    errors.append(issue(f"$.component_tree[{index}].repeat.count", "Grid count must equal columns * rows", "GRID_COUNT_MISMATCH"))
            elif columns is not None or rows is not None:
                errors.append(issue(f"$.component_tree[{index}].repeat", "Non-grid repeat must use null columns and rows", "GRID_DIMENSIONS_UNEXPECTED"))

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
        if interaction.get("navigation_id") is not None and interaction.get("navigation_id") not in navigation_map:
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

    constraints = data.get("generation_constraints", {})
    if isinstance(constraints, dict):
        for index, zone in enumerate(constraints.get("key_content_zones", [])):
            if isinstance(zone, dict) and zone.get("component_id") not in component_map:
                errors.append(issue(f"$.generation_constraints.key_content_zones[{index}].component_id", "Unknown component_id", "UNKNOWN_COMPONENT"))
        for index, component_id in enumerate(constraints.get("focal_hierarchy", [])):
            if component_id not in component_map:
                errors.append(issue(f"$.generation_constraints.focal_hierarchy[{index}]", "Unknown focal component", "UNKNOWN_COMPONENT"))
    return errors


def requirement_errors(data: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    context = data.get("project_context", {})
    requirement = context.get("user_requirement", "") if isinstance(context, dict) else ""
    hard = context.get("hard_requirements", {}) if isinstance(context, dict) else {}
    pages = data.get("pages", [])
    component_map = {item.get("component_id"): item for item in data.get("component_tree", []) if isinstance(item, dict)}
    layout_map = {item.get("component_id"): item for item in data.get("layout_rules", []) if isinstance(item, dict)}
    interactions = data.get("interactions", [])
    constraints = data.get("generation_constraints", {})
    exact_map = {item.get("component_id"): item for item in constraints.get("exact_counts", []) if isinstance(item, dict)}
    grid_map = {item.get("component_id"): item for item in constraints.get("grid_specs", []) if isinstance(item, dict)}

    evidence_items: list[tuple[str, Any]] = []
    if isinstance(hard, dict):
        evidence_items.append(("$.project_context.hard_requirements.page_semantic.evidence", hard.get("page_semantic", {}).get("evidence")))
        for collection in ("explicit_counts", "grid_requirements", "required_elements"):
            for index, item in enumerate(hard.get(collection, [])):
                if isinstance(item, dict):
                    evidence_items.append((f"$.project_context.hard_requirements.{collection}[{index}].evidence", item.get("evidence")))
    for path, evidence in evidence_items:
        if not isinstance(evidence, str) or evidence not in requirement:
            errors.append(issue(path, "Evidence must be an exact substring of user_requirement", "REQUIREMENT_EVIDENCE_MISMATCH"))

    semantic = hard.get("page_semantic", {}).get("value") if isinstance(hard, dict) else None
    for index, page in enumerate(pages if isinstance(pages, list) else []):
        if isinstance(page, dict) and page.get("page_type") != semantic:
            errors.append(issue(f"$.pages[{index}].page_type", f"Page semantic must remain {semantic!r}", "SEMANTIC_DRIFT"))

    for index, fact in enumerate(hard.get("explicit_counts", []) if isinstance(hard, dict) else []):
        if not isinstance(fact, dict):
            continue
        component_id, count = fact.get("target_component_id"), fact.get("count")
        component = component_map.get(component_id)
        if component is None:
            errors.append(issue(f"$.project_context.hard_requirements.explicit_counts[{index}].target_component_id", "Unknown component", "UNKNOWN_COMPONENT"))
            continue
        actual = component.get("repeat", {}).get("count", 1) if isinstance(component.get("repeat"), dict) else 1
        if count != actual:
            errors.append(issue(f"$.component_tree[{data.get('component_tree', []).index(component)}].repeat.count", f"Hard requirement count is {count}, got {actual}", "REQUIREMENT_COUNT_MISMATCH"))
        if exact_map.get(component_id, {}).get("count") != count:
            errors.append(issue("$.generation_constraints.exact_counts", f"Missing or inconsistent exact count for {component_id}", "CROSS_SECTION_COUNT_MISMATCH"))

    for index, fact in enumerate(hard.get("grid_requirements", []) if isinstance(hard, dict) else []):
        if not isinstance(fact, dict):
            continue
        component_id = fact.get("target_component_id")
        component = component_map.get(component_id)
        repeat = component.get("repeat", {}) if isinstance(component, dict) else {}
        expected = (fact.get("columns"), fact.get("rows"))
        if (repeat.get("columns"), repeat.get("rows")) != expected:
            errors.append(issue(f"$.project_context.hard_requirements.grid_requirements[{index}]", f"Component grid does not match {expected[0]}x{expected[1]}", "REQUIREMENT_GRID_MISMATCH"))
        spec = grid_map.get(component_id, {})
        if (spec.get("columns"), spec.get("rows")) != expected:
            errors.append(issue("$.generation_constraints.grid_specs", f"Missing or inconsistent grid spec for {component_id}", "CROSS_SECTION_GRID_MISMATCH"))

    for index, fact in enumerate(hard.get("required_elements", []) if isinstance(hard, dict) else []):
        if not isinstance(fact, dict):
            continue
        component_id = fact.get("target_component_id")
        if component_id not in component_map:
            errors.append(issue(f"$.project_context.hard_requirements.required_elements[{index}].target_component_id", "Unknown component", "UNKNOWN_COMPONENT"))
            continue
        position = fact.get("position")
        if position:
            anchor = layout_map.get(component_id, {}).get("anchor", "")
            if position not in anchor:
                errors.append(issue(f"$.layout_rules", f"{component_id} must be positioned at {position}", "REQUIREMENT_POSITION_MISMATCH"))
        semantic_name = fact.get("semantic", "")
        if "refresh" in semantic_name and not any(
            isinstance(item, dict)
            and item.get("trigger_component_id") == component_id
            and "refresh" in item.get("action", "")
            for item in interactions
        ):
            errors.append(issue("$.interactions", f"{component_id} lacks its required refresh interaction", "REQUIREMENT_INTERACTION_MISMATCH"))

    for value in hard.get("must_include", []) if isinstance(hard, dict) else []:
        if value not in constraints.get("must_include", []):
            errors.append(issue("$.generation_constraints.must_include", f"Missing derived hard requirement: {value}", "CROSS_SECTION_REQUIREMENT_MISMATCH"))
    for value in hard.get("must_not_include", []) if isinstance(hard, dict) else []:
        if value not in constraints.get("must_not_include", []):
            errors.append(issue("$.generation_constraints.must_not_include", f"Missing derived prohibition: {value}", "CROSS_SECTION_REQUIREMENT_MISMATCH"))

    target = json.dumps({
        "design_summary": data.get("design_summary"),
        "pages": data.get("pages"),
        "component_tree": data.get("component_tree"),
        "interactions": data.get("interactions"),
        "generation_constraints": data.get("generation_constraints"),
    }, ensure_ascii=False).lower()
    user_lower = requirement.lower()
    for term in DRIFT_TERMS:
        pattern = rf"(?<![a-z]){re.escape(term)}(?![a-z])"
        if re.search(pattern, user_lower) is None and re.search(pattern, target) is not None:
            errors.append(issue("$", f"Target content introduced absent business semantic: {term}", "SEMANTIC_DRIFT"))
    return errors


def traceability_errors(data: dict[str, Any], input_data: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    layout = input_data.get("layout_reference_analysis", {})
    style = input_data.get("style_profile", {})
    reference = data.get("reference_application", {})
    requirement = input_data.get("request", {}).get("user_requirement", "")
    components = {item.get("component_id") for item in data.get("component_tree", []) if isinstance(item, dict)}
    if reference.get("layout_analysis_id") != layout.get("analysis_id"):
        errors.append(issue("$.reference_application.layout_analysis_id", "Does not match input A analysis_id", "LAYOUT_ANALYSIS_ID_MISMATCH"))
    if reference.get("style_profile_id") != style.get("profile_id"):
        errors.append(issue("$.reference_application.style_profile_id", "Does not match input B profile_id", "STYLE_PROFILE_ID_MISMATCH"))

    try:
        registry = build_evidence_registry(layout, style)
    except ValueError as exc:
        return [issue("$", f"Unable to build evidence registry: {exc}", "EVIDENCE_REGISTRY_ERROR")]

    for index, decision in enumerate(reference.get("layout", [])):
        if not isinstance(decision, dict):
            continue
        origin = decision.get("origin")
        source_kind = decision.get("source_kind")
        source_ids = decision.get("source_ids", [])
        if origin == "layout_reference":
            if not source_ids:
                errors.append(
                    issue(
                        f"$.reference_application.layout[{index}].source_ids",
                        "origin=layout_reference requires at least one A source id",
                        "MISSING_A_SOURCE_IDS",
                    )
                )
            for source_index, source_id in enumerate(source_ids):
                if not registry.a_matches(source_id, source_kind):
                    errors.append(
                        issue(
                            f"$.reference_application.layout[{index}].source_ids[{source_index}]",
                            f"Unknown A source id: {source_id!r}; source type: {source_kind!r}",
                            "UNKNOWN_A_SOURCE_ID",
                        )
                    )
        else:
            if source_ids:
                errors.append(
                    issue(
                        f"$.reference_application.layout[{index}].source_ids",
                        f"origin={origin!r} must not cite A source ids",
                        "NON_REFERENCE_A_SOURCE_IDS",
                    )
                )
            if source_kind is not None:
                errors.append(
                    issue(
                        f"$.reference_application.layout[{index}].source_kind",
                        f"origin={origin!r} requires source_kind=null",
                        "NON_REFERENCE_A_SOURCE_KIND",
                    )
                )

    decisions: dict[str, dict[str, Any]] = {}
    for index, decision in enumerate(reference.get("style", [])):
        if not isinstance(decision, dict):
            continue
        origin = decision.get("origin")
        trait_id = decision.get("trait_id")
        if origin != "style_reference":
            if trait_id is not None:
                errors.append(
                    issue(
                        f"$.reference_application.style[{index}].trait_id",
                        f"origin={origin!r} must not cite a B trait id",
                        "NON_REFERENCE_B_TRAIT_ID",
                    )
                )
            continue
        if not isinstance(trait_id, str):
            errors.append(
                issue(
                    f"$.reference_application.style[{index}].trait_id",
                    "origin=style_reference requires one B trait id",
                    "MISSING_B_TRAIT_ID",
                )
            )
            continue
        actual = registry.b_match(trait_id)
        if actual is None:
            errors.append(
                issue(
                    f"$.reference_application.style[{index}].trait_id",
                    f"Unknown B trait id: {trait_id!r}; source type: 'style_reference'",
                    "UNKNOWN_B_TRAIT_ID",
                )
            )
            continue
        decisions[trait_id] = decision
        if decision.get("dimension") != actual.dimension:
            errors.append(
                issue(
                    f"$.reference_application.style[{index}].dimension",
                    f"B declares {trait_id!r} in dimension {actual.dimension!r}",
                    "B_TRAIT_DIMENSION_MISMATCH",
                )
            )
        if decision.get("classification") != actual.classification:
            errors.append(
                issue(
                    f"$.reference_application.style[{index}].classification",
                    f"B declares {trait_id!r} as {actual.classification!r}",
                    "B_TRAIT_CLASSIFICATION_MISMATCH",
                )
            )
        promoted = decision.get("promoted_by_user_requirement")
        promotion_evidence = decision.get("promotion_evidence")
        if promoted:
            if not isinstance(promotion_evidence, str) or promotion_evidence not in requirement:
                errors.append(issue(f"$.reference_application.style[{index}].promotion_evidence", "Promotion evidence must be an exact user_requirement substring", "INVALID_LOCAL_PROMOTION"))
        elif promotion_evidence is not None:
            errors.append(issue(f"$.reference_application.style[{index}].promotion_evidence", "Non-promoted traits must use null promotion_evidence", "INVALID_LOCAL_PROMOTION"))
        if actual.classification == "local" and decision.get("disposition") in ("adopted", "conditionally_adopted", "overridden_by_user"):
            scope = decision.get("target_scope", [])
            if not promoted and (len(scope) != 1 or scope[0] not in components):
                errors.append(issue(f"$.reference_application.style[{index}].target_scope", "Adopted local trait must stay on exactly one existing component unless explicitly promoted by the user", "LOCAL_TRAIT_SCOPE_VIOLATION"))

    for index, directive in enumerate(data.get("visual_direction", {}).get("directives", [])):
        if not isinstance(directive, dict):
            continue
        sources = directive.get("source_trait_ids", [])
        if not directive.get("user_override") and not sources:
            errors.append(issue(f"$.visual_direction.directives[{index}].source_trait_ids", "Non-user directive must cite at least one B trait", "MISSING_STYLE_TRACE"))
        for trait_id in sources:
            decision = decisions.get(trait_id)
            if decision is None:
                errors.append(issue(f"$.visual_direction.directives[{index}].source_trait_ids", f"Unknown style decision: {trait_id}", "UNKNOWN_STYLE_TRAIT"))
                continue
            if decision.get("disposition") in ("ignored", "rejected_due_to_conflict"):
                errors.append(issue(f"$.visual_direction.directives[{index}].source_trait_ids", f"Directive uses non-adopted trait: {trait_id}", "NON_ADOPTED_STYLE_TRAIT"))
            actual = registry.b_match(trait_id)
            if actual and actual.classification == "local" and not decision.get("promoted_by_user_requirement"):
                if not set(directive.get("target_scope", [])).issubset(set(decision.get("target_scope", []))):
                    errors.append(issue(f"$.visual_direction.directives[{index}].target_scope", f"Directive globalizes local trait: {trait_id}", "LOCAL_TRAIT_SCOPE_VIOLATION"))
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


def validate_document(data: Any, input_data: Any | None = None) -> tuple[list[dict[str, str]], str, bool, str | None]:
    schema = load_json(SCHEMA_PATH)
    contract_errors, mode, full, notice = schema_errors(data, schema)
    if not isinstance(data, dict):
        return deduplicate(contract_errors), mode, full, notice
    errors = [*contract_errors, *base_semantic_errors(data), *requirement_errors(data)]
    if isinstance(input_data, dict):
        errors.extend(traceability_errors(data, input_data))
        plan_requirement = data.get("project_context", {}).get("user_requirement")
        input_requirement = input_data.get("request", {}).get("user_requirement")
        if plan_requirement != input_requirement:
            errors.append(issue("$.project_context.user_requirement", "Plan must copy input request.user_requirement exactly", "USER_REQUIREMENT_MISMATCH"))
    return deduplicate(errors), mode, full, notice


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--input", type=Path, help="Composer input for strict A/B and requirement cross-validation")
    args = parser.parse_args()
    try:
        data = load_json(args.plan)
        input_data = load_json(args.input) if args.input else None
        errors, mode, full, notice = validate_document(data, input_data)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "errors": [issue("$", str(exc), "JSON_READ_ERROR")]}, ensure_ascii=False, indent=2))
        return 2
    validator: dict[str, Any] = {"mode": mode, "full_draft_2020_12": full}
    if notice:
        validator["notice"] = notice
    codes = {item["code"] for item in errors}
    result = {
        "status": "valid" if not errors else "error",
        "validator": validator,
        "errors": errors,
        "summary": {
            "schema_version": data.get("schema_version") if isinstance(data, dict) else None,
            "page_count": len(data.get("pages", [])) if isinstance(data, dict) and isinstance(data.get("pages"), list) else 0,
            "component_count": len(data.get("component_tree", [])) if isinstance(data, dict) and isinstance(data.get("component_tree"), list) else 0,
            "input_cross_validation": input_data is not None,
            "traceability_passed": input_data is not None and not codes.intersection({
                "EVIDENCE_REGISTRY_ERROR",
                "UNKNOWN_A_SOURCE_ID",
                "MISSING_A_SOURCE_IDS",
                "NON_REFERENCE_A_SOURCE_IDS",
                "NON_REFERENCE_A_SOURCE_KIND",
                "UNKNOWN_B_TRAIT_ID",
                "MISSING_B_TRAIT_ID",
                "NON_REFERENCE_B_TRAIT_ID",
                "B_TRAIT_DIMENSION_MISMATCH",
                "B_TRAIT_CLASSIFICATION_MISMATCH",
            }),
            "requirement_preservation_passed": not any(code.startswith("REQUIREMENT_") or code in {"SEMANTIC_DRIFT", "USER_REQUIREMENT_MISMATCH"} for code in codes),
            "cross_section_consistency_passed": not any(code.startswith("CROSS_SECTION_") or code == "GRID_COUNT_MISMATCH" for code in codes),
            "local_scope_passed": "LOCAL_TRAIT_SCOPE_VIOLATION" not in codes,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
