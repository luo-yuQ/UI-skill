#!/usr/bin/env python3
"""Validate Composer v2 input against its contract and authoritative A/B schemas."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


COMPOSER_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = COMPOSER_ROOT.parent
INPUT_SCHEMA_PATH = COMPOSER_ROOT / "schemas" / "ui-compose-input.schema.json"
LAYOUT_SCHEMA_PATH = (
    WORKSPACE_ROOT
    / "game-ui-layout-reference-analyzer"
    / "schemas"
    / "layout-reference-analysis.schema.json"
)
STYLE_SCHEMA_PATH = (
    WORKSPACE_ROOT
    / "game-ui-style-reference-analyzer"
    / "schemas"
    / "style-profile.schema.json"
)


def issue(path: str, message: str, code: str) -> dict[str, str]:
    return {"path": path, "message": message, "code": code}


def emit(
    *,
    status: str,
    mode: str,
    full_draft_validation: bool,
    errors: list[dict[str, str]],
    warnings: list[dict[str, str]],
    data: Any = None,
    notice: str | None = None,
) -> None:
    validator_info: dict[str, Any] = {
        "mode": mode,
        "full_draft_2020_12": full_draft_validation,
    }
    if notice:
        validator_info["notice"] = notice

    layout = data.get("layout_reference_analysis", {}) if isinstance(data, dict) else {}
    style = data.get("style_profile", {}) if isinstance(data, dict) else {}
    result = {
        "status": status,
        "validator": validator_info,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "composer_schema_version": data.get("schema_version") if isinstance(data, dict) else None,
            "layout_analysis_id": layout.get("analysis_id") if isinstance(layout, dict) else None,
            "style_profile_id": style.get("profile_id") if isinstance(style, dict) else None,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_contracts() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    input_schema = load_json(INPUT_SCHEMA_PATH)
    upstream = {
        "layout": load_json(LAYOUT_SCHEMA_PATH),
        "style": load_json(STYLE_SCHEMA_PATH),
    }
    return input_schema, upstream


def json_path(parts: Any) -> str:
    path = "$"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def validate_with_jsonschema(
    data: Any,
    input_schema: dict[str, Any],
    upstream: dict[str, dict[str, Any]],
) -> tuple[bool, list[dict[str, str]], str | None]:
    """Return (jsonschema_available, errors, setup_error)."""

    try:
        from jsonschema import Draft202012Validator
    except ModuleNotFoundError:
        return False, [], None

    schemas = [input_schema, *upstream.values()]
    try:
        for schema in schemas:
            Draft202012Validator.check_schema(schema)

        try:
            from referencing import Registry, Resource

            registry = Registry()
            for schema in upstream.values():
                registry = registry.with_resource(
                    schema["$id"], Resource.from_contents(schema)
                )
            validator = Draft202012Validator(input_schema, registry=registry)
        except (ImportError, TypeError):
            from jsonschema import RefResolver

            resolver = RefResolver.from_schema(
                input_schema,
                store={schema["$id"]: schema for schema in upstream.values()},
            )
            validator = Draft202012Validator(input_schema, resolver=resolver)

        errors = [
            issue(json_path(error.absolute_path), error.message, "SCHEMA_VALIDATION_ERROR")
            for error in sorted(
                validator.iter_errors(data),
                key=lambda item: json_path(item.absolute_path),
            )
        ]
        return True, errors, None
    except Exception as exc:  # pragma: no cover - package-version dependent
        return True, [], f"Unable to initialize Draft 2020-12 validation: {exc}"


class LimitedLocalValidator:
    """Validate the exact keyword subset used by the Composer and upstream contracts."""

    def __init__(
        self,
        input_schema: dict[str, Any],
        upstream: dict[str, dict[str, Any]],
    ) -> None:
        self.input_schema = input_schema
        self.registry: dict[str, dict[str, Any]] = {
            "ui-compose-input.schema.json": input_schema,
            input_schema["$id"]: input_schema,
        }
        for schema in upstream.values():
            self.registry[schema["$id"]] = schema
            self.registry[Path(schema["$id"]).name] = schema

    @staticmethod
    def _pointer(root: Any, fragment: str) -> Any:
        node = root
        if not fragment:
            return node
        for token in fragment.lstrip("/").split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            node = node[token]
        return node

    def _resolve(self, ref: str, root: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        if ref.startswith("#"):
            return self._pointer(root, ref[1:]), root
        target, _, fragment = ref.partition("#")
        target_root = self.registry.get(target) or self.registry.get(Path(target).name)
        if target_root is None:
            raise KeyError(f"Unresolved local schema reference: {ref}")
        return self._pointer(target_root, fragment), target_root

    @staticmethod
    def _matches_type(value: Any, expected: str) -> bool:
        checks = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "null": lambda item: item is None,
        }
        return expected in checks and checks[expected](value)

    def validate(self, data: Any) -> list[dict[str, str]]:
        errors: list[dict[str, str]] = []
        self._validate_node(data, self.input_schema, self.input_schema, "$", errors)
        return errors

    def _validate_node(
        self,
        value: Any,
        schema: dict[str, Any],
        root: dict[str, Any],
        path: str,
        errors: list[dict[str, str]],
    ) -> None:
        if "$ref" in schema:
            try:
                target, target_root = self._resolve(schema["$ref"], root)
            except (KeyError, TypeError) as exc:
                errors.append(issue(path, str(exc), "UNRESOLVED_SCHEMA_REFERENCE"))
                return
            self._validate_node(value, target, target_root, path, errors)

        for branch in schema.get("allOf", []):
            self._validate_node(value, branch, root, path, errors)

        if "anyOf" in schema:
            branch_results: list[list[dict[str, str]]] = []
            for branch in schema["anyOf"]:
                branch_errors: list[dict[str, str]] = []
                self._validate_node(value, branch, root, path, branch_errors)
                branch_results.append(branch_errors)
            if all(branch_errors for branch_errors in branch_results):
                errors.append(issue(path, "Value does not match any allowed schema branch", "ANY_OF_MISMATCH"))
                return

        if "const" in schema and value != schema["const"]:
            errors.append(issue(path, f"Expected constant value {schema['const']!r}", "CONST_MISMATCH"))
        if "enum" in schema and value not in schema["enum"]:
            errors.append(issue(path, "Value is not in the allowed enum", "ENUM_MISMATCH"))

        expected = schema.get("type")
        if expected is not None:
            choices = expected if isinstance(expected, list) else [expected]
            if not any(self._matches_type(value, choice) for choice in choices):
                errors.append(issue(path, f"Expected type {choices}, got {type(value).__name__}", "TYPE_MISMATCH"))
                return

        if isinstance(value, dict):
            for field in schema.get("required", []):
                if field not in value:
                    errors.append(issue(f"{path}.{field}", "Missing required field", "MISSING_REQUIRED_FIELD"))
            properties = schema.get("properties", {})
            if schema.get("additionalProperties") is False:
                for field in sorted(set(value) - set(properties)):
                    errors.append(issue(f"{path}.{field}", "Unexpected field", "ADDITIONAL_PROPERTY"))
            for field, child in value.items():
                if field in properties:
                    self._validate_node(child, properties[field], root, f"{path}.{field}", errors)

        if isinstance(value, list):
            if len(value) < schema.get("minItems", 0):
                errors.append(issue(path, "Array has fewer than minItems", "MIN_ITEMS"))
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                errors.append(issue(path, "Array has more than maxItems", "MAX_ITEMS"))
            if schema.get("uniqueItems"):
                normalized = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
                if len(normalized) != len(set(normalized)):
                    errors.append(issue(path, "Array items must be unique", "UNIQUE_ITEMS"))
            if isinstance(schema.get("items"), dict):
                for index, child in enumerate(value):
                    self._validate_node(child, schema["items"], root, f"{path}[{index}]", errors)

        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0):
                errors.append(issue(path, "String is shorter than minLength", "MIN_LENGTH"))
            if "pattern" in schema and re.search(schema["pattern"], value) is None:
                errors.append(issue(path, "String does not match required pattern", "PATTERN"))

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                errors.append(issue(path, "Number is below minimum", "MINIMUM"))
            if "maximum" in schema and value > schema["maximum"]:
                errors.append(issue(path, "Number is above maximum", "MAXIMUM"))
            if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
                errors.append(issue(path, "Number is not above exclusiveMinimum", "EXCLUSIVE_MINIMUM"))
            if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
                errors.append(issue(path, "Number is not below exclusiveMaximum", "EXCLUSIVE_MAXIMUM"))


def legacy_errors(data: Any) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if isinstance(data, dict) and "assets" in data:
        errors.append(issue("$.assets", "Legacy v1 assets input is not supported by Composer v2", "LEGACY_ASSETS_FORBIDDEN"))

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if key == "pics":
                    errors.append(issue(child_path, "The legacy pics field is not supported", "LEGACY_PICS_FORBIDDEN"))
                visit(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(data, "$")
    return errors


def semantic_checks(data: Any) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not isinstance(data, dict):
        return errors
    request = data.get("request")
    if isinstance(request, dict):
        requirement = request.get("user_requirement")
        if isinstance(requirement, str) and not requirement.strip():
            errors.append(issue("$.request.user_requirement", "user_requirement must contain non-whitespace text", "EMPTY_USER_REQUIREMENT"))
    layout = data.get("layout_reference_analysis")
    if isinstance(layout, dict):
        if layout.get("schema_version") != "0.1":
            errors.append(issue("$.layout_reference_analysis.schema_version", "A final analysis must use schema_version 0.1", "INVALID_LAYOUT_SCHEMA_VERSION"))
        if layout.get("input_kind") != "layout_reference_screenshot":
            errors.append(issue("$.layout_reference_analysis.input_kind", "Expected an A layout-reference final analysis", "INVALID_LAYOUT_INPUT_KIND"))
    style = data.get("style_profile")
    if isinstance(style, dict) and style.get("schema_version") != "0.1":
        errors.append(issue("$.style_profile.schema_version", "B2 style profile must use schema_version 0.1", "INVALID_STYLE_SCHEMA_VERSION"))
    return errors


def recommendation_warnings(data: Any) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    request = data.get("request") if isinstance(data, dict) else None
    if not isinstance(request, dict):
        return warnings
    for field in ("orientation", "target_resolution"):
        if request.get(field) is None:
            warnings.append(issue(f"$.request.{field}", f"Optional target constraint is absent: {field}", "OPTIONAL_FIELD_ABSENT"))
    return warnings


def deduplicate(items: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        key = (item["path"], item["message"], item["code"])
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def validate_document(data: Any) -> tuple[list[dict[str, str]], list[dict[str, str]], str, bool, str | None]:
    input_schema, upstream = load_contracts()
    available, schema_errors, setup_error = validate_with_jsonschema(data, input_schema, upstream)
    if setup_error:
        return [issue("$", setup_error, "VALIDATOR_SETUP_ERROR")], [], "jsonschema_draft_2020_12", False, None
    if available:
        mode = "jsonschema_draft_2020_12"
        full = True
        notice = None
    else:
        mode = "limited_local"
        full = False
        notice = (
            "The jsonschema dependency is unavailable. Validation used the bundled local "
            "validator over the Composer v2 contract and both authoritative upstream schemas; "
            "it is not a general-purpose Draft 2020-12 implementation."
        )
        schema_errors = LimitedLocalValidator(input_schema, upstream).validate(data)
    errors = deduplicate([*schema_errors, *legacy_errors(data), *semantic_checks(data)])
    return errors, recommendation_warnings(data), mode, full, notice


def main() -> int:
    if len(sys.argv) != 2:
        emit(
            status="error",
            mode="not_started",
            full_draft_validation=False,
            errors=[issue("$", "Usage: python scripts/validate_input.py <input.json>", "USAGE_ERROR")],
            warnings=[],
        )
        return 1

    input_path = Path(sys.argv[1])
    if not input_path.is_file():
        emit(
            status="error",
            mode="not_started",
            full_draft_validation=False,
            errors=[issue("$", f"Input file not found: {input_path}", "INPUT_FILE_NOT_FOUND")],
            warnings=[],
        )
        return 1

    try:
        data = load_json(input_path)
        errors, warnings, mode, full, notice = validate_document(data)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        emit(
            status="error",
            mode="not_started",
            full_draft_validation=False,
            errors=[issue("$", f"Unable to read JSON contract data: {exc}", "JSON_READ_ERROR")],
            warnings=[],
        )
        return 2

    emit(
        status="valid" if not errors else "error",
        mode=mode,
        full_draft_validation=full,
        errors=errors,
        warnings=warnings,
        data=data,
        notice=notice,
    )
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
