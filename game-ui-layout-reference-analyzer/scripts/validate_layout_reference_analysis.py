#!/usr/bin/env python3
"""Validate an A1 layout-reference analysis without reading its source image."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = SKILL_ROOT / "schemas" / "layout-reference-analysis.schema.json"
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
    "model",
    "response_format",
}


def json_path(parts: Iterable[Any]) -> str:
    path = "$"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class SchemaSubsetValidator:
    """Validate the Draft 2020-12 keywords used by the bundled schema.

    This is deliberately not a general JSON Schema implementation. It keeps the
    validator deterministic when the optional ``jsonschema`` package is absent.
    """

    def __init__(self, schema: dict[str, Any]) -> None:
        self.schema = schema

    @staticmethod
    def _resolve_pointer(root: Any, pointer: str) -> Any:
        node = root
        for token in pointer.lstrip("/").split("/") if pointer else []:
            token = token.replace("~1", "/").replace("~0", "~")
            node = node[token]
        return node

    @staticmethod
    def _matches_type(value: Any, expected: str) -> bool:
        checks = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "number": lambda item: isinstance(item, (int, float))
            and not isinstance(item, bool),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "null": lambda item: item is None,
        }
        return expected in checks and checks[expected](value)

    def validate(self, data: Any) -> list[str]:
        errors: list[str] = []
        self._validate_node(data, self.schema, "$", errors)
        return errors

    def _validate_node(
        self,
        value: Any,
        schema: dict[str, Any],
        path: str,
        errors: list[str],
    ) -> None:
        if "$ref" in schema:
            ref = schema["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#"):
                errors.append(f"{path}: unsupported schema reference {ref!r}")
                return
            try:
                target = self._resolve_pointer(self.schema, ref[1:])
            except (KeyError, TypeError):
                errors.append(f"{path}: unresolved schema reference {ref!r}")
                return
            self._validate_node(value, target, path, errors)
            return

        if "anyOf" in schema:
            alternatives: list[list[str]] = []
            for branch in schema["anyOf"]:
                branch_errors: list[str] = []
                self._validate_node(value, branch, path, branch_errors)
                alternatives.append(branch_errors)
            if all(branch_errors for branch_errors in alternatives):
                errors.append(f"{path}: value does not match any allowed schema branch")
                return

        if "const" in schema and value != schema["const"]:
            errors.append(f"{path}: expected constant {schema['const']!r}")

        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: value is not in the allowed enum")

        expected = schema.get("type")
        if expected is not None:
            choices = expected if isinstance(expected, list) else [expected]
            if not any(self._matches_type(value, choice) for choice in choices):
                errors.append(f"{path}: expected type {choices}, got {type(value).__name__}")
                return

        if isinstance(value, dict):
            required = schema.get("required", [])
            for field in required:
                if field not in value:
                    errors.append(f"{path}.{field}: missing required field")

            properties = schema.get("properties", {})
            if schema.get("additionalProperties") is False:
                for field in sorted(set(value) - set(properties)):
                    errors.append(f"{path}.{field}: unexpected field")

            for field, child in value.items():
                if field in properties:
                    self._validate_node(child, properties[field], f"{path}.{field}", errors)

        if isinstance(value, list):
            minimum = schema.get("minItems")
            if minimum is not None and len(value) < minimum:
                errors.append(f"{path}: expected at least {minimum} item(s)")
            if schema.get("uniqueItems"):
                serialized = [
                    json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value
                ]
                if len(serialized) != len(set(serialized)):
                    errors.append(f"{path}: array items must be unique")
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for index, child in enumerate(value):
                    self._validate_node(child, item_schema, f"{path}[{index}]", errors)

        if isinstance(value, str):
            minimum = schema.get("minLength")
            if minimum is not None and len(value) < minimum:
                errors.append(f"{path}: string must contain at least {minimum} character(s)")
            pattern = schema.get("pattern")
            if pattern and re.search(pattern, value) is None:
                errors.append(f"{path}: string does not match required pattern")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            minimum = schema.get("minimum")
            maximum = schema.get("maximum")
            if minimum is not None and value < minimum:
                errors.append(f"{path}: value must be >= {minimum}")
            if maximum is not None and value > maximum:
                errors.append(f"{path}: value must be <= {maximum}")


def validate_schema(data: Any, schema: dict[str, Any]) -> list[str]:
    """Use jsonschema when available, otherwise validate the bundled subset."""

    try:
        from jsonschema import Draft202012Validator
    except ModuleNotFoundError:
        return SchemaSubsetValidator(schema).validate(data)

    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        return [
            f"{json_path(error.absolute_path)}: {error.message}"
            for error in sorted(
                validator.iter_errors(data),
                key=lambda item: json_path(item.absolute_path),
            )
        ]
    except Exception as exc:  # pragma: no cover - depends on optional package version
        return [f"$: unable to initialize Draft 2020-12 validation: {exc}"]


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


def collect_unique_ids(
    items: Any,
    id_field: str,
    path: str,
    errors: list[str],
) -> set[str]:
    identifiers: set[str] = set()
    if not isinstance(items, list):
        return identifiers
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        identifier = item.get(id_field)
        if not isinstance(identifier, str):
            continue
        if identifier in identifiers:
            errors.append(f"{path}[{index}].{id_field}: duplicate ID {identifier!r}")
        identifiers.add(identifier)
    return identifiers


def check_parent_cycles(regions: Any, region_ids: set[str], errors: list[str]) -> None:
    if not isinstance(regions, list):
        return
    parent_by_id = {
        region.get("region_id"): region.get("parent_region_id")
        for region in regions
        if isinstance(region, dict) and isinstance(region.get("region_id"), str)
    }

    for region_id, parent_id in parent_by_id.items():
        if parent_id == region_id:
            errors.append(f"$.regions: region {region_id!r} cannot be its own parent")
        elif parent_id is not None and parent_id not in region_ids:
            errors.append(
                f"$.regions: region {region_id!r} references missing parent {parent_id!r}"
            )

    reported: set[tuple[str, ...]] = set()
    for start in parent_by_id:
        order: list[str] = []
        positions: dict[str, int] = {}
        current: Any = start
        while isinstance(current, str) and current in parent_by_id:
            if current in positions:
                cycle = tuple(order[positions[current] :])
                normalized = tuple(sorted(cycle))
                if len(cycle) > 1 and normalized not in reported:
                    errors.append(f"$.regions: parent cycle detected: {' -> '.join(cycle)}")
                    reported.add(normalized)
                break
            positions[current] = len(order)
            order.append(current)
            current = parent_by_id[current]


def check_bounds(regions: Any, errors: list[str]) -> None:
    if not isinstance(regions, list):
        return
    for index, region in enumerate(regions):
        if not isinstance(region, dict):
            continue
        bounds = region.get("approximate_bounds")
        if not isinstance(bounds, dict):
            continue
        x, y = bounds.get("x"), bounds.get("y")
        width, height = bounds.get("width"), bounds.get("height")
        numbers = (x, y, width, height)
        if all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in numbers):
            if x + width > 1 + 1e-9:
                errors.append(
                    f"$.regions[{index}].approximate_bounds: x + width must be <= 1"
                )
            if y + height > 1 + 1e-9:
                errors.append(
                    f"$.regions[{index}].approximate_bounds: y + height must be <= 1"
                )


def hierarchy_entries(visual_hierarchy: Any) -> Iterable[tuple[str, Any]]:
    if not isinstance(visual_hierarchy, dict):
        return
    for field in ("primary_focal_point", "primary_action"):
        entry = visual_hierarchy.get(field)
        if isinstance(entry, dict):
            yield f"$.visual_hierarchy.{field}", entry
    for field in ("secondary_focal_points", "supporting_information", "background_content"):
        entries = visual_hierarchy.get(field)
        if isinstance(entries, list):
            for index, entry in enumerate(entries):
                if isinstance(entry, dict):
                    yield f"$.visual_hierarchy.{field}[{index}]", entry


def validate_semantics(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return []

    errors: list[str] = []
    regions = data.get("regions")
    relationships = data.get("region_relationships")
    groups = data.get("component_groups")
    rules = data.get("layout_rules")
    uncertainties = data.get("uncertainties")

    region_ids = collect_unique_ids(regions, "region_id", "$.regions", errors)
    group_ids = collect_unique_ids(groups, "group_id", "$.component_groups", errors)
    collect_unique_ids(
        relationships,
        "relationship_id",
        "$.region_relationships",
        errors,
    )
    collect_unique_ids(rules, "rule_id", "$.layout_rules", errors)
    collect_unique_ids(
        uncertainties,
        "uncertainty_id",
        "$.uncertainties",
        errors,
    )

    check_parent_cycles(regions, region_ids, errors)
    check_bounds(regions, errors)

    if isinstance(relationships, list):
        for index, relationship in enumerate(relationships):
            if not isinstance(relationship, dict):
                continue
            for field in ("source_region_id", "target_region_id"):
                reference = relationship.get(field)
                if isinstance(reference, str) and reference not in region_ids:
                    errors.append(
                        f"$.region_relationships[{index}].{field}: "
                        f"missing region {reference!r}"
                    )

    if isinstance(groups, list):
        for index, group in enumerate(groups):
            if not isinstance(group, dict):
                continue
            reference = group.get("region_id")
            if isinstance(reference, str) and reference not in region_ids:
                errors.append(
                    f"$.component_groups[{index}].region_id: missing region {reference!r}"
                )

    entity_ids = region_ids | group_ids
    for path, entry in hierarchy_entries(data.get("visual_hierarchy")):
        reference = entry.get("entity_id")
        if isinstance(reference, str) and reference not in entity_ids:
            errors.append(f"{path}.entity_id: missing entity {reference!r}")

    if isinstance(rules, list):
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                continue
            evidence = rule.get("source_evidence")
            if isinstance(evidence, list):
                for evidence_index, reference in enumerate(evidence):
                    if isinstance(reference, str) and reference not in entity_ids:
                        errors.append(
                            f"$.layout_rules[{index}].source_evidence[{evidence_index}]: "
                            f"missing entity {reference!r}"
                        )

    if isinstance(uncertainties, list):
        for index, uncertainty in enumerate(uncertainties):
            if not isinstance(uncertainty, dict):
                continue
            affected = uncertainty.get("affected_entities")
            if isinstance(affected, list):
                for affected_index, reference in enumerate(affected):
                    if isinstance(reference, str) and reference not in entity_ids:
                        errors.append(
                            f"$.uncertainties[{index}].affected_entities[{affected_index}]: "
                            f"missing entity {reference!r}"
                        )

    errors.extend(find_forbidden_keys(data))
    return errors


def validate_document(data: Any) -> list[str]:
    try:
        schema = load_json(SCHEMA_PATH)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"$: unable to load bundled schema: {exc}"]
    return validate_schema(data, schema) + validate_semantics(data)


def validate_file(path: Path) -> list[str]:
    try:
        data = load_json(path)
    except FileNotFoundError:
        return [f"$: input file not found: {path}"]
    except OSError as exc:
        return [f"$: unable to read input file: {exc}"]
    except json.JSONDecodeError as exc:
        return [f"$: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"]
    return validate_document(data)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate an A1 layout-reference analysis JSON file."
    )
    parser.add_argument("analysis_json", type=Path, help="Path to the analysis JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors = validate_file(args.analysis_json)
    if errors:
        print(f"Validation failed with {len(errors)} error(s):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Valid layout reference analysis: {args.analysis_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
