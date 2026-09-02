#!/usr/bin/env python3
"""Execute a provider-specific ToAPIs preview generation request.

The adapter consumes preview-request.json, resolves its ordered references,
submits the confirmed ToAPIs protocol, polls the asynchronous task, and writes
the downloaded image plus a redacted result summary. Use --dry-run to perform
all local validation and path resolution without network access.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path, PureWindowsPath
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - exercised only without dependency
    requests = None  # type: ignore[assignment]


PROVIDER = "ToAPIs"
DEFAULT_BASE_URL = "https://ai-api.youchu.work"
MODEL = "gpt-image-2"
IMAGE_TYPE = "image"
IMAGE_SIZE = "1024x1536"
IMAGE_COUNT = 1
RESPONSE_FORMAT = "url"

EXIT_INPUT = 2
EXIT_UPLOAD = 3
EXIT_SUBMIT = 4
EXIT_POLL = 5
EXIT_RESULT = 6
EXIT_OUTPUT = 7
EXIT_CONFIG = 8


class ToApisAdapterError(RuntimeError):
    """Expected adapter failure with a stable stage, code, and exit status."""

    def __init__(
        self,
        stage: str,
        error_code: str,
        message: str,
        *,
        task_id: str | None = None,
        exit_code: int = EXIT_INPUT,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.error_code = error_code
        self.message = message
        self.task_id = task_id
        self.exit_code = exit_code

    def summary(self) -> dict[str, Any]:
        return {
            "success": False,
            "provider": PROVIDER,
            "stage": self.stage,
            "error_code": self.error_code,
            "message": self.message,
            "task_id": self.task_id,
        }


def is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def provider_url(base_url: str, value: str) -> str:
    """Preserve absolute public URLs and resolve only relative provider paths."""

    if is_http_url(value):
        return value
    return urljoin(base_url.rstrip("/") + "/", value.lstrip("/"))


# ---------------------------------------------------------------------------
# Image-generation result protocol dispatch.
#
# Protocols are selected from the CREATE response shape, never from the model
# name: a task_id in the response does not by itself imply an async protocol.
#
# - "openai_images_sync": the create response already carries the final image
#   result (for example data[0].url, data[0].b64_json, items[0].url). Polling
#   is forbidden for this protocol.
# - "toapis_async": the create response only enqueues a task (gpt-image-2
#   shape: task_id + task_status_url, data is a task object, not a list).
# ---------------------------------------------------------------------------
SYNC_RESULT_PROTOCOL = "openai_images_sync"
ASYNC_TASK_PROTOCOL = "toapis_async"

_SYNC_RESULT_CANDIDATE_PATHS = (
    ("items", ("items",)),
    ("data.result.data", ("data", "result", "data")),
    ("data.data", ("data", "data")),
    ("data", ("data",)),
    ("images", ("images",)),
)

_SYNC_BASE64_FIELDS = ("b64_json", "base64", "image_base64")


def _looks_like_image_item(value: Any) -> bool:
    if isinstance(value, str):
        return is_http_url(value) or value.startswith("data:image/") or bool(value.strip())
    if isinstance(value, dict):
        if is_http_url(value.get("url")):
            return True
        for field in _SYNC_BASE64_FIELDS:
            if isinstance(value.get(field), str) and value[field].strip():
                return True
        nested = value.get("image")
        if isinstance(nested, dict):
            return _looks_like_image_item(nested)
    return False


def extract_sync_image_items(response: Any) -> list[Any] | None:
    """Return the direct image result array from a create response, or None.

    Only unambiguous final-result shapes are accepted; dict-valued data such
    as the ToAPIs async task object never matches.
    """

    if not isinstance(response, dict):
        return None

    if is_http_url(response.get("url")):
        return [{"url": response["url"]}]

    for field in _SYNC_BASE64_FIELDS:
        value = response.get(field)
        if isinstance(value, str) and value.strip():
            return [{field: value}]

    for _, path in _SYNC_RESULT_CANDIDATE_PATHS:
        current: Any = response
        for key in path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        if (
            isinstance(current, list)
            and current
            and any(_looks_like_image_item(item) for item in current)
        ):
            return current

    return None


def detect_result_protocol(create_response: Any) -> str:
    """Classify a create response into a centralized protocol identifier."""

    if extract_sync_image_items(create_response) is not None:
        return SYNC_RESULT_PROTOCOL
    return ASYNC_TASK_PROTOCOL


def load_preview_request(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise ToApisAdapterError(
            "input", "REQUEST_FILE_NOT_FOUND", f"Preview request file not found: {path}"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ToApisAdapterError(
            "input", "REQUEST_JSON_INVALID", f"Invalid preview request JSON: {exc.msg}"
        ) from exc
    except OSError as exc:
        raise ToApisAdapterError(
            "input", "REQUEST_READ_ERROR", f"Unable to read preview request: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ToApisAdapterError(
            "validate", "REQUEST_TYPE_INVALID", "Preview request must be a JSON object"
        )
    return data


def _require_object(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ToApisAdapterError(
            "validate", "REQUIRED_FIELD_INVALID", f"{key} must be an object"
        )
    return value


def _require_list(parent: dict[str, Any], key: str) -> list[Any]:
    value = parent.get(key)
    if not isinstance(value, list):
        raise ToApisAdapterError(
            "validate", "REQUIRED_FIELD_INVALID", f"{key} must be an array"
        )
    return value


def _non_empty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_preview_request(data: dict[str, Any]) -> list[dict[str, Any]]:
    if not _non_empty(data.get("schema_version")):
        raise ToApisAdapterError(
            "validate", "SCHEMA_VERSION_MISSING", "schema_version must be a non-empty string"
        )
    source = _require_object(data, "source")
    if not _non_empty(source.get("page_id")):
        raise ToApisAdapterError(
            "validate", "PAGE_ID_MISSING", "source.page_id must be a non-empty string"
        )
    if not _non_empty(source.get("project_name")):
        raise ToApisAdapterError(
            "validate", "PROJECT_NAME_MISSING", "source.project_name is required for Prompt generation"
        )
    _require_object(data, "generation_intent")
    composition = _require_list(data, "composition_requirements")
    _require_list(data, "preserve_requirements")
    _require_list(data, "avoid")
    _require_object(data, "output_spec")
    if not any(_non_empty(item) for item in composition):
        raise ToApisAdapterError(
            "validate",
            "PURPOSE_MISSING",
            "composition_requirements must contain a page purpose",
        )

    references = _require_list(data, "reference_assets")
    orders: list[int] = []
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(references):
        if not isinstance(item, dict):
            raise ToApisAdapterError(
                "validate", "REFERENCE_ASSET_INVALID", f"reference_assets[{index}] must be an object"
            )
        order = item.get("order")
        if not isinstance(order, int) or isinstance(order, bool) or order < 1:
            raise ToApisAdapterError(
                "validate", "REFERENCE_ORDER_INVALID", f"reference_assets[{index}].order is invalid"
            )
        for field in ("asset_id", "usage"):
            if not _non_empty(item.get(field)):
                raise ToApisAdapterError(
                    "validate",
                    "REFERENCE_ASSET_INVALID",
                    f"reference_assets[{index}].{field} must be a non-empty string",
                )
        source_ref = item.get("source_ref")
        if not isinstance(source_ref, dict):
            raise ToApisAdapterError(
                "validate",
                "SOURCE_REF_INVALID",
                f"reference_assets[{index}].source_ref must be an object",
            )
        if not _non_empty(source_ref.get("ref_type")) or not _non_empty(source_ref.get("value")):
            raise ToApisAdapterError(
                "validate",
                "SOURCE_REF_INVALID",
                f"reference_assets[{index}].source_ref requires ref_type and value",
            )
        orders.append(order)
        normalized.append(item)

    normalized.sort(key=lambda item: item["order"])
    expected = list(range(1, len(normalized) + 1))
    if sorted(orders) != expected:
        raise ToApisAdapterError(
            "validate",
            "REFERENCE_ORDER_INVALID",
            "reference_assets.order must be unique and consecutive starting at 1",
        )
    return normalized


def _ascii_clause(value: Any) -> str:
    """Extract a concise English/ASCII clause from mixed-language plan text."""

    if not isinstance(value, str):
        return ""
    candidates = re.findall(r"[A-Za-z][\x20-\x7E]*", value)
    if not candidates:
        return ""
    text = max(candidates, key=len).strip(" :-.;")
    return re.sub(r"\s+", " ", text)


def _extract_purpose(data: dict[str, Any]) -> str:
    for item in data.get("composition_requirements", []):
        if not isinstance(item, str):
            continue
        lower = item.lower()
        if "purpose" in lower or "用途" in item:
            clause = _ascii_clause(item)
            if clause:
                return clause
    description = data.get("generation_intent", {}).get("description")
    clause = _ascii_clause(description)
    if clause:
        return clause
    raise ToApisAdapterError(
        "validate", "PURPOSE_MISSING", "Unable to derive an English page purpose"
    )


def _role_for_reference(reference: dict[str, Any]) -> str:
    text = f"{reference.get('asset_id', '')} {reference.get('usage', '')}".lower()
    if "background" in text or "_bg" in text:
        return "Full-page background"
    if "button" in text:
        if "login" in text or "primary" in text:
            return "Primary login button"
        return "Planned UI button"
    usage = _ascii_clause(reference.get("usage"))
    return usage or "Planned reference asset"


def _preserve_for_reference(data: dict[str, Any], order: int) -> list[str]:
    markers = (f"参考图 {order}", f"Reference image {order}", f"Reference asset {order}")
    values: list[str] = []
    for item in data.get("preserve_requirements", []):
        if not isinstance(item, str) or not any(marker in item for marker in markers):
            continue
        clause = _ascii_clause(item)
        if clause and clause not in values:
            values.append(clause)
    if not values:
        values.append("Preserve the asset identity, core silhouette, and planned visual role")
    return values[:4]


def _layout_lines(data: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for item in data.get("composition_requirements", []):
        if not isinstance(item, str) or not ("布局" in item or "layout" in item.lower()):
            continue
        lower = item.lower()
        if "page root" in lower:
            continue
        if "background" in lower and "canvas" in lower and "stretch" in lower:
            line = "Background fills the entire canvas."
        elif ("button" in lower or "action" in lower) and "bottom center" in lower:
            match = re.search(r"0\.(\d+)\s*x", lower)
            if match:
                percent = int(round(float(f"0.{match.group(1)}") * 100))
                line = f"Login button is positioned at bottom center and is about {percent}% of its parent width."
            else:
                line = "Login button is positioned at bottom center."
        else:
            clause = _ascii_clause(item)
            if not clause:
                continue
            line = clause if clause.endswith(".") else clause + "."
        if line not in lines:
            lines.append(line)
    if not lines:
        lines.append("Follow the planned page composition and relative placement.")
    return lines[:6]


def build_image_prompt(data: dict[str, Any], references: list[dict[str, Any]]) -> str:
    source = data["source"]
    project = str(source["project_name"]).strip()
    page = str(source["page_id"]).replace("_", " ").title()
    purpose = _extract_purpose(data)
    lines = ["Project:", project, "", "Page:", page, "", "Purpose:", purpose, ""]
    lines.append("Reference assets:")
    if references:
        for reference in references:
            order = reference["order"]
            lines.extend(
                [
                    f"{order}. {reference['asset_id']}",
                    f"   Role: {_role_for_reference(reference)}",
                    "   Preserve:",
                ]
            )
            lines.extend(f"   - {item}" for item in _preserve_for_reference(data, order))
            lines.append("")
    else:
        lines.extend(["None. Create from the approved composition requirements only.", ""])

    lines.append("Layout:")
    lines.extend(f"- {item}" for item in _layout_lines(data))
    if sum("button" in _role_for_reference(item).lower() for item in references) == 1:
        lines.append("- Only one interactive button is allowed.")

    lines.extend(
        [
            "",
            "Constraints:",
            "- Do not add account forms.",
            "- Do not add social login buttons.",
            "- Do not add menus.",
            "- Do not add extra UI panels.",
            "- Do not add buttons, features, or page regions that are not present in the plan.",
            "- Do not turn clicks, navigation, or loading behavior into extra visible controls.",
            "- This is a concept preview, not an engineering screenshot.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def normalize_source_ref(source_ref: dict[str, Any]) -> str:
    """Return a stable serialization used only as a per-run resolution cache key."""

    return json.dumps(source_ref, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def resolve_local_path(value: str, asset_root: Path | None) -> Path:
    candidate = Path(value)
    windows_absolute = PureWindowsPath(value).is_absolute()
    if not candidate.is_absolute() and not windows_absolute:
        if asset_root is None:
            raise ToApisAdapterError(
                "resolve_assets",
                "ASSET_ROOT_REQUIRED",
                f"Relative local asset requires --asset-root: {value}",
            )
        candidate = asset_root / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:
        raise ToApisAdapterError(
            "resolve_assets", "LOCAL_ASSET_PATH_INVALID", f"Unable to resolve local asset path: {exc}"
        ) from exc
    if not resolved.exists() or not resolved.is_file():
        raise ToApisAdapterError(
            "resolve_assets", "LOCAL_ASSET_NOT_FOUND", f"Local asset not found: {resolved}"
        )
    return resolved


def _authorization(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def _response_json(response: Any, stage: str, code: str, exit_code: int) -> dict[str, Any]:
    try:
        response.raise_for_status()
    except Exception as exc:
        status = getattr(response, "status_code", "unknown")
        raise ToApisAdapterError(
            stage, code, f"Provider HTTP request failed with status {status}", exit_code=exit_code
        ) from exc
    try:
        data = response.json()
    except (ValueError, TypeError) as exc:
        raise ToApisAdapterError(
            stage, f"{code}_INVALID_JSON", "Provider response was not valid JSON", exit_code=exit_code
        ) from exc
    if not isinstance(data, dict):
        raise ToApisAdapterError(
            stage, f"{code}_INVALID_BODY", "Provider response must be a JSON object", exit_code=exit_code
        )
    return data


def upload_image(
    image_path: Path,
    *,
    base_url: str,
    api_key: str,
    timeout: float,
    session: Any,
) -> str:
    upload_url = provider_url(base_url, "/api/upload")
    try:
        with image_path.open("rb") as file:
            response = session.post(
                upload_url,
                headers=_authorization(api_key),
                files={"file": (image_path.name, file)},
                timeout=timeout,
            )
    except OSError as exc:
        raise ToApisAdapterError(
            "upload", "UPLOAD_FILE_READ_ERROR", f"Unable to read local asset: {image_path}", exit_code=EXIT_UPLOAD
        ) from exc
    except Exception as exc:
        raise ToApisAdapterError(
            "upload", "UPLOAD_REQUEST_ERROR", f"Upload request failed: {exc}", exit_code=EXIT_UPLOAD
        ) from exc
    data = _response_json(response, "upload", "UPLOAD_HTTP_ERROR", EXIT_UPLOAD)
    value = data.get("url")
    if not _non_empty(value):
        raise ToApisAdapterError(
            "upload", "UPLOAD_URL_MISSING", "Upload response did not contain data['url']", exit_code=EXIT_UPLOAD
        )
    resolved = provider_url(base_url, value)
    if not is_http_url(resolved):
        raise ToApisAdapterError(
            "upload", "UPLOAD_URL_INVALID", "Upload response URL is invalid", exit_code=EXIT_UPLOAD
        )
    return resolved


def resolve_reference_assets(
    references: list[dict[str, Any]],
    *,
    asset_root: Path | None,
    base_url: str,
    api_key: str | None,
    upload_timeout: float,
    dry_run: bool,
    session: Any = None,
    protected_output_paths: set[Path] | None = None,
) -> list[dict[str, Any]]:
    cache: dict[str, tuple[str, str]] = {}
    resolved: list[dict[str, Any]] = []
    for reference in references:
        source_ref = reference["source_ref"]
        key = normalize_source_ref(source_ref)
        if key in cache:
            source_kind, resolved_url = cache[key]
        else:
            value = source_ref["value"].strip()
            ref_type = source_ref["ref_type"]
            if is_http_url(value):
                source_kind, resolved_url = "public_url", value
            elif ref_type == "workspace_path":
                local_path = resolve_local_path(value, asset_root)
                if protected_output_paths and local_path in protected_output_paths:
                    raise ToApisAdapterError(
                        "resolve_assets",
                        "PATH_CONFLICT",
                        f"Output path must not overwrite local reference asset: {local_path}",
                    )
                source_kind = "local_upload"
                if dry_run:
                    resolved_url = f"<local-upload-required:{local_path.name}>"
                else:
                    if not api_key:
                        raise ToApisAdapterError(
                            "configuration", "TOAPIS_API_KEY_MISSING", "TOAPIS_API_KEY is required", exit_code=EXIT_CONFIG
                        )
                    resolved_url = upload_image(
                        local_path,
                        base_url=base_url,
                        api_key=api_key,
                        timeout=upload_timeout,
                        session=session,
                    )
            else:
                raise ToApisAdapterError(
                    "resolve_assets",
                    "UNSUPPORTED_SOURCE_REF",
                    f"Unsupported source_ref type: {ref_type}",
                )
            cache[key] = (source_kind, resolved_url)
        resolved.append(
            {
                "order": reference["order"],
                "asset_id": reference["asset_id"],
                "source_kind": source_kind,
                "resolved_url": resolved_url,
            }
        )
    return resolved


def build_generation_payload(prompt: str, resolved_assets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "model": MODEL,
        "prompt": prompt,
        "type": IMAGE_TYPE,
        "images": [item["resolved_url"] for item in resolved_assets],
        "size": IMAGE_SIZE,
        "n": IMAGE_COUNT,
        "response_format": RESPONSE_FORMAT,
    }


def submit_generation(
    payload: dict[str, Any], *, base_url: str, api_key: str, timeout: float, session: Any
) -> dict[str, Any]:
    try:
        response = session.post(
            provider_url(base_url, "/v1/images/generations"),
            headers={**_authorization(api_key), "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
    except Exception as exc:
        raise ToApisAdapterError(
            "submit", "SUBMIT_REQUEST_ERROR", f"Generation submission failed: {exc}", exit_code=EXIT_SUBMIT
        ) from exc
    data = _response_json(response, "submit", "SUBMIT_HTTP_ERROR", EXIT_SUBMIT)
    task_id = data.get("task_id")
    if data.get("success") is False or not _non_empty(task_id):
        raise ToApisAdapterError(
            "submit", "SUBMIT_TASK_ID_MISSING", "Generation response did not contain a task_id", exit_code=EXIT_SUBMIT
        )
    return data


def _positive_number(value: Any, fallback: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return fallback


def poll_task_status(
    task_id: str,
    submit_data: dict[str, Any],
    *,
    base_url: str,
    api_key: str,
    poll_interval: float,
    max_wait: float,
    timeout: float,
    session: Any,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    debug_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    interval = _positive_number(submit_data.get("poll_interval"), poll_interval)
    wait_limit = _positive_number(submit_data.get("max_wait"), max_wait)
    status_path = submit_data.get("task_status_url") or f"/v1/tasks/{task_id}/status"
    status_url = provider_url(base_url, str(status_path))
    if debug_info is not None:
        debug_info["polling_url"] = status_url
    started = monotonic_fn()
    while True:
        try:
            response = session.get(status_url, headers=_authorization(api_key), timeout=timeout)
        except Exception as exc:
            raise ToApisAdapterError(
                "poll", "POLL_REQUEST_ERROR", f"Task status request failed: {exc}", task_id=task_id, exit_code=EXIT_POLL
            ) from exc
        data = _response_json(response, "poll", "POLL_HTTP_ERROR", EXIT_POLL)
        status = data.get("task_status") or data.get("status")
        if status == "completed":
            return data
        if status == "failed":
            raise ToApisAdapterError(
                "poll", "TASK_FAILED", "Provider task reported failed", task_id=task_id, exit_code=EXIT_POLL
            )
        if status not in {"pending", "in_progress"}:
            raise ToApisAdapterError(
                "poll", "TASK_STATUS_UNKNOWN", f"Unknown task status: {status}", task_id=task_id, exit_code=EXIT_POLL
            )
        elapsed = monotonic_fn() - started
        if elapsed >= wait_limit:
            raise ToApisAdapterError(
                "poll", "POLL_TIMEOUT", f"Task exceeded maximum wait of {wait_limit:g} seconds", task_id=task_id, exit_code=EXIT_POLL
            )
        sleep_fn(min(interval, max(0.0, wait_limit - elapsed)))


def extract_image_url(result: dict[str, Any]) -> str | None:
    try:
        value = result["items"][0]["url"]
        if is_http_url(value):
            return value
    except (KeyError, IndexError, TypeError):
        pass
    try:
        value = result["data"]["result"]["data"][0]["url"]
        if is_http_url(value):
            return value
    except (KeyError, IndexError, TypeError):
        pass
    return None


def fetch_task_result(
    task_id: str, *, base_url: str, api_key: str, timeout: float, session: Any
) -> tuple[dict[str, Any], str]:
    try:
        response = session.get(
            provider_url(base_url, f"/v1/tasks/{task_id}/result"),
            headers=_authorization(api_key),
            timeout=timeout,
        )
    except Exception as exc:
        raise ToApisAdapterError(
            "fetch_result", "RESULT_REQUEST_ERROR", f"Task result request failed: {exc}", task_id=task_id, exit_code=EXIT_RESULT
        ) from exc
    data = _response_json(response, "fetch_result", "RESULT_HTTP_ERROR", EXIT_RESULT)
    image_url = extract_image_url(data)
    if image_url is None:
        raise ToApisAdapterError(
            "fetch_result",
            "RESULT_IMAGE_URL_MISSING",
            "Completed task result did not contain a valid image URL",
            task_id=task_id,
            exit_code=EXIT_RESULT,
        )
    return data, image_url


def download_image(image_url: str, output_path: Path, *, timeout: float, session: Any) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        response = session.get(image_url, stream=True, timeout=timeout)
        response.raise_for_status()
        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, dir=output_path.parent, prefix=f".{output_path.name}.", suffix=".part"
        ) as file:
            temporary = Path(file.name)
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    file.write(chunk)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, output_path)
        temporary = None
    except Exception as exc:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise ToApisAdapterError(
            "download", "IMAGE_DOWNLOAD_ERROR", f"Image download or write failed: {exc}", exit_code=EXIT_OUTPUT
        ) from exc


def write_result_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False, dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        ) as file:
            temporary = Path(file.name)
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        temporary = None
    except Exception as exc:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise ToApisAdapterError(
            "output", "RESULT_JSON_WRITE_ERROR", f"Unable to write result JSON: {exc}", exit_code=EXIT_OUTPUT
        ) from exc


def emit_json_summary(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def validate_path_conflicts(request_path: Path, output_path: Path, result_path: Path) -> None:
    try:
        paths = {
            "request": request_path.resolve(strict=False),
            "output": output_path.resolve(strict=False),
            "result_json": result_path.resolve(strict=False),
        }
    except OSError as exc:
        raise ToApisAdapterError(
            "validate", "PATH_RESOLUTION_ERROR", f"Unable to normalize paths: {exc}"
        ) from exc
    pairs = (("request", "output"), ("request", "result_json"), ("output", "result_json"))
    for left, right in pairs:
        if paths[left] == paths[right]:
            raise ToApisAdapterError(
                "validate", "PATH_CONFLICT", f"{left} and {right} must refer to different paths"
            )


def result_path_is_safe(request_path: Path, output_path: Path, result_path: Path) -> bool:
    """Return whether failure metadata can be written without aliasing another role."""

    try:
        result = result_path.resolve(strict=False)
        return result not in {
            request_path.resolve(strict=False),
            output_path.resolve(strict=False),
        }
    except OSError:
        return False


def payload_summary(payload: dict[str, Any], *, include_images: bool = False) -> dict[str, Any]:
    result = {
        "model": payload["model"],
        "type": payload["type"],
        "size": payload["size"],
        "n": payload["n"],
        "response_format": payload["response_format"],
        "images_count": len(payload["images"]),
    }
    if include_images:
        result["images"] = list(payload["images"])
    return result


class AdapterArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        safe_message = re.sub(
            r"(--api-key(?:=|\s+))\S+", r"\1[REDACTED]", message, flags=re.IGNORECASE
        )
        raise ToApisAdapterError("input", "CLI_ARGUMENT_ERROR", safe_message)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = AdapterArgumentParser(description="Generate a UI preview through the confirmed ToAPIs protocol.")
    parser.add_argument("--request", required=True, help="Path to preview-request.json")
    parser.add_argument("--asset-root", help="Root used for relative workspace_path references")
    parser.add_argument("--output", required=True, help="Destination image path")
    parser.add_argument("--result-json", required=True, help="Destination result metadata JSON")
    parser.add_argument("--poll-interval", type=float, default=3.0)
    parser.add_argument("--max-wait", type=float, default=300.0)
    parser.add_argument("--upload-timeout", type=float, default=120.0)
    parser.add_argument("--request-timeout", type=float, default=120.0)
    parser.add_argument("--download-timeout", type=float, default=120.0)
    parser.add_argument("--dry-run", action="store_true", help="Validate and resolve locally without network access")
    return parser.parse_args(argv)


def _validate_cli_numbers(args: argparse.Namespace) -> None:
    for name in ("poll_interval", "max_wait", "upload_timeout", "request_timeout", "download_timeout"):
        if getattr(args, name) <= 0:
            raise ToApisAdapterError(
                "validate", "CLI_VALUE_INVALID", f"--{name.replace('_', '-')} must be greater than zero"
            )


def _ensure_runtime(base_url: str, api_key: str | None) -> None:
    if not is_http_url(base_url):
        raise ToApisAdapterError(
            "configuration", "TOAPIS_BASE_URL_INVALID", "TOAPIS_BASE_URL must be an HTTP(S) URL", exit_code=EXIT_CONFIG
        )
    if not api_key:
        raise ToApisAdapterError(
            "configuration", "TOAPIS_API_KEY_MISSING", "TOAPIS_API_KEY is required", exit_code=EXIT_CONFIG
        )
    if requests is None:
        raise ToApisAdapterError(
            "configuration",
            "REQUESTS_DEPENDENCY_MISSING",
            "The requests package is required; install it with: python -m pip install requests",
            exit_code=EXIT_CONFIG,
        )


def _redact_message(message: str, api_key: str | None) -> str:
    return message.replace(api_key, "[REDACTED]") if api_key else message


def run(args: argparse.Namespace, *, session: Any = None) -> tuple[int, dict[str, Any]]:
    request_path = Path(args.request)
    output_path = Path(args.output)
    result_path = Path(args.result_json)
    asset_root = Path(args.asset_root) if args.asset_root else None
    base_url = os.environ.get("TOAPIS_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    api_key = os.environ.get("TOAPIS_API_KEY")
    task_id: str | None = None
    try:
        _validate_cli_numbers(args)
        validate_path_conflicts(request_path, output_path, result_path)
        data = load_preview_request(request_path)
        references = validate_preview_request(data)
        prompt = build_image_prompt(data, references)
        if not args.dry_run:
            _ensure_runtime(base_url, api_key)
        active_session = session if session is not None else requests
        resolved = resolve_reference_assets(
            references,
            asset_root=asset_root,
            base_url=base_url,
            api_key=api_key,
            upload_timeout=args.upload_timeout,
            dry_run=args.dry_run,
            session=active_session,
            protected_output_paths={
                output_path.resolve(strict=False),
                result_path.resolve(strict=False),
            },
        )
        payload = build_generation_payload(prompt, resolved)
        if args.dry_run:
            summary = {
                "success": True,
                "dry_run": True,
                "provider": PROVIDER,
                "model": MODEL,
                "task_id": None,
                "task_status": "not_submitted",
                "request_file": str(request_path),
                "output_image": str(output_path),
                "image_url": None,
                "final_prompt": prompt,
                "reference_images": resolved,
                "payload_summary": payload_summary(payload, include_images=True),
            }
            write_result_json(result_path, summary)
            return 0, summary

        submit_data = submit_generation(
            payload,
            base_url=base_url,
            api_key=api_key or "",
            timeout=args.request_timeout,
            session=active_session,
        )
        task_id = submit_data["task_id"]
        poll_task_status(
            task_id,
            submit_data,
            base_url=base_url,
            api_key=api_key or "",
            poll_interval=args.poll_interval,
            max_wait=args.max_wait,
            timeout=args.request_timeout,
            session=active_session,
        )
        _, image_url = fetch_task_result(
            task_id,
            base_url=base_url,
            api_key=api_key or "",
            timeout=args.request_timeout,
            session=active_session,
        )
        download_image(image_url, output_path, timeout=args.download_timeout, session=active_session)
        summary = {
            "success": True,
            "provider": PROVIDER,
            "model": MODEL,
            "task_id": task_id,
            "task_status": "completed",
            "request_file": str(request_path),
            "output_image": str(output_path),
            "image_url": image_url,
            "final_prompt": prompt,
            "reference_images": resolved,
            "payload_summary": payload_summary(payload),
        }
        write_result_json(result_path, summary)
        return 0, summary
    except ToApisAdapterError as exc:
        exc.message = _redact_message(exc.message, api_key)
        if exc.task_id is None:
            exc.task_id = task_id
        summary = exc.summary()
        try:
            if result_path_is_safe(request_path, output_path, result_path):
                write_result_json(result_path, summary)
        except (OSError, ToApisAdapterError):
            summary["result_json_write_failed"] = True
        return exc.exit_code, summary
    except Exception as exc:  # defensive boundary: never expose a routine traceback
        summary = ToApisAdapterError(
            "internal",
            "UNEXPECTED_ADAPTER_ERROR",
            _redact_message(f"Unexpected adapter error: {exc}", api_key),
            task_id=task_id,
            exit_code=EXIT_OUTPUT,
        ).summary()
        try:
            if result_path_is_safe(request_path, output_path, result_path):
                write_result_json(result_path, summary)
        except (OSError, ToApisAdapterError):
            summary["result_json_write_failed"] = True
        return EXIT_OUTPUT, summary


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except ToApisAdapterError as exc:
        emit_json_summary(exc.summary())
        return exc.exit_code
    exit_code, summary = run(args)
    emit_json_summary(summary)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
