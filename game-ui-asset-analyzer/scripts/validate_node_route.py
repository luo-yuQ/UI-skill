#!/usr/bin/env python3
"""Validate and deterministically resolve a Stage2-A Node Router v0.1 result."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "node-route.schema.json"

ROLE_ACTION_MAP = {
    "structural_group": "structural_split",
    "repeated_group": "expand_instances",
    "component_instance": "semantic_decompose",
    "asset": "stop",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_schema() -> dict[str, Any]:
    schema = load_json(SCHEMA_PATH)
    if not isinstance(schema, dict):
        raise ValueError("schema root must be an object")
    Draft202012Validator.check_schema(schema)
    return schema


def json_path(parts: Iterable[Any]) -> str:
    path = "$"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def validate_schema(data: Any) -> list[str]:
    try:
        schema = load_schema()
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [f"$: unable to load schema {SCHEMA_PATH}: {exc}"]

    validator = Draft202012Validator(schema)
    return [
        f"{json_path(error.absolute_path)}: {error.message}"
        for error in sorted(
            validator.iter_errors(data),
            key=lambda item: (
                tuple(str(part) for part in item.absolute_path),
                item.message,
            ),
        )
    ]


def resolve_node_action(role: str) -> str:
    """Return the frozen action for a valid role; never silently fall back."""

    try:
        return ROLE_ACTION_MAP[role]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"unsupported node_role: {role!r}") from exc


def build_route_result(role: str) -> dict[str, str]:
    """Build the engineering-owned route result from a validated VLM role."""

    return {
        "node_role": role,
        "next_action": resolve_node_action(role),
    }


def validate_document(data: Any) -> list[str]:
    """Validate immutable raw VLM output without judging or changing its role."""

    errors = validate_schema(data)
    if isinstance(data, dict) and "node_role" in data:
        try:
            resolve_node_action(data["node_role"])
        except ValueError as exc:
            errors.append(f"$.node_role: deterministic route unavailable: {exc}")
    return errors


def validate_file(document: Path) -> list[str]:
    try:
        data = load_json(document)
    except json.JSONDecodeError as exc:
        return [
            f"$: invalid JSON in {document}: line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ]
    except (OSError, UnicodeError) as exc:
        return [f"$: unable to read {document}: {exc}"]
    return validate_document(data)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a Stage2-A Node Router v0.1 VLM result."
    )
    parser.add_argument("document", type=Path, help="raw node-route JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        data = load_json(args.document)
    except json.JSONDecodeError as exc:
        errors = [
            f"$: invalid JSON in {args.document}: line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ]
    except (OSError, UnicodeError) as exc:
        errors = [f"$: unable to read {args.document}: {exc}"]
    else:
        errors = validate_document(data)

    if errors:
        print(f"Validation failed for {args.document}:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    route = build_route_result(data["node_role"])
    print(
        "Valid Node Router v0.1 result: "
        f"{route['node_role']} -> {route['next_action']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
