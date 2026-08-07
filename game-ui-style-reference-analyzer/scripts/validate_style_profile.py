#!/usr/bin/env python3
"""Validate one B2 style-profile JSON document without reading source images."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Iterable


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = SKILL_ROOT / "schemas" / "style-profile.schema.json"
B1_VALIDATOR_PATH = SKILL_ROOT / "scripts" / "validate_asset_analysis.py"

SPEC = importlib.util.spec_from_file_location("b1_validator_for_b2", B1_VALIDATOR_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import setup guard
    raise RuntimeError("Unable to load frozen B1 validator support")
b1_validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(b1_validator)

REFERENCE_FIELDS = {
    "supporting_references",
    "contradicting_references",
    "related_references",
    "affected_references",
}


def load_schema(path: Path = SCHEMA_PATH) -> dict[str, Any]:
    """Load and locally sanity-check the B2 Draft 2020-12 schema."""

    schema = b1_validator.load_json(path)
    if not isinstance(schema, dict):
        raise ValueError("schema root must be an object")
    definition_errors = b1_validator.check_schema_definition(schema)
    if definition_errors:
        raise ValueError("invalid schema definition: " + "; ".join(definition_errors))
    return schema


def iter_objects(value: Any, path: str = "$") -> Iterable[tuple[str, dict[str, Any]]]:
    """Yield all object nodes with JSONPath-like locations."""

    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from iter_objects(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_objects(child, f"{path}[{index}]")


def validate_unique_identifiers(
    objects: Iterable[tuple[str, dict[str, Any]]],
    field: str,
) -> list[str]:
    """Report duplicate identifiers across matching objects."""

    errors: list[str] = []
    seen: dict[str, str] = {}
    for path, item in objects:
        identifier = item.get(field)
        if not isinstance(identifier, str):
            continue
        if identifier in seen:
            errors.append(
                f"{path}.{field}: duplicate {field} {identifier!r}; "
                f"first declared at {seen[identifier]}"
            )
        else:
            seen[identifier] = f"{path}.{field}"
    return errors


def validate_semantics(data: Any) -> list[str]:
    """Validate B2 provenance and identity invariants without synthesizing traits."""

    errors = b1_validator.validate_semantics(data)
    if not isinstance(data, dict):
        return errors

    sources = data.get("source_analyses")
    known_ids: set[str] = set()
    source_objects: list[tuple[str, dict[str, Any]]] = []
    if isinstance(sources, list):
        for index, item in enumerate(sources):
            if not isinstance(item, dict):
                continue
            path = f"$.source_analyses[{index}]"
            source_objects.append((path, item))
            asset_id = item.get("asset_id")
            if isinstance(asset_id, str):
                if asset_id in known_ids:
                    errors.append(f"{path}.asset_id: duplicate source asset_id {asset_id!r}")
                known_ids.add(asset_id)

    all_objects = list(iter_objects(data))
    errors.extend(validate_unique_identifiers(all_objects, "trait_id"))
    errors.extend(validate_unique_identifiers(all_objects, "conflict_id"))
    errors.extend(validate_unique_identifiers(all_objects, "uncertainty_id"))

    for path, item in all_objects:
        for field in REFERENCE_FIELDS:
            references = item.get(field)
            if not isinstance(references, list):
                continue
            for index, reference in enumerate(references):
                if isinstance(reference, str) and reference not in known_ids:
                    errors.append(
                        f"{path}.{field}[{index}]: unknown B1 asset_id {reference!r}"
                    )

    return errors


def validate_document(
    data: Any,
    schema_path: Path = SCHEMA_PATH,
) -> list[str]:
    """Validate a parsed B2 document without adding, changing, or classifying traits."""

    try:
        schema = load_schema(schema_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [f"$: unable to load schema {schema_path}: {exc}"]
    return b1_validator.validate_schema(data, schema) + validate_semantics(data)


def validate_file(
    input_path: Path,
    schema_path: Path = SCHEMA_PATH,
) -> list[str]:
    """Load and validate one style-profile file with clear errors."""

    try:
        data = b1_validator.load_json(input_path)
    except json.JSONDecodeError as exc:
        return [
            f"$: invalid JSON in {input_path}: line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ]
    except (OSError, UnicodeError) as exc:
        return [f"$: unable to read {input_path}: {exc}"]
    return validate_document(data, schema_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate one B2 multi-reference style-profile JSON file."
    )
    parser.add_argument("input", type=Path, help="B2 style-profile JSON to validate")
    parser.add_argument(
        "--schema",
        type=Path,
        default=SCHEMA_PATH,
        help=f"schema path (default: {SCHEMA_PATH})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors = validate_file(args.input, args.schema)
    if errors:
        print(f"Validation failed for {args.input}:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Valid B2 style profile: {args.input}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
