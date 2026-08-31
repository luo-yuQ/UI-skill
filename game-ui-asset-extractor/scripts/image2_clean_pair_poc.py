#!/usr/bin/env python3
"""PoC: request a matched text/clean game-UI pair from GPT Image 2.

This script is intentionally independent from the Stage0/Stage1/Stage2 flow.

Experiment goal:
- upload one reference UI image
- send one image-to-image request
- request n=2 outputs
- ask GPT Image 2 to produce:
  A. normal UI with text
  B. same UI without text

The upload path deliberately uses the verified ToAPIs multipart /api/upload
protocol rather than relying on the adapter's generic upload implementation.
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

REQUESTED_OUTPUT_COUNT = 1

UPLOAD_TIMEOUT = 120.0
REQUEST_TIMEOUT = 120.0
DOWNLOAD_TIMEOUT = 180.0

POLL_INTERVAL = 3.0
MAX_WAIT = 300.0


PAIR_PROMPT = """Generate exactly ONE image.

The single output image must contain TWO side-by-side versions of the SAME game UI.

LEFT HALF:
Presentation version with normal visible UI text.

RIGHT HALF:
Clean production version with all ordinary UI text and numbers removed.

Both halves must use the SAME:
- composition
- panel geometry
- icon positions
- character positions
- button positions
- decorations
- spacing
- proportions
- visual style

The right half is NOT a redesign.
It must reproduce the left-half UI as closely as possible, except that ordinary text and numbers are absent.

Use a strict vertical split at the center of the canvas.
Do not allow elements to cross the center line.
Do not stack the versions vertically.
Do not create more than two versions.

Do not replace removed text with:
- placeholder bars
- gray blocks
- fake labels
- new decorations

Preserve the underlying button surfaces, panels, borders, gradients, shadows, textures, icons and artwork where text is removed.

