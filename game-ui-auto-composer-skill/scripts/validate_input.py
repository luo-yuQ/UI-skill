#!/usr/bin/env python3
"""Validate ui-compose-input JSON without resolving source_ref values.

The validator prefers the third-party ``jsonschema`` package for full Draft
2020-12 validation. If that dependency is unavailable, it uses an explicitly
limited local validator covering the keywords currently used by the bundled
ui-compose-input and asset-analysis schemas. The fallback must not be described
as complete Draft 2020-12 validation.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
INPUT_SCHEMA_PATH = REPO_ROOT / "schemas" / "ui-compose-input.schema.json"
ASSET_SCHEMA_PATH = REPO_ROOT / "asset-analysis.schema.json"
IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def issue(path: str, message: str, code: str) -> dict[str, str]:
    return {"path": path, "message": message, "code": code}


def emit(
    *,
    status: str,
    mode: str,
    full_draft_validation: bool,
    errors: list[dict[str, str]],
    warnings: list[dict[str, str]],
    asset_count: int = 0,
    page_count: int = 0,
    notice: str | None = None,
) -> None:
    validator_info: dict[str, Any] = {
        "mode": mode,
        "full_draft_2020_12": full_draft_validation,
    }
    if notice:
        validator_info["notice"] = notice

    result = {
        "status": status,
        "validator": validator_info,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "asset_count": asset_count,
            "page_count": page_count,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def json_path(parts: Any) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path


def validate_with_jsonschema(
    data: Any,
    input_schema: dict[str, Any],
    asset_schema: dict[str, Any],
) -> tuple[bool, list[dict[str, str]], str | None]:
    """Return (available, errors, setup_error)."""

    try:
        from jsonschema import Draft202012Validator
    except ModuleNotFoundError:
        return False, [], None

    try:
        Draft202012Validator.check_schema(asset_schema)
        Draft202012Validator.check_schema(input_schema)

        try:
            from referencing import Registry, Resource

            registry = Registry().with_resource(
                asset_schema["$id"], Resource.from_contents(asset_schema)
            )
            validator = Draft202012Validator(input_schema, registry=registry)
        except (ImportError, TypeError):
            # Compatibility path for older jsonschema releases.
            from jsonschema import RefResolver

            resolver = RefResolver.from_schema(
                input_schema,
                store={asset_schema["$id"]: asset_schema},
            )
            validator = Draft202012Validator(input_schema, resolver=resolver)

        errors = [
            issue(
                json_path(error.absolute_path),
                error.message,
                "SCHEMA_VALIDATION_ERROR",
            )
            for error in sorted(
                validator.iter_errors(data),
                key=lambda item: json_path(item.absolute_path),
            )
        ]
        return True, errors, None
    except Exception as exc:  # pragma: no cover - depends on installed package version
        return True, [], f"Unable to initialize Draft 2020-12 validation: {exc}"


class LimitedLocalValidator:
    """Validate only the JSON Schema keywords used by the current contracts."""

    def __init__(
        self,
        input_schema: dict[str, Any],
        asset_schema: dict[str, Any],
    ) -> None:
        self.input_schema = input_schema
        self.registry = {
            "ui-compose-input.schema.json": input_schema,
            input_schema.get("$id", ""): input_schema,
            "asset-analysis.schema.json": asset_schema,
            asset_schema.get("$id", ""): asset_schema,
        }

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
            "number": lambda item: isinstance(item, (int, float))
            and not isinstance(item, bool),
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
                errors.append(
                    issue(path, "Value does not match any allowed schema branch", "ANY_OF_MISMATCH")
                )
                return

        if "const" in schema and value != schema["const"]:
            errors.append(
                issue(path, f"Expected constant value {schema['const']!r}", "CONST_MISMATCH")
            )

        if "enum" in schema and value not in schema["enum"]:
            errors.append(issue(path, "Value is not in the allowed enum", "ENUM_MISMATCH"))

        expected = schema.get("type")
        if expected is not None:
            choices = expected if isinstance(expected, list) else [expected]
            if not any(self._matches_type(value, choice) for choice in choices):
                errors.append(
                    issue(path, f"Expected type {choices}, got {type(value).__name__}", "TYPE_MISMATCH")
                )
                return

        if isinstance(value, dict):
            required = schema.get("required", [])
            for field in required:
                if field not in value:
                    errors.append(
                        issue(f"{path}.{field}", "Missing required field", "MISSING_REQUIRED_FIELD")
                    )

            properties = schema.get("properties", {})
            if schema.get("additionalProperties") is False:
                for field in sorted(set(value) - set(properties)):
                    errors.append(
                        issue(f"{path}.{field}", "Unexpected field", "ADDITIONAL_PROPERTY")
                    )

            for field, child in value.items():
                if field in properties:
                    self._validate_node(
                        child,
                        properties[field],
                        root,
                        f"{path}.{field}",
                        errors,
                    )

        if isinstance(value, list):
            if len(value) < schema.get("minItems", 0):
                errors.append(issue(path, "Array has fewer than minItems", "MIN_ITEMS"))
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                errors.append(issue(path, "Array has more than maxItems", "MAX_ITEMS"))
            if schema.get("uniqueItems"):
                normalized = [
                    json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value
                ]
                if len(normalized) != len(set(normalized)):
                    errors.append(issue(path, "Array items must be unique", "UNIQUE_ITEMS"))
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for index, child in enumerate(value):
                    self._validate_node(
                        child,
                        item_schema,
                        root,
                        f"{path}[{index}]",
                        errors,
                    )

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
                errors.append(
                    issue(path, "Number is not above exclusiveMinimum", "EXCLUSIVE_MINIMUM")
                )
            if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
                errors.append(
                    issue(path, "Number is not below exclusiveMaximum", "EXCLUSIVE_MAXIMUM")
                )


def find_forbidden_pics(value: Any, path: str = "$") -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == "pics":
                errors.append(
                    issue(child_path, "The legacy pics field is not supported", "LEGACY_PICS_FORBIDDEN")
                )
            errors.extend(find_forbidden_pics(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(find_forbidden_pics(child, f"{path}[{index}]"))
    return errors


def semantic_checks(data: Any) -> tuple[list[dict[str, str]], int, int]:
    errors: list[dict[str, str]] = []
    if not isinstance(data, dict):
        return errors, 0, 0

    assets = data.get("assets")
    asset_count = len(assets) if isinstance(assets, list) else 0
    asset_ids: list[str] = []
    if isinstance(assets, list):
        for index, item in enumerate(assets):
            if not isinstance(item, dict):
                continue
            analysis = item.get("asset_analysis")
            if not isinstance(analysis, dict):
                continue
            asset_id = analysis.get("asset_id")
            if not isinstance(asset_id, str) or not asset_id.strip():
                errors.append(
                    issue(
                        f"$.assets[{index}].asset_analysis.asset_id",
                        "asset_id must be a non-empty string",
                        "INVALID_ASSET_ID",
                    )
                )
            elif not IDENTIFIER_PATTERN.fullmatch(asset_id):
                errors.append(
                    issue(
                        f"$.assets[{index}].asset_analysis.asset_id",
                        "asset_id must match the identifier pattern",
                        "INVALID_ASSET_ID",
                    )
                )
            else:
                asset_ids.append(asset_id)

    duplicates = sorted({asset_id for asset_id in asset_ids if asset_ids.count(asset_id) > 1})
    for asset_id in duplicates:
        errors.append(
            issue(
                "$.assets",
                f"Duplicate asset_id: {asset_id}",
                "DUPLICATE_ASSET_ID",
            )
        )

    request = data.get("request")
    page_count = 0
    if isinstance(request, dict):
        page_request = request.get("page_request")
        if isinstance(page_request, dict):
            pages = page_request.get("pages")
            page_ids: list[str] = []
            if isinstance(pages, list):
                page_count = len(pages)
                for page in pages:
                    if isinstance(page, dict) and isinstance(page.get("page_id"), str):
                        page_ids.append(page["page_id"])
            duplicate_pages = sorted(
                {page_id for page_id in page_ids if page_ids.count(page_id) > 1}
            )
            for page_id in duplicate_pages:
                errors.append(
                    issue(
                        "$.request.page_request.pages",
                        f"Duplicate page_id: {page_id}",
                        "DUPLICATE_PAGE_ID",
                    )
                )

            primary_page_id = page_request.get("primary_page_id")
            if isinstance(primary_page_id, str) and primary_page_id not in page_ids:
                errors.append(
                    issue(
                        "$.request.page_request.primary_page_id",
                        "primary_page_id must match one of page_request.pages[].page_id",
                        "UNKNOWN_PRIMARY_PAGE_ID",
                    )
                )

    return errors, asset_count, page_count


def recommendation_warnings(data: Any) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if not isinstance(data, dict):
        return warnings
    request = data.get("request")
    if not isinstance(request, dict):
        return warnings
    game_description = request.get("game_description")
    if isinstance(game_description, dict):
        for field in ("core_interaction", "audience"):
            if not game_description.get(field):
                warnings.append(
                    issue(
                        f"$.request.game_description.{field}",
                        f"Recommended field is missing or empty: {field}",
                        "RECOMMENDED_FIELD_MISSING",
                    )
                )
        if not game_description.get("style_keywords"):
            warnings.append(
                issue(
                    "$.request.game_description.style_keywords",
                    "Recommended field is missing or empty: style_keywords",
                    "RECOMMENDED_FIELD_MISSING",
                )
            )
    for field in ("constraints", "visual_preferences"):
        if field not in request:
            warnings.append(
                issue(
                    f"$.request.{field}",
                    f"Optional planning field is absent: {field}",
                    "OPTIONAL_FIELD_ABSENT",
                )
            )
    return warnings


def deduplicate(issues: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in issues:
        key = (item["path"], item["message"], item["code"])
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


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
    if not input_path.exists() or not input_path.is_file():
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
        input_schema = load_json(INPUT_SCHEMA_PATH)
        asset_schema = load_json(ASSET_SCHEMA_PATH)
    except (OSError, json.JSONDecodeError) as exc:
        emit(
            status="error",
            mode="not_started",
            full_draft_validation=False,
            errors=[issue("$", f"Unable to read JSON contract data: {exc}", "JSON_READ_ERROR")],
            warnings=[],
        )
        return 2

    available, schema_errors, setup_error = validate_with_jsonschema(
        data, input_schema, asset_schema
    )
    if setup_error:
        emit(
            status="error",
            mode="jsonschema_draft_2020_12",
            full_draft_validation=False,
            errors=[issue("$", setup_error, "VALIDATOR_SETUP_ERROR")],
            warnings=[],
        )
        return 2

    if available:
        mode = "jsonschema_draft_2020_12"
        full_draft_validation = True
        notice = None
    else:
        mode = "limited_local"
        full_draft_validation = False
        notice = (
            "The jsonschema dependency is unavailable. Validation used a limited local "
            "implementation covering only the keywords currently present in the bundled "
            "input contracts; it is not full Draft 2020-12 validation."
        )
        schema_errors = LimitedLocalValidator(input_schema, asset_schema).validate(data)

    errors = list(schema_errors)
    errors.extend(find_forbidden_pics(data))
    semantic_errors, asset_count, page_count = semantic_checks(data)
    errors.extend(semantic_errors)
    errors = deduplicate(errors)
    warnings = recommendation_warnings(data)

    status = "valid" if not errors else "error"
    emit(
        status=status,
        mode=mode,
        full_draft_validation=full_draft_validation,
        errors=errors,
        warnings=warnings,
        asset_count=asset_count,
        page_count=page_count,
        notice=notice,
    )
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
