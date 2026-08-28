#!/usr/bin/env python3
"""PoC: request a matched text/clean game-UI pair from GPT Image 2.

This script is intentionally independent from the Stage0/Stage1/Stage2 flow. It
reuses the repository's verified ToAPIs upload and asynchronous image-generation
protocol, while preserving the request and provider responses for inspection.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import io
import json
import os
import tempfile
import time
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from PIL import Image, UnidentifiedImageError


EXPERIMENT = "single-request-pair"
REQUESTED_OUTPUT_COUNT = 2
UPLOAD_TIMEOUT = 120.0
REQUEST_TIMEOUT = 120.0
DOWNLOAD_TIMEOUT = 180.0
POLL_INTERVAL = 3.0
MAX_WAIT = 300.0

PAIR_PROMPT = """Generate a paired game UI design based on the provided reference image and user requirement.

Both outputs must represent the SAME UI design:

- same composition
- same panel geometry
- same character placement
- same icons
- same buttons
- same decorations
- same visual style
- same spacing and proportions

Output A is the presentation version:

- normal UI text is visible

Output B is the production clean version:

- remove ALL visible text
- remove ALL letters
- remove ALL numbers
- remove text shadows and text outlines
- preserve the underlying button, panel and background graphics
- do not remove icons or other visual assets

The clean version must NOT redesign the interface.
It should look like the exact same UI before text layers were added.

Return exactly two separate images in this one request: one Output A and one Output B.
Do not combine the two versions into a comparison sheet, diptych, collage, or single image.

