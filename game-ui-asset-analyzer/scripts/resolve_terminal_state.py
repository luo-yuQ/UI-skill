#!/usr/bin/env python3
"""Resolve Stage2-A Asset / Stop Contract v0.1 without a VLM or image."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

import validate_node_route as router


ROOT = Path(__file__).resolve().parents[1]
RESULT_SCHEMA_PATH = ROOT / "schemas" / "asset-stop-result.schema.json"
SEMANTIC_SCHEMA_PATH = ROOT / "schemas" / "semantic-decomposition.schema.json"
PRODUCER_ACTIONS = frozenset(
    action for action in router.ROLE_ACTION_MAP.values() if action != "stop"
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_result_schema() -> dict[str, Any]:
    schema = load_json(RESULT_SCHEMA_PATH)
    if not isinstance(schema, dict):
        raise ValueError("resolver result schema root must be an object")
    Draft202012Validator.check_schema(schema)
    return schema


def load_frozen_taxonomy() -> frozenset[str]:
    """Read the canonical taxonomy enum from the frozen semantic schema."""

    schema = load_json(SEMANTIC_SCHEMA_PATH)
    try:
        values = schema["$defs"]["taxonomy"]["enum"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"frozen taxonomy is unavailable in {SEMANTIC_SCHEMA_PATH}"
        ) from exc
    if (
        not isinstance(values, list)
        or not values
        or not all(isinstance(value, str) and value for value in values)
        or len(values) != len(set(values))
    ):
        raise ValueError("frozen taxonomy enum is invalid")
    return frozenset(values)


def json_path(parts: Iterable[Any]) -> str:
    path = "$"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def validate_result(result: Any) -> list[str]:
    """Validate the result schema and existing frozen role/action mapping."""

    try:
        validator = Draft202012Validator(load_result_schema())
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [f"$: unable to load result schema {RESULT_SCHEMA_PATH}: {exc}"]
    errors = [
        f"{json_path(error.absolute_path)}: {error.message}"
        for error in sorted(
            validator.iter_errors(result),
            key=lambda item: (
                tuple(str(part) for part in item.absolute_path),
                item.message,
            ),
        )
    ]
    if not isinstance(result, dict) or result.get("requires_router") is not False:
        return errors
    role = result.get("node_role")
    action = result.get("next_action")
    try:
        expected_action = router.resolve_node_action(role)
    except ValueError as exc:
        errors.append(f"$.node_role: deterministic route unavailable: {exc}")
        return errors
    if action != expected_action:
        errors.append(
            f"$.next_action: {action!r} does not match frozen route "
            f"{role!r} -> {expected_action!r}"
        )
    expected_terminal = role == "asset"
    if result.get("terminal") is not expected_terminal:
        errors.append(
            f"$.terminal: must be {expected_terminal} for node_role {role!r}"
        )
    return errors


def _resolved_role_result(node_role: str) -> dict[str, Any]:
    action = router.resolve_node_action(node_role)
    result = {
        "node_role": node_role,
        "terminal": node_role == "asset",
        "next_action": action,
        "requires_router": False,
    }
    errors = validate_result(result)
    if errors:
        raise ValueError("invalid terminal-state result: " + "; ".join(errors))
    return result


def resolve_terminal_state(
    *,
    node_role: str | None = None,
    produced_by: str | None = None,
    taxonomy: str | None = None,
) -> dict[str, Any]:
    """Resolve terminal state from a Router role or deterministic provenance."""

    if produced_by is not None and (
        not isinstance(produced_by, str) or produced_by not in PRODUCER_ACTIONS
    ):
        raise ValueError(f"unsupported produced_by: {produced_by!r}")

    if produced_by == "semantic_decompose":
        if (
            not isinstance(taxonomy, str)
            or taxonomy not in load_frozen_taxonomy()
        ):
            raise ValueError(f"invalid frozen taxonomy: {taxonomy!r}")
        if node_role is not None and node_role != "asset":
            raise ValueError(
                "conflicting inputs: semantic_decompose provenance implies "
                f"node_role 'asset', got {node_role!r}"
            )
        return _resolved_role_result("asset")

    if taxonomy is not None:
        raise ValueError(
            "taxonomy is valid only for semantic_decompose provenance"
        )

    if produced_by == "expand_instances":
        if node_role is not None and node_role != "component_instance":
            raise ValueError(
                "conflicting inputs: expand_instances provenance implies "
                f"node_role 'component_instance', got {node_role!r}"
            )
        return _resolved_role_result("component_instance")

    if produced_by == "structural_split" and node_role is None:
        result = {"terminal": False, "requires_router": True}
        errors = validate_result(result)
        if errors:
            raise ValueError("invalid terminal-state result: " + "; ".join(errors))
        return result

    if node_role is None:
        raise ValueError("node_role is required when provenance cannot infer a role")
    return _resolved_role_result(node_role)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve Stage2-A Asset / Stop Contract v0.1."
    )
    parser.add_argument("--node-role")
    parser.add_argument("--produced-by", choices=sorted(PRODUCER_ACTIONS))
    parser.add_argument("--taxonomy")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = resolve_terminal_state(
            node_role=args.node_role,
            produced_by=args.produced_by,
            taxonomy=args.taxonomy,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"Terminal-state resolution failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
