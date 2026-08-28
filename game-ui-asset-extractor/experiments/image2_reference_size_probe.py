#!/usr/bin/env python3
"""Probe whether gpt-image-2 output size changes with reference count.

This is a standalone ToAPIs provider-behavior experiment. It deliberately
does not perform any UI repair and never resizes, crops, thumbnails, converts,
or re-encodes the downloaded provider image.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

from PIL import Image, UnidentifiedImageError


PROMPT = (
    "Generate exactly one simple vertical test image. No text. "
    "Keep the composition simple."
)
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_SIZE = "1024x1536"
EXPECTED_REFERENCE_SIZE = (1024, 1536)
DEFAULT_BASE_URL = "https://ai-api.youchu.work"

UPLOAD_TIMEOUT = 120.0
REQUEST_TIMEOUT = 120.0
DOWNLOAD_TIMEOUT = 180.0
POLL_INTERVAL = 3.0
MAX_WAIT = 300.0

CASE_SPECS = (
    ("A", "case-a-no-reference", 0),
    ("B", "case-b-one-reference", 1),
    ("C", "case-c-two-references", 2),
)


class ProbeError(RuntimeError):
    """Expected probe input, configuration, provider, or output failure."""


def _load_repository_module(name: str, relative_path: str) -> ModuleType:
    repository_root = Path(__file__).resolve().parents[2]
    module_path = repository_root / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load repository helper: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Load private module instances so this probe reuses the repository's verified
# ToAPIs implementation without changing either helper module.
provider = _load_repository_module(
    "_image2_reference_size_generate_preview",
    "game-ui-image-provider-adapter/scripts/generate_preview.py",
)
toapis = _load_repository_module(
    "_image2_reference_size_toapis_adapter",
    "game-ui-auto-composer-skill/scripts/toapis_preview_adapter.py",
)


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


def write_json(path: Path, value: dict[str, Any], *, api_key: str | None) -> None:
    """Atomically write a UTF-8 JSON artifact, redacting only the API key."""

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
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def write_text(path: Path, value: str) -> None:
    """Atomically write a UTF-8 text artifact."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as file:
            temporary = Path(file.name)
            file.write(value)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def inspect_reference(path: Path) -> None:
    if not path.exists() or not path.is_file():
        raise ProbeError(f"Reference does not exist or is not a file: {path}")
    try:
        with Image.open(path) as image:
            image_format = image.format
            size = image.size
            image.verify()
    except (OSError, UnidentifiedImageError) as exc:
        raise ProbeError(f"Reference is not a readable PNG: {path}") from exc
    if image_format != "PNG":
        raise ProbeError(f"--reference must be a PNG; Pillow detected {image_format!r}")
    if size != EXPECTED_REFERENCE_SIZE:
        raise ProbeError(
            "--reference must be exactly 1024x1536; "
            f"Pillow detected {size[0]}x{size[1]}"
        )


def inspect_downloaded_size(path: Path) -> tuple[int, int]:
    """Read the provider file's dimensions without changing the file."""

    try:
        with Image.open(path) as image:
            image.load()
            width, height = image.size
    except (OSError, UnidentifiedImageError) as exc:
        raise ProbeError(f"Downloaded provider output is not a readable image: {path}") from exc
    if width <= 0 or height <= 0:
        raise ProbeError(f"Downloaded provider output has invalid dimensions: {width}x{height}")
    return width, height


def build_request(
    *, model: str, size: str, reference_urls: list[str]
) -> dict[str, Any]:
    """Build the exact JSON body sent to /v1/images/generations."""

    body: dict[str, Any] = {
        "model": model,
        "type": "image",
        "prompt": PROMPT,
        "size": size,
        "n": 1,
        "response_format": "url",
    }
    # Case A must omit the field rather than submit an empty array.
    if reference_urls:
        body["images"] = list(reference_urls)
    return body


def _error_artifact(stage: str, exc: Exception, api_key: str) -> dict[str, Any]:
    return {
        "status": "error",
        "stage": stage,
        "error_type": type(exc).__name__,
        "error_message": str(exc).replace(api_key, "[REDACTED]"),
    }