User requirement:
{user_prompt}"""


class PairPocError(RuntimeError):
    """Expected input, configuration, provider, or output failure."""


def _load_repository_module(name: str, relative_path: str) -> ModuleType:
    repository_root = Path(__file__).resolve().parents[2]
    module_path = repository_root / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load repository helper: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# These are private module instances: the existing common clients are reused but
# never modified. generate_preview supplies the currently verified curl-based
# submit/poll/result transport; toapis_preview_adapter supplies upload/download.
provider = _load_repository_module(
    "_image2_pair_generate_preview",
    "game-ui-image-provider-adapter/scripts/generate_preview.py",
)
toapis = _load_repository_module(
    "_image2_pair_toapis_preview_adapter",
    "game-ui-auto-composer-skill/scripts/toapis_preview_adapter.py",
)

DEFAULT_MODEL = provider.DEFAULT_MODEL
SUPPORTED_SIZES = tuple(provider.SUPPORTED_SIZES)


def inspect_image(path: Path, label: str = "Image") -> tuple[int, int]:
    if not path.exists() or not path.is_file():
        raise PairPocError(f"{label} does not exist or is not a file: {path}")
    try:
        with Image.open(path) as image:
            size = image.size
            image.verify()
    except (OSError, UnidentifiedImageError) as exc:
        raise PairPocError(f"{label} is not a readable image: {path}") from exc
    if size[0] <= 0 or size[1] <= 0:
        raise PairPocError(f"{label} dimensions must be positive: {size[0]}x{size[1]}")
    return size


def select_provider_size(reference_size: tuple[int, int]) -> str:
    width, height = reference_size
    selected = (
        "1536x1024"
        if width > height
        else "1024x1536"
        if height > width
        else "1024x1024"
    )
    if selected not in SUPPORTED_SIZES:
        raise PairPocError(
            f"Project provider does not support the selected size {selected}; "
            f"supported sizes: {', '.join(SUPPORTED_SIZES)}"
        )
    return selected


def normalize_provider_base_url(configured_url: str) -> str:
    """Accept the usual OPENAI_BASE_URL forms without producing /v1/v1 paths."""

    normalized = configured_url.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise PairPocError("OPENAI_BASE_URL must be an absolute HTTP(S) URL")
    if parsed.query or parsed.fragment:
        raise PairPocError("OPENAI_BASE_URL must not contain a query or fragment")
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def build_prompt(user_prompt: str) -> str:
    requirement = user_prompt.strip()
    if not requirement:
        raise PairPocError("--prompt must not be empty")
    return PAIR_PROMPT.format(user_prompt=requirement)


def build_payload(
    *, model: str, prompt: str, reference_url: str, requested_size: str
) -> dict[str, Any]:
    return {
        "model": model,
        "type": "image",
        "images": [reference_url],
        "prompt": prompt,
        "size": requested_size,
        "n": REQUESTED_OUTPUT_COUNT,
        "response_format": "url",
    }


def _redact(value: Any, api_key: str | None) -> Any:
    if not api_key:
        return value
    if isinstance(value, str):
        return value.replace(api_key, "[REDACTED]")
    if isinstance(value, list):
        return [_redact(item, api_key) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact(item, api_key) for key, item in value.items()}
    return value


def write_json(path: Path, value: Any, *, api_key: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as file:
            temporary = Path(file.name)
            json.dump(_redact(value, api_key), file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def poll_task_with_raw_responses(
    task_id: str,
    submit_response: dict[str, Any],
    *,
    base_url: str,
    api_key: str,
    curl_path: str,
) -> list[dict[str, Any]]:
    """Follow the verified task protocol while retaining every poll body."""

    interval = provider.positive_number(
        submit_response.get("poll_interval"), POLL_INTERVAL
    )
    wait_limit = provider.positive_number(submit_response.get("max_wait"), MAX_WAIT)
    status_path = submit_response.get("task_status_url") or f"/v1/tasks/{task_id}/status"
    status_url = provider.provider_url(base_url, str(status_path))
    started = time.monotonic()
    responses: list[dict[str, Any]] = []

    while True:
        data = provider.curl_json_request(
            "GET",
            status_url,
            curl_path=curl_path,
            api_key=api_key,
            timeout=REQUEST_TIMEOUT,
        )
        responses.append(data)
        status = str(data.get("task_status") or data.get("status") or "").lower()
        if status in {"completed", "succeeded", "success", "finished"}:
            return responses
        if status in {"failed", "error", "cancelled", "canceled"}:
            raise PairPocError(f"Provider task reported failure: {status}")
        if status not in {"pending", "in_progress", "processing", "queued"}:
            raise PairPocError(f"Unknown provider task status: {status or '<missing>'}")
        elapsed = time.monotonic() - started
        if elapsed >= wait_limit:
            raise PairPocError(
                f"Provider task exceeded maximum wait of {wait_limit:g} seconds"
            )
        time.sleep(min(interval, max(0.0, wait_limit - elapsed)))


def fetch_raw_result(
    task_id: str, *, base_url: str, api_key: str, curl_path: str
) -> dict[str, Any]:
    return provider.curl_json_request(
        "GET",
        provider.provider_url(base_url, f"/v1/tasks/{task_id}/result"),
        curl_path=curl_path,
        api_key=api_key,
        timeout=REQUEST_TIMEOUT,
    )


def _value_at_path(value: Any, path: tuple[str, ...]) -> Any:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def extract_ordered_image_items(result: dict[str, Any]) -> tuple[str, list[Any]]:
    """Return the first recognized provider image array without reordering it."""

    candidates = (
        ("items", ("items",)),
        ("data.result.data", ("data", "result", "data")),
        ("data.data", ("data", "data")),
        ("result.data", ("result", "data")),
        ("images", ("images",)),
        ("data", ("data",)),
    )
    for label, path in candidates:
        value = _value_at_path(result, path)
        if isinstance(value, list) and value:
            return label, value
    return "not_found", []


def image_source(item: Any) -> tuple[str, str]:
    if isinstance(item, str):
        if provider.is_http_url(item):
            return "url", item
        return "base64", item
    if not isinstance(item, dict):
        raise PairPocError(f"Unsupported image result item type: {type(item).__name__}")
    url = item.get("url")
    if provider.is_http_url(url):
        return "url", url
    for key in ("b64_json", "base64", "image_base64"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return "base64", value
    nested = item.get("image")
    if isinstance(nested, dict):
        return image_source(nested)
    raise PairPocError(
        "Image result item does not contain a supported URL or base64 field"
    )


def _decode_base64_image(value: str) -> bytes:
    encoded = value.strip()
    if encoded.startswith("data:"):
        marker = encoded.find(",")
        if marker < 0:
            raise PairPocError("Invalid image data URL")
        encoded = encoded[marker + 1 :]
    try:
        return base64.b64decode("".join(encoded.split()), validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise PairPocError("Provider returned invalid base64 image data") from exc


def _save_png_bytes(data: bytes, output_path: Path) -> tuple[int, int]:
    temporary: Path | None = None
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            width, height = source.size
            bands = source.getbands()
            converted = source.convert("RGBA" if "A" in bands else "RGB")
        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
        ) as file:
            temporary = Path(file.name)
        converted.save(temporary, format="PNG")
        os.replace(temporary, output_path)
        temporary = None
        return width, height
    except (OSError, UnidentifiedImageError) as exc:
        raise PairPocError("Provider output is not a readable image") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_image_item(
    item: Any,
    output_path: Path,
    *,
    session: Any,
) -> tuple[int, int]:
    kind, value = image_source(item)
    if kind == "base64":
        return _save_png_bytes(_decode_base64_image(value), output_path)

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=output_path.parent,
            prefix=".image2-pair-download.",
            suffix=".part",
        ) as file:
            temporary = Path(file.name)
        toapis.download_image(
            value,
            temporary,
            timeout=DOWNLOAD_TIMEOUT,
            session=session,
        )
        data = temporary.read_bytes()
        return _save_png_bytes(data, output_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _base_result(
    *, model: str, reference_image: Path, requested_size: str | None
) -> dict[str, Any]:
    return {
        "experiment": EXPERIMENT,
        "model": model,
        "requested_size": requested_size,
        "reference_image": str(reference_image.resolve(strict=False)),
        "requested_output_count": REQUESTED_OUTPUT_COUNT,
        "submit_response": None,
        "poll_responses": [],
        "result_response": None,
    }


def run(
    args: argparse.Namespace,
    *,
    session: Any = None,
    curl_path: str | None = None,
) -> tuple[int, dict[str, Any]]:
    reference_image = Path(args.image)
    output_dir = Path(args.output_dir)
    request_path = output_dir / "request.json"
    result_path = output_dir / "result.json"
    metadata_path = output_dir / "metadata.json"
    model = args.model.strip()
    api_key = os.environ.get("OPENAI_API_KEY")
    configured_base_url = os.environ.get("OPENAI_BASE_URL") or ""
    requested_size: str | None = None
    result = _base_result(
        model=model, reference_image=reference_image, requested_size=requested_size
    )
    output_records: list[dict[str, Any]] = []
    output_errors: list[dict[str, Any]] = []

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        if not model:
            raise PairPocError("--model must not be empty")
        base_url = normalize_provider_base_url(configured_base_url)
        if not api_key:
            raise PairPocError("OPENAI_API_KEY is required")
        if session is None and toapis.requests is None:
            raise PairPocError("The requests package is required for upload/download")

        reference_size = inspect_image(reference_image, "Reference image")
        requested_size = select_provider_size(reference_size)
        final_prompt = build_prompt(args.prompt)
        result["requested_size"] = requested_size
        result["prompt"] = final_prompt

        active_session = session if session is not None else toapis.requests.Session()
        reference_url = toapis.upload_image(
            reference_image,
            base_url=base_url,
            api_key=api_key,
            timeout=UPLOAD_TIMEOUT,
            session=active_session,
        )
        payload = build_payload(
            model=model,
            prompt=final_prompt,
            reference_url=reference_url,
            requested_size=requested_size,
        )
        write_json(request_path, payload, api_key=api_key)

        active_curl = curl_path or provider.find_curl()
        submit_response = provider.submit_generation(
            payload,
            base_url=base_url,
            api_key=api_key,
            timeout=REQUEST_TIMEOUT,
            curl_path=active_curl,
        )
        result["submit_response"] = submit_response
        task_id = provider.submit_task_id(submit_response)
        if not isinstance(task_id, str) or not task_id.strip():
            raise PairPocError("Generation response did not contain a usable task id")
        result["task_id"] = task_id

        poll_responses = poll_task_with_raw_responses(
            task_id,
            submit_response,
            base_url=base_url,
            api_key=api_key,
            curl_path=active_curl,
        )
        result["poll_responses"] = poll_responses
        raw_result = fetch_raw_result(
            task_id,
            base_url=base_url,
            api_key=api_key,
            curl_path=active_curl,
        )
        result["result_response"] = raw_result

        result_path_label, image_items = extract_ordered_image_items(raw_result)
        result["image_array_path"] = result_path_label
        result["provider_output_count"] = len(image_items)

        for index, item in enumerate(image_items):
            output_path = output_dir / f"image_{index + 1:02d}.png"
            try:
                width, height = save_image_item(
                    item,
                    output_path,
                    session=active_session,
                )
                output_records.append(
                    {
                        "index": index,
                        "file": output_path.name,
                        "width": width,
                        "height": height,
                    }
                )
            except Exception as exc:
                output_errors.append(
                    {
                        "index": index,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    }
                )

        result["outputs"] = output_records
        result["output_errors"] = output_errors
        if len(output_records) == REQUESTED_OUTPUT_COUNT and not output_errors:
            status = "success"
            exit_code = 0
        else:
            status = "unexpected_output_count"
            exit_code = 3
        result["status"] = status

        metadata = {
            "experiment": EXPERIMENT,
            "model": model,
            "requested_size": requested_size,
            "reference_image": str(reference_image.resolve(strict=False)),
            "prompt": final_prompt,
            "outputs": output_records,
        }
        write_json(metadata_path, metadata, api_key=api_key)
        write_json(result_path, result, api_key=api_key)
        return exit_code, result
    except Exception as exc:
        result.update(
            {
                "status": "error",
                "outputs": output_records,
                "output_errors": output_errors,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }
        )
        try:
            write_json(result_path, result, api_key=api_key)
        except Exception:
            result["result_json_write_failed"] = True
        return 2, result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Test whether one GPT Image 2 request can return a matched text/clean UI pair."
        )
    )
    parser.add_argument("--image", required=True, help="Path to the reference UI image")
    parser.add_argument("--prompt", required=True, help="User requirement text")
    parser.add_argument("--output-dir", required=True, help="PoC artifact directory")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Image model (project default: {DEFAULT_MODEL})",
    )
    return parser.parse_args(argv)


def _output_path(result: dict[str, Any], output_dir: Path, index: int) -> str:
    for output in result.get("outputs", []):
        if isinstance(output, dict) and output.get("index") == index:
            return str((output_dir / str(output["file"])).resolve(strict=False))
    return ""


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    exit_code, result = run(args)
    output_dir = Path(args.output_dir)
    safe_result = _redact(result, os.environ.get("OPENAI_API_KEY"))
    print(f"STATUS = {safe_result.get('status', 'error')}")
    print(f"MODEL = {safe_result.get('model', args.model)}")
    print(f"OUTPUT_COUNT = {len(safe_result.get('outputs', []))}")
    print(f"IMAGE_01 = {_output_path(safe_result, output_dir, 0)}")
    print(f"IMAGE_02 = {_output_path(safe_result, output_dir, 1)}")
    print(f"RESULT_JSON = {(output_dir / 'result.json').resolve(strict=False)}")
    if safe_result.get("error_message"):
        print(f"ERROR = {safe_result['error_message']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