User requirement:
{user_prompt}"""


class PairPocError(RuntimeError):
    """Expected input, configuration, provider, or output failure."""


def _load_repository_module(name: str, relative_path: str) -> ModuleType:
    repository_root = Path(__file__).resolve().parents[2]
    module_path = repository_root / relative_path

    spec = importlib.util.spec_from_file_location(name, module_path)

    if spec is None or spec.loader is None:
        raise ImportError(
            f"Unable to load repository helper: {module_path}"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


# Existing repository helpers:
#
# generate_preview.py:
# - submit_generation
# - task polling helpers
# - curl request transport
#
# toapis_preview_adapter.py:
# - provider_url
# - download_image
# - requests dependency
#
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


def inspect_image(
    path: Path,
    label: str = "Image",
) -> tuple[int, int]:
    if not path.exists() or not path.is_file():
        raise PairPocError(
            f"{label} does not exist or is not a file: {path}"
        )

    try:
        with Image.open(path) as image:
            size = image.size
            image.verify()

    except (OSError, UnidentifiedImageError) as exc:
        raise PairPocError(
            f"{label} is not a readable image: {path}"
        ) from exc

    if size[0] <= 0 or size[1] <= 0:
        raise PairPocError(
            f"{label} dimensions must be positive: "
            f"{size[0]}x{size[1]}"
        )

    return size


def select_provider_size(
    reference_size: tuple[int, int],
) -> str:
    width, height = reference_size

    if width > height:
        selected = "1536x1024"
    elif height > width:
        selected = "1024x1536"
    else:
        selected = "1024x1024"

    if selected not in SUPPORTED_SIZES:
        raise PairPocError(
            f"Project provider does not support the selected size "
            f"{selected}; supported sizes: "
            f"{', '.join(SUPPORTED_SIZES)}"
        )

    return selected


def normalize_provider_base_url(
    configured_url: str,
) -> str:
    """Normalize OPENAI_BASE_URL to provider root.

    Examples:

    https://ai-api.youchu.work
        ->
    https://ai-api.youchu.work

    https://ai-api.youchu.work/v1
        ->
    https://ai-api.youchu.work
    """

    normalized = configured_url.strip().rstrip("/")

    parsed = urlsplit(normalized)

    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
    ):
        raise PairPocError(
            "OPENAI_BASE_URL must be an absolute HTTP(S) URL"
        )

    if parsed.query or parsed.fragment:
        raise PairPocError(
            "OPENAI_BASE_URL must not contain a query or fragment"
        )

    path = parsed.path.rstrip("/")

    if path.endswith("/v1"):
        path = path[:-3]

    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            "",
            "",
        )
    ).rstrip("/")


def build_prompt(user_prompt: str) -> str:
    requirement = user_prompt.strip()

    if not requirement:
        raise PairPocError(
            "--prompt must not be empty"
        )

    return PAIR_PROMPT.format(
        user_prompt=requirement
    )


def build_payload(
    *,
    model: str,
    prompt: str,
    reference_url: str,
    requested_size: str,
) -> dict[str, Any]:
    """Build ToAPIs /v1/images/generations payload."""

    return {
        "model": model,
        "type": "image",
        "images": [reference_url],
        "prompt": prompt,
        "size": requested_size,
        "n": REQUESTED_OUTPUT_COUNT,
    }


def _redact(
    value: Any,
    api_key: str | None,
) -> Any:
    if not api_key:
        return value

    if isinstance(value, str):
        return value.replace(
            api_key,
            "[REDACTED]",
        )

    if isinstance(value, list):
        return [
            _redact(item, api_key)
            for item in value
        ]

    if isinstance(value, dict):
        return {
            str(key): _redact(item, api_key)
            for key, item in value.items()
        }

    return value


def write_json(
    path: Path,
    value: Any,
    *,
    api_key: str | None = None,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

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

            json.dump(
                _redact(value, api_key),
                file,
                ensure_ascii=False,
                indent=2,
            )

            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        os.replace(
            temporary,
            path,
        )

        temporary = None

    finally:
        if temporary is not None:
            temporary.unlink(
                missing_ok=True
            )


def upload_reference_image(
    image_path: Path,
    *,
    base_url: str,
    api_key: str,
    timeout: float,
    session: Any,
) -> str:
    """Upload image using the verified ToAPIs multipart protocol.

    POST:
        /api/upload

    Header:
        Authorization: Bearer <key>

    Multipart:
        file=<binary image>
    """

    upload_url = toapis.provider_url(
        base_url,
        "/api/upload",
    )

    suffix = image_path.suffix.lower()

    if suffix == ".png":
        mime_type = "image/png"

    elif suffix in {".jpg", ".jpeg"}:
        mime_type = "image/jpeg"

    elif suffix == ".webp":
        mime_type = "image/webp"

    else:
        raise PairPocError(
            f"Unsupported image type for upload: "
            f"{image_path}"
        )

    try:
        with image_path.open("rb") as file:

            response = session.post(
                upload_url,
                headers={
                    "Authorization": (
                        f"Bearer {api_key}"
                    ),
                },
                files={
                    "file": (
                        image_path.name,
                        file,
                        mime_type,
                    ),
                },
                timeout=timeout,
            )

    except Exception as exc:
        raise PairPocError(
            f"Upload request failed for "
            f"{image_path.name}: {exc}"
        ) from exc

    if not 200 <= response.status_code < 300:
        body = response.text[:2000]

        raise PairPocError(
            f"Upload failed for "
            f"{image_path.name}: "
            f"HTTP {response.status_code}, "
            f"body={body}"
        )

    try:
        data = response.json()

    except ValueError as exc:
        raise PairPocError(
            f"Upload response was not JSON "
            f"for {image_path.name}: "
            f"{response.text[:2000]}"
        ) from exc

    image_url = data.get("url")

    if (
        not isinstance(image_url, str)
        or not image_url.strip()
    ):
        raise PairPocError(
            f"Upload response missing url "
            f"for {image_path.name}: "
            f"{json.dumps(data, ensure_ascii=False)}"
        )

    return image_url.strip()


def poll_task_with_raw_responses(
    task_id: str,
    submit_response: dict[str, Any],
    *,
    base_url: str,
    api_key: str,
    curl_path: str,
) -> list[dict[str, Any]]:
    """Follow task protocol while retaining every poll response."""

    interval = provider.positive_number(
        submit_response.get("poll_interval"),
        POLL_INTERVAL,
    )

    wait_limit = provider.positive_number(
        submit_response.get("max_wait"),
        MAX_WAIT,
    )

    status_path = (
        submit_response.get("task_status_url")
        or f"/v1/tasks/{task_id}/status"
    )

    status_url = provider.provider_url(
        base_url,
        str(status_path),
    )

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

        status = str(
            data.get("task_status")
            or data.get("status")
            or ""
        ).lower()

        if status in {
            "completed",
            "succeeded",
            "success",
            "finished",
        }:
            return responses

        if status in {
            "failed",
            "error",
            "cancelled",
            "canceled",
        }:
            raise PairPocError(
                f"Provider task reported failure: "
                f"{status}"
            )

        if status not in {
            "pending",
            "in_progress",
            "processing",
            "queued",
        }:
            raise PairPocError(
                f"Unknown provider task status: "
                f"{status or '<missing>'}"
            )

        elapsed = (
            time.monotonic()
            - started
        )

        if elapsed >= wait_limit:
            raise PairPocError(
                f"Provider task exceeded maximum "
                f"wait of {wait_limit:g} seconds"
            )

        time.sleep(
            min(
                interval,
                max(
                    0.0,
                    wait_limit - elapsed,
                ),
            )
        )


def fetch_raw_result(
    task_id: str,
    *,
    base_url: str,
    api_key: str,
    curl_path: str,
) -> dict[str, Any]:

    result_url = provider.provider_url(
        base_url,
        f"/v1/tasks/{task_id}/result",
    )

    return provider.curl_json_request(
        "GET",
        result_url,
        curl_path=curl_path,
        api_key=api_key,
        timeout=REQUEST_TIMEOUT,
    )


def _value_at_path(
    value: Any,
    path: tuple[str, ...],
) -> Any:

    current = value

    for key in path:

        if not isinstance(current, dict):
            return None

        current = current.get(key)

    return current


def extract_ordered_image_items(
    result: dict[str, Any],
) -> tuple[str, list[Any]]:
    """Find provider image array without reordering it."""

    candidates = (
        (
            "items",
            ("items",),
        ),
        (
            "data.result.data",
            ("data", "result", "data"),
        ),
        (
            "data.data",
            ("data", "data"),
        ),
        (
            "result.data",
            ("result", "data"),
        ),
        (
            "images",
            ("images",),
        ),
        (
            "data",
            ("data",),
        ),
    )

    for label, path in candidates:

        value = _value_at_path(
            result,
            path,
        )

        if (
            isinstance(value, list)
            and value
        ):
            return label, value

    return "not_found", []


def image_source(
    item: Any,
) -> tuple[str, str]:

    if isinstance(item, str):

        if provider.is_http_url(item):
            return "url", item

        return "base64", item

    if not isinstance(item, dict):
        raise PairPocError(
            f"Unsupported image result item type: "
            f"{type(item).__name__}"
        )

    url = item.get("url")

    if provider.is_http_url(url):
        return "url", url

    for key in (
        "b64_json",
        "base64",
        "image_base64",
    ):

        value = item.get(key)

        if (
            isinstance(value, str)
            and value.strip()
        ):
            return "base64", value

    nested = item.get("image")

    if isinstance(nested, dict):
        return image_source(nested)

    raise PairPocError(
        "Image result item does not contain "
        "a supported URL or base64 field"
    )


def _decode_base64_image(
    value: str,
) -> bytes:

    encoded = value.strip()

    if encoded.startswith("data:"):

        marker = encoded.find(",")

        if marker < 0:
            raise PairPocError(
                "Invalid image data URL"
            )

        encoded = encoded[
            marker + 1 :
        ]

    try:
        return base64.b64decode(
            "".join(encoded.split()),
            validate=True,
        )

    except Exception as exc:
        raise PairPocError(
            "Provider returned invalid "
            "base64 image data"
        ) from exc


def _save_png_bytes(
    data: bytes,
    output_path: Path,
) -> tuple[int, int]:

    temporary: Path | None = None

    try:

        with Image.open(
            io.BytesIO(data)
        ) as source:

            source.load()

            width, height = (
                source.size
            )

            bands = source.getbands()

            converted = source.convert(
                "RGBA"
                if "A" in bands
                else "RGB"
            )

        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
        ) as file:

            temporary = Path(
                file.name
            )

        converted.save(
            temporary,
            format="PNG",
        )

        os.replace(
            temporary,
            output_path,
        )

        temporary = None

        return width, height

    except (
        OSError,
        UnidentifiedImageError,
    ) as exc:

        raise PairPocError(
            "Provider output is not "
            "a readable image"
        ) from exc

    finally:

        if temporary is not None:
            temporary.unlink(
                missing_ok=True
            )


def save_image_item(
    item: Any,
    output_path: Path,
    *,
    session: Any,
) -> tuple[int, int]:

    kind, value = image_source(
        item
    )

    if kind == "base64":

        raw = _decode_base64_image(
            value
        )

        return _save_png_bytes(
            raw,
            output_path,
        )

    temporary: Path | None = None

    try:

        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=output_path.parent,
            prefix=".image2-pair-download.",
            suffix=".part",
        ) as file:

            temporary = Path(
                file.name
            )

        toapis.download_image(
            value,
            temporary,
            timeout=DOWNLOAD_TIMEOUT,
            session=session,
        )

        data = temporary.read_bytes()

        return _save_png_bytes(
            data,
            output_path,
        )

    finally:

        if temporary is not None:
            temporary.unlink(
                missing_ok=True
            )


def _base_result(
    *,
    model: str,
    reference_image: Path,
    requested_size: str | None,
) -> dict[str, Any]:

    return {
        "experiment": EXPERIMENT,
        "model": model,
        "requested_size": requested_size,
        "reference_image": str(
            reference_image.resolve(
                strict=False
            )
        ),
        "requested_output_count": (
            REQUESTED_OUTPUT_COUNT
        ),
        "upload_response_url": None,
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

    reference_image = Path(
        args.image
    )

    output_dir = Path(
        args.output_dir
    )

    request_path = (
        output_dir
        / "request.json"
    )

    result_path = (
        output_dir
        / "result.json"
    )

    metadata_path = (
        output_dir
        / "metadata.json"
    )

    model = args.model.strip()

    api_key = os.environ.get(
        "OPENAI_API_KEY"
    )

    configured_base_url = (
        os.environ.get(
            "OPENAI_BASE_URL"
        )
        or ""
    )

    requested_size: str | None = None

    result = _base_result(
        model=model,
        reference_image=reference_image,
        requested_size=requested_size,
    )

    output_records: list[
        dict[str, Any]
    ] = []

    output_errors: list[
        dict[str, Any]
    ] = []

    try:

        output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        if not model:
            raise PairPocError(
                "--model must not be empty"
            )

        base_url = (
            normalize_provider_base_url(
                configured_base_url
            )
        )

        if not api_key:
            raise PairPocError(
                "OPENAI_API_KEY is required"
            )

        if (
            session is None
            and toapis.requests is None
        ):
            raise PairPocError(
                "The requests package is required "
                "for upload/download"
            )

        reference_size = inspect_image(
            reference_image,
            "Reference image",
        )

        requested_size = (
            select_provider_size(
                reference_size
            )
        )

        final_prompt = build_prompt(
            args.prompt
        )

        result["requested_size"] = (
            requested_size
        )

        result["prompt"] = (
            final_prompt
        )

        if session is not None:

            active_session = session

        else:

            active_session = (
                toapis.requests.Session()
            )

        # ---------------------------------------------------------
        # STEP 1
        # Verified multipart upload.
        # ---------------------------------------------------------

        reference_url = (
            upload_reference_image(
                reference_image,
                base_url=base_url,
                api_key=api_key,
                timeout=UPLOAD_TIMEOUT,
                session=active_session,
            )
        )

        result["upload_response_url"] = (
            reference_url
        )

        # ---------------------------------------------------------
        # STEP 2
        # Build image-generation request.
        # ---------------------------------------------------------

        payload = build_payload(
            model=model,
            prompt=final_prompt,
            reference_url=reference_url,
            requested_size=requested_size,
        )

        # Important:
        #
        # If upload succeeded, request.json MUST exist before
        # generation submission starts.
        #
        write_json(
            request_path,
            payload,
            api_key=api_key,
        )

        # ---------------------------------------------------------
        # STEP 3
        # Submit /v1/images/generations.
        # ---------------------------------------------------------

        active_curl = (
            curl_path
            or provider.find_curl()
        )

        submit_response = (
            provider.submit_generation(
                payload,
                base_url=base_url,
                api_key=api_key,
                timeout=REQUEST_TIMEOUT,
                curl_path=active_curl,
            )
        )

        result["submit_response"] = (
            submit_response
        )

        task_id = (
            provider.submit_task_id(
                submit_response
            )
        )

        if (
            not isinstance(
                task_id,
                str,
            )
            or not task_id.strip()
        ):
            raise PairPocError(
                "Generation response did not "
                "contain a usable task id"
            )

        result["task_id"] = (
            task_id
        )

        # ---------------------------------------------------------
        # STEP 4
        # Poll asynchronous generation task.
        # ---------------------------------------------------------

        poll_responses = (
            poll_task_with_raw_responses(
                task_id,
                submit_response,
                base_url=base_url,
                api_key=api_key,
                curl_path=active_curl,
            )
        )

        result["poll_responses"] = (
            poll_responses
        )

        # ---------------------------------------------------------
        # STEP 5
        # Fetch raw generation result.
        # ---------------------------------------------------------

        raw_result = fetch_raw_result(
            task_id,
            base_url=base_url,
            api_key=api_key,
            curl_path=active_curl,
        )

        result["result_response"] = (
            raw_result
        )

        (
            result_path_label,
            image_items,
        ) = extract_ordered_image_items(
            raw_result
        )

        result["image_array_path"] = (
            result_path_label
        )

        result["provider_output_count"] = (
            len(image_items)
        )

        # ---------------------------------------------------------
        # STEP 6
        # Save all provider outputs.
        # ---------------------------------------------------------

        for index, item in enumerate(
            image_items
        ):

            output_path = (
                output_dir
                / f"image_{index + 1:02d}.png"
            )

            try:

                width, height = (
                    save_image_item(
                        item,
                        output_path,
                        session=active_session,
                    )
                )

                output_records.append(
                    {
                        "index": index,
                        "file": (
                            output_path.name
                        ),
                        "width": width,
                        "height": height,
                    }
                )

            except Exception as exc:

                output_errors.append(
                    {
                        "index": index,
                        "error_type": (
                            type(exc).__name__
                        ),
                        "error_message": (
                            str(exc)
                        ),
                    }
                )

        result["outputs"] = (
            output_records
        )

        result["output_errors"] = (
            output_errors
        )

        if (
            len(output_records)
            == REQUESTED_OUTPUT_COUNT
            and not output_errors
        ):

            status = "success"
            exit_code = 0

        else:

            status = (
                "unexpected_output_count"
            )

            exit_code = 3

        result["status"] = status

        metadata = {
            "experiment": EXPERIMENT,
            "model": model,
            "requested_size": (
                requested_size
            ),
            "reference_image": str(
                reference_image.resolve(
                    strict=False
                )
            ),
            "prompt": final_prompt,
            "outputs": output_records,
        }

        write_json(
            metadata_path,
            metadata,
            api_key=api_key,
        )

        write_json(
            result_path,
            result,
            api_key=api_key,
        )

        return exit_code, result

    except Exception as exc:

        result.update(
            {
                "status": "error",
                "outputs": (
                    output_records
                ),
                "output_errors": (
                    output_errors
                ),
                "error_type": (
                    type(exc).__name__
                ),
                "error_message": (
                    str(exc)
                ),
            }
        )

        try:

            write_json(
                result_path,
                result,
                api_key=api_key,
            )

        except Exception:

            result[
                "result_json_write_failed"
            ] = True

        return 2, result


def parse_args(
    argv: list[str] | None = None,
) -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description=(
            "Test whether one GPT Image 2 request "
            "can return a matched text/clean UI pair."
        )
    )

    parser.add_argument(
        "--image",
        required=True,
        help=(
            "Path to the reference UI image"
        ),
    )

    parser.add_argument(
        "--prompt",
        required=True,
        help=(
            "User requirement text"
        ),
    )

    parser.add_argument(
        "--output-dir",
        required=True,
        help=(
            "PoC artifact directory"
        ),
    )

    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=(
            f"Image model "
            f"(project default: {DEFAULT_MODEL})"
        ),
    )

    return parser.parse_args(
        argv
    )


def _output_path(
    result: dict[str, Any],
    output_dir: Path,
    index: int,
) -> str:

    for output in result.get(
        "outputs",
        [],
    ):

        if (
            isinstance(output, dict)
            and output.get("index")
            == index
        ):

            return str(
                (
                    output_dir
                    / str(output["file"])
                ).resolve(
                    strict=False
                )
            )

    return ""


def main(
    argv: list[str] | None = None,
) -> int:

    args = parse_args(
        argv
    )

    exit_code, result = run(
        args
    )

    output_dir = Path(
        args.output_dir
    )

    safe_result = _redact(
        result,
        os.environ.get(
            "OPENAI_API_KEY"
        ),
    )

    print(
        f"STATUS = "
        f"{safe_result.get('status', 'error')}"
    )

    print(
        f"MODEL = "
        f"{safe_result.get('model', args.model)}"
    )

    print(
        f"OUTPUT_COUNT = "
        f"{len(safe_result.get('outputs', []))}"
    )

    print(
        f"IMAGE_01 = "
        f"{_output_path(safe_result, output_dir, 0)}"
    )

    print(
        f"IMAGE_02 = "
        f"{_output_path(safe_result, output_dir, 1)}"
    )

    print(
        f"RESULT_JSON = "
        f"{(output_dir / 'result.json').resolve(strict=False)}"
    )

    if safe_result.get(
        "upload_response_url"
    ):

        print(
            "UPLOAD = success"
        )

    if safe_result.get(
        "error_message"
    ):

        print(
            f"ERROR = "
            f"{safe_result['error_message']}"
        )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(
        main()
    )