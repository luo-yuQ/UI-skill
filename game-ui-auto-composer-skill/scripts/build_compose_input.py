#!/usr/bin/env python3
"""Build a Composer input from authoritative request, layout, and style JSON files."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


COMPOSER_ROOT = Path(__file__).resolve().parents[1]
INPUT_SCHEMA_PATH = COMPOSER_ROOT / "schemas" / "ui-compose-input.schema.json"


class ComposeInputBuildError(ValueError):
    """Raised when source data cannot be preserved in a valid input envelope."""


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def json_value_equal(left: Any, right: Any) -> bool:
    """Compare parsed JSON values recursively, keeping booleans distinct from numbers."""

    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            json_value_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            json_value_equal(left_item, right_item)
            for left_item, right_item in zip(left, right)
        )
    return type(left) is type(right) and left == right


def load_input_contract() -> tuple[str, set[str]]:
    schema = load_json(INPUT_SCHEMA_PATH)
    try:
        schema_version = schema["properties"]["schema_version"]["const"]
        request_fields = set(schema["$defs"]["request"]["properties"])
    except (KeyError, TypeError) as exc:
        raise ComposeInputBuildError(
            f"Unable to read the current Composer input contract: {exc}"
        ) from exc
    if not isinstance(schema_version, str):
        raise ComposeInputBuildError("Composer schema_version const must be a string")
    return schema_version, request_fields


def extract_request(source: Any, allowed_fields: set[str]) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise ComposeInputBuildError("request.json must contain a JSON object")

    if "user_requirement" in source and "request" in source:
        raise ComposeInputBuildError(
            "request.json is ambiguous: user_requirement exists both directly and under request"
        )
    if "user_requirement" in source:
        request_source = source
    elif isinstance(source.get("request"), dict):
        request_source = source["request"]
    else:
        raise ComposeInputBuildError(
            "request.json must be a request object or contain a request object"
        )

    if "user_requirement" not in request_source:
        raise ComposeInputBuildError("request.json is missing user_requirement")
    return {
        field: value
        for field, value in request_source.items()
        if field in allowed_fields
    }


def verify_embedded_values(
    built: Any,
    request: dict[str, Any],
    layout: Any,
    style: Any,
) -> None:
    if not isinstance(built, dict):
        raise ComposeInputBuildError("Generated Composer input is not a JSON object")
    if built.get("request", {}).get("user_requirement") != request["user_requirement"]:
        raise ComposeInputBuildError("Embedded user_requirement differs from request.json")
    if not json_value_equal(built.get("layout_reference_analysis"), layout):
        raise ComposeInputBuildError("Embedded layout analysis differs from source A")
    if not json_value_equal(built.get("style_profile"), style):
        raise ComposeInputBuildError("Embedded style profile differs from source B")


def build_compose_input(
    request_path: Path,
    layout_path: Path,
    style_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Build, write, reread, and integrity-check one ui-compose-input document."""

    schema_version, allowed_request_fields = load_input_contract()
    request = extract_request(load_json(request_path), allowed_request_fields)
    layout = load_json(layout_path)
    style = load_json(style_path)

    if not isinstance(layout, dict):
        raise ComposeInputBuildError("Layout analysis source must contain a JSON object")
    if not isinstance(style, dict):
        raise ComposeInputBuildError("Style profile source must contain a JSON object")

    document = {
        "schema_version": schema_version,
        "request": request,
        "layout_reference_analysis": layout,
        "style_profile": style,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=output_path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)

    try:
        with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")

        written = load_json(temporary_path)
        verify_embedded_values(written, request, layout, style)
        os.replace(temporary_path, output_path)

        final_document = load_json(output_path)
        verify_embedded_values(final_document, request, layout, style)
        return final_document
    finally:
        temporary_path.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path, help="Source request.json")
    parser.add_argument("--layout", required=True, type=Path, help="Source layout analysis JSON")
    parser.add_argument("--style", required=True, type=Path, help="Source style-profile.json")
    parser.add_argument("--output", required=True, type=Path, help="Destination ui-compose-input.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        document = build_compose_input(
            request_path=args.request,
            layout_path=args.layout,
            style_path=args.style,
            output_path=args.output,
        )
    except (OSError, json.JSONDecodeError, ComposeInputBuildError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "status": "built",
                "schema_version": document["schema_version"],
                "output": str(args.output),
                "user_requirement_preserved": True,
                "layout_preserved": True,
                "style_preserved": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
