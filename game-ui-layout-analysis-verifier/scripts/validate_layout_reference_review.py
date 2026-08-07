#!/usr/bin/env python3
"""Validate an A2 review and optional draft/final consistency."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


A2_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = A2_ROOT.parent
REVIEW_SCHEMA_PATH = A2_ROOT / "schemas" / "layout-reference-review.schema.json"
A1_VALIDATOR_PATH = (
    REPOSITORY_ROOT
    / "game-ui-layout-reference-analyzer"
    / "scripts"
    / "validate_layout_reference_analysis.py"
)

FORBIDDEN_KEYS = {
    "laya_new_ui",
    "fairygui",
    "unity",
    "cocos",
    "node_type",
    "recommended_node_type",
    "size_grid",
    "size_grid_candidate",
    "nine_slice",
    "api_key",
    "authorization",
    "response_format",
    "image_prompt",
    "generation_prompt",
}
REVIEW_ONLY_KEYS = {
    "review_id",
    "source_analysis",
    "review_summary",
    "findings",
    "category_assessments",
    "unresolved_findings",
    "finalization",
}
CHANGE_ACTIONS = {"modified", "added", "removed", "downgraded_to_uncertain"}
OLD_ENTITY_ACTIONS = {"confirmed", "modified", "removed"}
ASSESSMENT_CATEGORIES = {
    "page_understanding",
    "region_completeness",
    "region_structure",
    "component_groups",
    "repeat_counts",
    "visual_hierarchy",
    "evidence_discipline",
    "brand_isolation",
    "confidence_calibration",
}
APPROVAL_CHECKS = {
    "independent_baseline_completed",
    "page_consistency_checked",
    "major_regions_checked",
    "major_component_groups_checked",
    "primary_action_checked",
    "visual_and_interaction_foci_checked",
    "repeat_counts_checked",
    "secondary_actions_checked",
    "evidence_levels_checked",
    "confidence_recalibrated",
    "brand_isolation_checked",
    "user_focus_covered",
    "metadata_consistency_checked",
    "auditable_records_present",
}

_A1_VALIDATOR: ModuleType | None = None


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_a1_validator() -> ModuleType:
    global _A1_VALIDATOR
    if _A1_VALIDATOR is not None:
        return _A1_VALIDATOR
    spec = importlib.util.spec_from_file_location("a1_layout_validator", A1_VALIDATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load A1 validator from {A1_VALIDATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _A1_VALIDATOR = module
    return module


def find_forbidden_keys(value: Any, path: str = "$") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key.casefold() in FORBIDDEN_KEYS:
                errors.append(f"{child_path}: forbidden field name")
            errors.extend(find_forbidden_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(find_forbidden_keys(child, f"{path}[{index}]"))
    return errors


def find_review_only_keys(value: Any, path: str = "$") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in REVIEW_ONLY_KEYS:
                errors.append(f"{child_path}: review-only field is not allowed in final")
            errors.extend(find_review_only_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(find_review_only_keys(child, f"{path}[{index}]"))
    return errors


def collect_unique_values(
    items: Any,
    field: str,
    path: str,
    errors: list[str],
) -> set[str]:
    values: set[str] = set()
    if not isinstance(items, list):
        return values
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        value = item.get(field)
        if not isinstance(value, str):
            continue
        if value in values:
            errors.append(f"{path}[{index}].{field}: duplicate value {value!r}")
        values.add(value)
    return values


def collect_analysis_entities(data: Any) -> set[str]:
    if not isinstance(data, dict):
        return set()
    entities = {"page", "visual_hierarchy"}
    collections = (
        ("regions", "region_id"),
        ("region_relationships", "relationship_id"),
        ("component_groups", "group_id"),
        ("layout_rules", "rule_id"),
        ("uncertainties", "uncertainty_id"),
    )
    for collection, field in collections:
        items = data.get(collection)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get(field), str):
                entities.add(item[field])
    return entities


def expected_assessment_keys(data: Any) -> set[tuple[str, str]]:
    """Return every A1 object that requires an auditable A2 assessment."""

    if not isinstance(data, dict):
        return set()
    expected = {
        ("page", "page"),
        ("visual_hierarchy", "visual_hierarchy"),
        ("excluded_content", "excluded_content"),
        ("overall_confidence", "overall_confidence"),
    }
    collections = (
        ("regions", "region_id", "region"),
        ("region_relationships", "relationship_id", "region_relationship"),
        ("component_groups", "group_id", "component_group"),
        ("layout_rules", "rule_id", "layout_rule"),
        ("uncertainties", "uncertainty_id", "uncertainty"),
    )
    for collection, id_field, entity_type in collections:
        items = data.get(collection)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get(id_field), str):
                expected.add((entity_type, item[id_field]))
    return expected


def validate_review_schema(data: Any) -> list[str]:
    try:
        schema = load_json(REVIEW_SCHEMA_PATH)
        a1_validator = load_a1_validator()
    except (OSError, json.JSONDecodeError, RuntimeError) as exc:
        return [f"$: unable to initialize review schema validation: {exc}"]
    return a1_validator.validate_schema(data, schema)


def validate_review_semantics(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return []

    errors: list[str] = []
    findings = data.get("findings")
    unresolved = data.get("unresolved_findings")
    assessments = data.get("category_assessments")
    entity_assessments = data.get("entity_assessments")
    approval_evidence = data.get("approval_evidence")
    baseline = data.get("independent_baseline")
    comparison = data.get("comparison_summary")
    summary = data.get("review_summary")
    finalization = data.get("finalization")

    finding_ids = collect_unique_values(findings, "finding_id", "$.findings", errors)
    unresolved_ids = collect_unique_values(
        unresolved,
        "finding_id",
        "$.unresolved_findings",
        errors,
    )
    category_ids = collect_unique_values(
        assessments,
        "category",
        "$.category_assessments",
        errors,
    )
    if category_ids != ASSESSMENT_CATEGORIES:
        missing = sorted(ASSESSMENT_CATEGORIES - category_ids)
        extra = sorted(category_ids - ASSESSMENT_CATEGORIES)
        if missing:
            errors.append(f"$.category_assessments: missing categories {missing}")
        if extra:
            errors.append(f"$.category_assessments: unexpected categories {extra}")

    if not isinstance(baseline, dict):
        errors.append("$.independent_baseline: independent baseline is required")
    else:
        major_regions = baseline.get("major_region_summaries")
        if not isinstance(major_regions, list) or not major_regions:
            errors.append(
                "$.independent_baseline.major_region_summaries: must not be empty"
            )

    if not isinstance(comparison, dict):
        errors.append("$.comparison_summary: draft comparison is required")
    else:
        if "metadata_consistency" not in comparison:
            errors.append(
                "$.comparison_summary.metadata_consistency: comparison is required"
            )
        if "user_focus_coverage" not in comparison:
            errors.append(
                "$.comparison_summary.user_focus_coverage: comparison is required"
            )

    finding_list = findings if isinstance(findings, list) else []
    severity_counts = {
        severity: sum(
            1
            for finding in finding_list
            if isinstance(finding, dict) and finding.get("severity") == severity
        )
        for severity in ("critical", "major", "minor")
    }
    action_by_id = {
        finding.get("finding_id"): finding.get("correction_action")
        for finding in finding_list
        if isinstance(finding, dict) and isinstance(finding.get("finding_id"), str)
    }
    expected_applied = {
        finding_id
        for finding_id, action in action_by_id.items()
        if action in CHANGE_ACTIONS
    }
    expected_unresolved = {
        finding_id
        for finding_id, action in action_by_id.items()
        if action == "unresolved"
    }

    assessment_keys: set[tuple[str, str]] = set()
    entity_list = entity_assessments if isinstance(entity_assessments, list) else []
    for index, assessment in enumerate(entity_list):
        if not isinstance(assessment, dict):
            continue
        entity_type = assessment.get("entity_type")
        entity_id = assessment.get("entity_id")
        if isinstance(entity_type, str) and isinstance(entity_id, str):
            key = (entity_type, entity_id)
            if key in assessment_keys:
                errors.append(
                    f"$.entity_assessments[{index}]: duplicate entity assessment {key!r}"
                )
            assessment_keys.add(key)
        related = assessment.get("related_finding_ids")
        related_ids = set(related) if isinstance(related, list) else set()
        unknown = sorted(related_ids - finding_ids)
        if unknown:
            errors.append(
                f"$.entity_assessments[{index}].related_finding_ids: "
                f"unknown finding IDs {unknown}"
            )
        if assessment.get("status") != "confirmed" and not related_ids:
            errors.append(
                f"$.entity_assessments[{index}].related_finding_ids: "
                "non-confirmed assessment requires a finding"
            )

    approval_checks = collect_unique_values(
        approval_evidence,
        "check",
        "$.approval_evidence",
        errors,
    )

    if isinstance(summary, dict):
        if summary.get("issue_count") != len(finding_list):
            errors.append(
                "$.review_summary.issue_count: must equal the number of findings"
            )
        count_fields = {
            "critical": "critical_issue_count",
            "major": "major_issue_count",
            "minor": "minor_issue_count",
        }
        for severity, field in count_fields.items():
            if summary.get(field) != severity_counts[severity]:
                errors.append(
                    f"$.review_summary.{field}: must equal {severity_counts[severity]}"
                )
        if summary.get("changes_applied") is not bool(expected_applied):
            errors.append(
                "$.review_summary.changes_applied: inconsistent with correction actions"
            )
        if summary.get("verdict") == "approved" and (
            severity_counts["major"] or severity_counts["critical"]
        ):
            errors.append(
                "$.review_summary.verdict: approved cannot include major or critical findings"
            )
        if summary.get("verdict") == "approved":
            evidence_list = approval_evidence if isinstance(approval_evidence, list) else []
            if approval_checks != APPROVAL_CHECKS:
                missing = sorted(APPROVAL_CHECKS - approval_checks)
                errors.append(
                    "$.approval_evidence: approved review must cover every approval check"
                    + (f"; missing {missing}" if missing else "")
                )
            non_pass = [
                item.get("check")
                for item in evidence_list
                if isinstance(item, dict) and item.get("status") != "pass"
            ]
            if non_pass:
                errors.append(
                    f"$.approval_evidence: approved review has non-pass checks {non_pass}"
                )
            if expected_unresolved or unresolved_ids:
                errors.append(
                    "$.review_summary.verdict: approved review cannot have unresolved findings"
                )
            if expected_applied:
                errors.append(
                    "$.review_summary.verdict: approved review cannot require applied corrections"
                )
            non_confirmed = [
                item.get("entity_id")
                for item in entity_list
                if isinstance(item, dict) and item.get("status") != "confirmed"
            ]
            if non_confirmed:
                errors.append(
                    "$.entity_assessments: approved review requires confirmed assessments; "
                    f"non-confirmed entities {non_confirmed}"
                )
            non_pass_categories = [
                item.get("category")
                for item in (assessments if isinstance(assessments, list) else [])
                if isinstance(item, dict) and item.get("status") != "pass"
            ]
            if non_pass_categories:
                errors.append(
                    "$.category_assessments: approved review requires pass status; "
                    f"non-pass categories {non_pass_categories}"
                )
            unresolved_core = [
                item.get("entity_id")
                for item in entity_list
                if isinstance(item, dict)
                and item.get("status") == "unresolved"
                and item.get("entity_type")
                in {"page", "region", "visual_hierarchy", "overall_confidence"}
            ]
            if unresolved_core:
                errors.append(
                    "$.review_summary.verdict: approved review has unresolved core entities "
                    f"{unresolved_core}"
                )
            if isinstance(comparison, dict):
                non_matching_comparisons = [
                    field
                    for field, item in comparison.items()
                    if isinstance(item, dict)
                    and item.get("status") != "match"
                    and not (
                        field == "user_focus_coverage"
                        and item.get("status") == "not_applicable"
                    )
                ]
                if non_matching_comparisons:
                    errors.append(
                        "$.comparison_summary: approved review requires matching comparisons; "
                        f"non-matching fields {non_matching_comparisons}"
                    )
                metadata = comparison.get("metadata_consistency")
                metadata_status = metadata.get("status") if isinstance(metadata, dict) else None
                if metadata_status != "match":
                    errors.append(
                        "$.comparison_summary.metadata_consistency: "
                        "approved review requires verified matching metadata"
                    )
                focus = comparison.get("user_focus_coverage")
                focus_status = focus.get("status") if isinstance(focus, dict) else None
                if focus_status not in {"match", "not_applicable"}:
                    errors.append(
                        "$.comparison_summary.user_focus_coverage: "
                        "approved review requires covered or absent user focus"
                    )
        if summary.get("verdict") == "rejected" and summary.get("ready_for_downstream") is not False:
            errors.append(
                "$.review_summary.ready_for_downstream: rejected review must not be ready"
            )

    if unresolved_ids != expected_unresolved:
        missing = sorted(expected_unresolved - unresolved_ids)
        extra = sorted(unresolved_ids - expected_unresolved)
        if missing:
            errors.append(f"$.unresolved_findings: missing unresolved findings {missing}")
        if extra:
            errors.append(
                f"$.unresolved_findings: IDs without unresolved correction action {extra}"
            )

    if isinstance(finalization, dict):
        applied_ids_value = finalization.get("applied_finding_ids")
        unresolved_ids_value = finalization.get("unresolved_finding_ids")
        applied_ids = set(applied_ids_value) if isinstance(applied_ids_value, list) else set()
        final_unresolved_ids = (
            set(unresolved_ids_value) if isinstance(unresolved_ids_value, list) else set()
        )

        missing_references = sorted(applied_ids - finding_ids)
        if missing_references:
            errors.append(
                f"$.finalization.applied_finding_ids: unknown finding IDs {missing_references}"
            )
        if applied_ids != expected_applied:
            errors.append(
                "$.finalization.applied_finding_ids: must exactly match applied correction actions"
            )
        if final_unresolved_ids - unresolved_ids:
            errors.append(
                "$.finalization.unresolved_finding_ids: contains unknown unresolved IDs"
            )
        if final_unresolved_ids != expected_unresolved:
            errors.append(
                "$.finalization.unresolved_finding_ids: must exactly match unresolved actions"
            )
        if isinstance(summary, dict) and (
            finalization.get("ready_for_downstream")
            != summary.get("ready_for_downstream")
        ):
            errors.append(
                "$.finalization.ready_for_downstream: must match review summary"
            )
        if finalization.get("ready_for_downstream") is True:
            if finalization.get("final_validation_status") != "valid":
                errors.append(
                    "$.finalization.final_validation_status: ready final must be valid"
                )
            if not isinstance(finalization.get("final_analysis_id"), str):
                errors.append(
                    "$.finalization.final_analysis_id: ready final requires an analysis ID"
                )
            if not isinstance(finalization.get("final_analysis_ref"), str):
                errors.append(
                    "$.finalization.final_analysis_ref: ready final requires a reference"
                )

    errors.extend(find_forbidden_keys(data))
    return errors


def validate_draft_consistency(review: Any, draft: Any) -> list[str]:
    if not isinstance(review, dict) or not isinstance(draft, dict):
        return []
    errors: list[str] = []
    a1_validator = load_a1_validator()
    errors.extend(f"draft {error}" for error in a1_validator.validate_document(draft))

    source_analysis = review.get("source_analysis")
    expected_id = source_analysis.get("analysis_id") if isinstance(source_analysis, dict) else None
    if draft.get("analysis_id") != expected_id:
        errors.append(
            "$.source_analysis.analysis_id: does not match draft analysis_id"
        )

    draft_entities = collect_analysis_entities(draft)
    expected_assessments = expected_assessment_keys(draft)
    assessment_items = review.get("entity_assessments")
    actual_assessments = {
        (item.get("entity_type"), item.get("entity_id"))
        for item in assessment_items
        if isinstance(item, dict)
        and isinstance(item.get("entity_type"), str)
        and isinstance(item.get("entity_id"), str)
    } if isinstance(assessment_items, list) else set()
    missing_assessments = sorted(expected_assessments - actual_assessments)
    if missing_assessments:
        errors.append(
            "$.entity_assessments: missing draft object assessments "
            f"{missing_assessments}"
        )
    findings = review.get("findings")
    if isinstance(findings, list):
        for index, finding in enumerate(findings):
            if not isinstance(finding, dict) or finding.get("correction_action") not in OLD_ENTITY_ACTIONS:
                continue
            affected = finding.get("affected_entities")
            if not isinstance(affected, list):
                continue
            for entity_index, entity_id in enumerate(affected):
                if isinstance(entity_id, str) and entity_id not in draft_entities:
                    errors.append(
                        f"$.findings[{index}].affected_entities[{entity_index}]: "
                        f"old entity {entity_id!r} is missing from draft"
                    )
    return errors


def validate_final_consistency(review: Any, final: Any) -> list[str]:
    if not isinstance(review, dict) or not isinstance(final, dict):
        return []
    errors: list[str] = []
    a1_validator = load_a1_validator()
    errors.extend(f"final {error}" for error in a1_validator.validate_document(final))
    errors.extend(find_review_only_keys(final))

    finalization = review.get("finalization")
    expected_id = finalization.get("final_analysis_id") if isinstance(finalization, dict) else None
    if final.get("analysis_id") != expected_id:
        errors.append(
            "$.finalization.final_analysis_id: does not match final analysis_id"
        )

    final_entities = collect_analysis_entities(final)
    findings = review.get("findings")
    if isinstance(findings, list):
        for index, finding in enumerate(findings):
            if not isinstance(finding, dict):
                continue
            action = finding.get("correction_action")
            affected = finding.get("affected_entities")
            if not isinstance(affected, list):
                continue
            for entity_index, entity_id in enumerate(affected):
                if not isinstance(entity_id, str):
                    continue
                path = f"$.findings[{index}].affected_entities[{entity_index}]"
                if action == "removed" and entity_id in final_entities:
                    errors.append(f"{path}: removed entity {entity_id!r} still exists in final")
                elif action == "added" and entity_id not in final_entities:
                    errors.append(f"{path}: added entity {entity_id!r} is missing from final")
                elif action == "modified" and entity_id not in final_entities:
                    errors.append(f"{path}: modified entity {entity_id!r} is missing from final")
    return errors


def validate_document(
    review: Any,
    *,
    draft: Any | None = None,
    final: Any | None = None,
) -> list[str]:
    errors = validate_review_schema(review) + validate_review_semantics(review)
    if draft is not None:
        errors.extend(validate_draft_consistency(review, draft))
    if final is not None:
        errors.extend(validate_final_consistency(review, final))
    return errors


def read_input(path: Path, label: str) -> tuple[Any | None, list[str]]:
    try:
        return load_json(path), []
    except FileNotFoundError:
        return None, [f"$: {label} file not found: {path}"]
    except OSError as exc:
        return None, [f"$: unable to read {label} file: {exc}"]
    except json.JSONDecodeError as exc:
        return None, [
            f"$: invalid {label} JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ]


def validate_file(
    review_path: Path,
    *,
    draft_path: Path | None = None,
    final_path: Path | None = None,
) -> list[str]:
    review, errors = read_input(review_path, "review")
    if errors:
        return errors

    draft = None
    if draft_path is not None:
        draft, draft_errors = read_input(draft_path, "draft")
        errors.extend(draft_errors)

    final = None
    if final_path is not None:
        final, final_errors = read_input(final_path, "final")
        errors.extend(final_errors)

    if errors:
        return errors
    return validate_document(review, draft=draft, final=final)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate an A2 layout-reference review and optional analysis files."
    )
    parser.add_argument("review_json", type=Path, help="Path to the review JSON")
    parser.add_argument("--draft", type=Path, help="Optional A1 draft JSON")
    parser.add_argument("--final", type=Path, help="Optional A2 final analysis JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        errors = validate_file(
            args.review_json,
            draft_path=args.draft,
            final_path=args.final,
        )
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(f"Validation failed: unable to initialize validators: {exc}", file=sys.stderr)
        return 2

    if errors:
        print(f"Validation failed with {len(errors)} error(s):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Valid layout reference review: {args.review_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
