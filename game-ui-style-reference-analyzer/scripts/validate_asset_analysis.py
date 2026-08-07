#!/usr/bin/env python3
"""Validate one B1 asset-analysis JSON document against its bundled schema."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = SKILL_ROOT / "schemas" / "asset-analysis.schema.json"

FORBIDDEN_PURPOSE_PHRASES = (
    "适合用于",
    "适合用在",
    "推荐作为",
    "推荐用于",
    "可以用作",
    "可用作",
    "should be used",
    "recommended as",
    "recommended for",
    "suitable for",
    "can be used as",
    "could be used as",
)
FORBIDDEN_MATERIAL_PATTERNS = (
    r"\bfire\b",
    r"\bsmoke\b",
    r"\bfog\b",
    r"\bglow\b",
    r"\bbloom\b",
    r"\bparticles?\b",
    r"\bsparks?\b",
    r"\bmagical light\b",
    r"\bemissive\b",
    r"\bluminescent\b",
    "火焰",
    "烟雾",
    "雾气",
    "辉光",
    "泛光",
    "粒子",
    "火花",
    "魔法光",
    "发光",
)
FORBIDDEN_STYLE_CANDIDATE_PATTERNS = (
    r"\bcomposition\b",
    r"\bcamera angle\b",
    r"\blow[- ]angle\b",
    r"\bhigh[- ]angle\b",
    r"\bsubject placement\b",
    r"\bpositioned on the\b",
    r"\bperspective layout\b",
    r"\bpage layout\b",
    r"\belement positions?\b",
    r"\bupper illustration\b",
    r"\blower panel\b",
    r"\btop illustration\b",
    r"\bbottom panel\b",
    r"\bforeground/background arrangement\b",
    "构图",
    "镜头",
    "机位",
    "视角",
    "主体位于",
    "角色位于",
    "透视布局",
    "页面布局",
    "元素位置",
    "上方插画",
    "下方面板",
    "上图下板",
    "前景背景安排",
)


def json_path(parts: Iterable[Any]) -> str:
    """Render a jsonschema path as a compact JSONPath-like string."""

    path = "$"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def load_json(path: Path) -> Any:
    """Load UTF-8 JSON without modifying the input file."""

    return json.loads(path.read_text(encoding="utf-8"))


def check_schema_definition(schema: dict[str, Any]) -> list[str]:
    """Sanity-check the JSON Schema structures used by this project.

    Full meta-schema checking is performed by ``jsonschema`` when installed.
    This local check prevents malformed types, containers, or local references
    from silently passing in dependency-free environments.
    """

    errors: list[str] = []
    allowed_types = {"object", "array", "string", "number", "integer", "boolean", "null"}

    def resolve_pointer(pointer: str) -> Any:
        node: Any = schema
        for token in pointer.lstrip("/").split("/") if pointer else []:
            token = token.replace("~1", "/").replace("~0", "~")
            node = node[token]
        return node

    def visit(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            errors.append(f"{path}: schema node must be an object")
            return

        declared_type = node.get("type")
        if declared_type is not None:
            choices = declared_type if isinstance(declared_type, list) else [declared_type]
            if not choices or any(choice not in allowed_types for choice in choices):
                errors.append(f"{path}.type: invalid JSON Schema type declaration")

        ref = node.get("$ref")
        if ref is not None:
            if not isinstance(ref, str) or not ref.startswith("#"):
                errors.append(f"{path}.$ref: only local references are supported")
            else:
                try:
                    target = resolve_pointer(ref[1:])
                except (KeyError, TypeError):
                    errors.append(f"{path}.$ref: unresolved reference {ref!r}")
                else:
                    if not isinstance(target, dict):
                        errors.append(f"{path}.$ref: target must be a schema object")

        for keyword in ("properties", "$defs"):
            children = node.get(keyword)
            if children is not None:
                if not isinstance(children, dict):
                    errors.append(f"{path}.{keyword}: must be an object")
                else:
                    for name, child in children.items():
                        visit(child, f"{path}.{keyword}.{name}")

        items = node.get("items")
        if items is not None:
            visit(items, f"{path}.items")

        any_of = node.get("anyOf")
        if any_of is not None:
            if not isinstance(any_of, list) or not any_of:
                errors.append(f"{path}.anyOf: must be a non-empty array")
            else:
                for index, child in enumerate(any_of):
                    visit(child, f"{path}.anyOf[{index}]")

        required = node.get("required")
        if required is not None and (
            not isinstance(required, list)
            or any(not isinstance(field, str) for field in required)
        ):
            errors.append(f"{path}.required: must be an array of strings")

        enum = node.get("enum")
        if enum is not None and not isinstance(enum, list):
            errors.append(f"{path}.enum: must be an array")

        additional = node.get("additionalProperties")
        if additional is not None and not isinstance(additional, (bool, dict)):
            errors.append(f"{path}.additionalProperties: must be boolean or a schema")

    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errors.append("$.$schema: expected the Draft 2020-12 meta-schema URI")
    visit(schema, "$")
    return errors


class SchemaSubsetValidator:
    """Validate the Draft 2020-12 subset used by the bundled schema.

    The project treats ``jsonschema`` as optional. This fallback covers the
    schema keywords used by B1 so validation remains deterministic without
    adding a new dependency.
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
            errors.append(f"{path}: value {value!r} is not in the allowed enum")

        expected = schema.get("type")
        if expected is not None:
            choices = expected if isinstance(expected, list) else [expected]
            if not any(self._matches_type(value, choice) for choice in choices):
                errors.append(f"{path}: expected type {choices}, got {type(value).__name__}")
                return

        if isinstance(value, dict):
            for field in schema.get("required", []):
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
            maximum = schema.get("maxItems")
            if minimum is not None and len(value) < minimum:
                errors.append(f"{path}: expected at least {minimum} item(s)")
            if maximum is not None and len(value) > maximum:
                errors.append(f"{path}: expected at most {maximum} item(s)")
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
    """Use jsonschema when available, otherwise use the local subset validator."""

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
    except Exception as exc:  # pragma: no cover - optional package/version behavior
        return [f"$: unable to initialize Draft 2020-12 validation: {exc}"]