def run_case(
    *,
    case: str,
    directory_name: str,
    reference_urls: list[str],
    output_dir: Path,
    model: str,
    size: str,
    base_url: str,
    api_key: str,
    curl_path: str,
    session: Any,
    request_timeout: float,
    download_timeout: float,
    poll_interval: float,
    max_wait: float,
) -> dict[str, Any]:
    case_dir = output_dir / directory_name
    case_dir.mkdir(parents=True, exist_ok=True)
    request_path = case_dir / "request.json"
    submit_path = case_dir / "submit.json"
    result_response_path = case_dir / "result-response.json"
    output_path = case_dir / "output.png"
    result_path = case_dir / "result.json"

    # A failed rerun must not leave an earlier successful image looking current.
    output_path.unlink(missing_ok=True)

    request_body = build_request(
        model=model,
        size=size,
        reference_urls=reference_urls,
    )
    # This file is written immediately before the same object is submitted.
    write_json(request_path, request_body, api_key=api_key)

    task_id: str | None = None
    image_url: str | None = None
    submit_saved = False
    result_response_saved = False
    stage = "submit"
    result: dict[str, Any] = {
        "case": case,
        "reference_count": len(reference_urls),
        "requested_size": size,
        "actual_width": 0,
        "actual_height": 0,
        "image_url": None,
        "task_id": None,
    }

    try:
        # Use generate_preview's verified curl JSON transport and task-id rules.
        submit_response = provider.curl_json_request(
            "POST",
            provider.provider_url(base_url, "/v1/images/generations"),
            curl_path=curl_path,
            api_key=api_key,
            timeout=request_timeout,
            payload=request_body,
        )
        write_json(submit_path, submit_response, api_key=api_key)
        submit_saved = True
        if submit_response.get("success") is False:
            raise ProbeError("Generation submission reported success=false")
        task_id = provider.submit_task_id(submit_response)
        if not isinstance(task_id, str) or not task_id.strip():
            raise ProbeError("Generation submission did not return a usable task id")
        result["task_id"] = task_id

        stage = "poll"
        provider.poll_task(
            task_id,
            submit_response,
            base_url=base_url,
            api_key=api_key,
            poll_interval=poll_interval,
            max_wait=max_wait,
            timeout=request_timeout,
            curl_path=curl_path,
        )

        stage = "result"
        result_response = provider.curl_json_request(
            "GET",
            provider.provider_url(base_url, f"/v1/tasks/{task_id}/result"),
            curl_path=curl_path,
            api_key=api_key,
            timeout=request_timeout,
        )
        # Preserve the complete parsed JSON body returned by the result endpoint.
        write_json(result_response_path, result_response, api_key=api_key)
        result_response_saved = True
        image_url = provider.extract_image_url(result_response)
        if not isinstance(image_url, str) or not image_url.strip():
            raise ProbeError("Task result did not contain a usable image URL")
        result["image_url"] = image_url

        stage = "download"
        # The existing helper streams provider bytes directly to output.png.
        # No Pillow save and no image transformation is performed.
        toapis.download_image(
            image_url,
            output_path,
            timeout=download_timeout,
            session=session,
        )

        stage = "inspect"
        width, height = inspect_downloaded_size(output_path)
        result.update(
            {
                "status": "success",
                "actual_width": width,
                "actual_height": height,
            }
        )
    except Exception as exc:
        error = _error_artifact(stage, exc, api_key)
        if not submit_saved:
            write_json(submit_path, error, api_key=api_key)
        if not result_response_saved:
            write_json(result_response_path, error, api_key=api_key)
        result.update(error)
        result["task_id"] = task_id
        result["image_url"] = image_url

    write_json(result_path, result, api_key=api_key)
    return result