def load_schema(path: Path = SCHEMA_PATH) -> dict[str, Any]:
    schema = load_json(path)
    if not isinstance(schema, dict):
        raise ValueError("schema root must be an object")
    definition_errors = check_schema_definition(schema)
    if definition_errors:
        raise ValueError("invalid schema definition: " + "; ".join(definition_errors))
    return schema


def iter_string_values(value: Any, path: str = "$") -> Iterable[tuple[str, str]]:
    """Yield every string value with its JSONPath-like location."""

    if isinstance(value, dict):
        for key, child in value.items():
            yield from iter_string_values(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_string_values(child, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def contains_pattern(text: str, patterns: Iterable[str]) -> str | None:
    """Return the first forbidden case-insensitive regex pattern found."""

    for pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return pattern
    return None


def validate_semantics(data: Any) -> list[str]:
    """Enforce B1 descriptive boundaries without performing visual inference."""

    errors: list[str] = []

    for path, text in iter_string_values(data):
        matched = contains_pattern(text, FORBIDDEN_PURPOSE_PHRASES)
        if matched is not None:
            errors.append(
                f"{path}: purpose, suitability, or recommendation language is forbidden "
                f"in every B1 field (matched {matched!r})"
            )

    if not isinstance(data, dict):
        return errors

    visual_language = data.get("visual_language")
    material = visual_language.get("material") if isinstance(visual_language, dict) else None
    if material is not None:
        for path, text in iter_string_values(material, "$.visual_language.material"):
            matched = contains_pattern(text, FORBIDDEN_MATERIAL_PATTERNS)
            if matched is not None:
                errors.append(
                    f"{path}: non-tangible visual effects are forbidden in Material "
                    f"Language (matched {matched!r})"
                )

    candidates = data.get("style_candidates")
    if isinstance(candidates, list):
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                continue
            trait = candidate.get("trait")
            if not isinstance(trait, str):
                continue
            matched = contains_pattern(trait, FORBIDDEN_STYLE_CANDIDATE_PATTERNS)
            if matched is not None:
                errors.append(
                    f"$.style_candidates[{index}].trait: composition, camera, layout, "
                    f"or current-page spatial organization is forbidden in style candidates "
                    f"(matched {matched!r})"
                )

    return errors


def validate_document(
    data: Any,
    schema_path: Path = SCHEMA_PATH,
) -> list[str]:
    """Validate a parsed document without adding defaults or visual inference."""

    try:
        schema = load_schema(schema_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [f"$: unable to load schema {schema_path}: {exc}"]
    return validate_schema(data, schema) + validate_semantics(data)


def validate_file(
    input_path: Path,
    schema_path: Path = SCHEMA_PATH,
) -> list[str]:
    """Load and validate one input file, returning human-readable errors."""

    try:
        data = load_json(input_path)
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
        description="Validate one B1 single-reference visual analysis JSON file."
    )
    parser.add_argument("input", type=Path, help="B1 JSON document to validate")
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
    print(f"Valid B1 asset analysis: {args.input}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