def build_summary(size: str, case_results: list[dict[str, Any]]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for result in case_results:
        if result.get("status") == "success":
            actual = f"{result['actual_width']}x{result['actual_height']}"
        else:
            actual = "ERROR"
        item: dict[str, Any] = {
            "case": result["case"],
            "reference_count": result["reference_count"],
            "actual_size": actual,
        }
        if result.get("status") != "success":
            item["status"] = "error"
            item["error_message"] = result.get("error_message")
        cases.append(item)
    return {"requested_size": size, "cases": cases}


def build_readme(summary: dict[str, Any]) -> str:
    requested_size = str(summary["requested_size"])
    rows = [
        "# gpt-image-2 Reference Count / Output Size Probe",
        "",
        "| Case | Reference Count | Requested | Actual |",
        "| ---- | --------------- | --------- | ------ |",
    ]
    for item in summary["cases"]:
        rows.append(
            f"| {item['case']} | {item['reference_count']} | "
            f"{requested_size} | {item['actual_size']} |"
        )
    rows.append("")
    return "\n".join(rows)


def _validate_positive_args(args: argparse.Namespace) -> None:
    for name in (
        "upload_timeout",
        "request_timeout",
        "download_timeout",
        "poll_interval",
        "max_wait",
    ):
        if getattr(args, name) <= 0:
            raise ProbeError(f"--{name.replace('_', '-')} must be greater than zero")


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    reference_path = Path(args.reference)
    output_dir = Path(args.output_dir)
    api_key = os.environ.get("TOAPIS_API_KEY")
    base_url = os.environ.get("TOAPIS_BASE_URL", DEFAULT_BASE_URL).rstrip("/")

    _validate_positive_args(args)
    inspect_reference(reference_path)
    if not provider.is_http_url(base_url):
        raise ProbeError("TOAPIS_BASE_URL must be an absolute HTTP(S) URL")
    if not api_key:
        raise ProbeError("TOAPIS_API_KEY is required")
    if toapis.requests is None:
        raise ProbeError("The requests package is required for upload and download")

    output_dir.mkdir(parents=True, exist_ok=True)
    curl_path = provider.find_curl()
    session = toapis.requests.Session()
    try:
        # Intentionally upload the exact same local PNG twice. Do not cache or
        # reuse the first URL: reference count is the experiment's only variable.
        reference_url_1 = toapis.upload_image(
            reference_path,
            base_url=base_url,
            api_key=api_key,
            timeout=args.upload_timeout,
            session=session,
        )
        reference_url_2 = toapis.upload_image(
            reference_path,
            base_url=base_url,
            api_key=api_key,
            timeout=args.upload_timeout,
            session=session,
        )

        urls_by_case = {
            "A": [],
            "B": [reference_url_1],
            "C": [reference_url_1, reference_url_2],
        }
        case_results = []
        for case, directory_name, expected_count in CASE_SPECS:
            reference_urls = urls_by_case[case]
            if len(reference_urls) != expected_count:
                raise AssertionError(f"Internal reference-count mismatch for case {case}")
            case_results.append(
                run_case(
                    case=case,
                    directory_name=directory_name,
                    reference_urls=reference_urls,
                    output_dir=output_dir,
                    model=args.model,
                    size=args.size,
                    base_url=base_url,
                    api_key=api_key,
                    curl_path=curl_path,
                    session=session,
                    request_timeout=args.request_timeout,
                    download_timeout=args.download_timeout,
                    poll_interval=args.poll_interval,
                    max_wait=args.max_wait,
                )
            )
    finally:
        session.close()

    summary = build_summary(args.size, case_results)
    write_json(output_dir / "summary.json", summary, api_key=api_key)
    write_text(output_dir / "README.md", build_readme(summary))
    all_succeeded = all(item.get("status") == "success" for item in case_results)
    return (0 if all_succeeded else 1), summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reference",
        required=True,
        help="Path to the local 1024x1536 PNG uploaded twice for cases B/C",
    )
    parser.add_argument("--output-dir", required=True, help="Probe artifact directory")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        choices=(DEFAULT_MODEL,),
        help=f"Fixed provider model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--size",
        default=DEFAULT_SIZE,
        choices=(DEFAULT_SIZE,),
        help=f"Fixed requested provider size (default: {DEFAULT_SIZE})",
    )
    parser.add_argument("--upload-timeout", type=float, default=UPLOAD_TIMEOUT)
    parser.add_argument("--request-timeout", type=float, default=REQUEST_TIMEOUT)
    parser.add_argument("--download-timeout", type=float, default=DOWNLOAD_TIMEOUT)
    parser.add_argument("--poll-interval", type=float, default=POLL_INTERVAL)
    parser.add_argument("--max-wait", type=float, default=MAX_WAIT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        exit_code, summary = run(args)
    except Exception as exc:
        api_key = os.environ.get("TOAPIS_API_KEY")
        message = str(exc).replace(api_key, "[REDACTED]") if api_key else str(exc)
        print(json.dumps({"status": "error", "error_message": message}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
