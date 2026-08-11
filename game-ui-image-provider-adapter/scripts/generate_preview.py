#!/usr/bin/env python3
"""Generate one pure-text game UI preview through ToAPIs using system curl."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse


SCHEMA_VERSION = "0.1"
PROVIDER = "ToAPIs"
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_BASE_URL = "https://ai-api.youchu.work"
SUPPORTED_SIZES = ("1024x1024", "1536x1024", "1024x1536")

EXIT_INPUT = 2
EXIT_CONFIG = 3
EXIT_PROVIDER = 4
EXIT_OUTPUT = 5

HTTP_STATUS_MARKER = "__CODEX_HTTP_STATUS__:"
CONTENT_TYPE_MARKER = "__CODEX_CONTENT_TYPE__:"
CANVAS_PATTERN = re.compile(
    r"\bcompose\s+for\s+a\s+(\d+)\s*[x×]\s*(\d+)\s*px\s+canvas\b",
    re.IGNORECASE,
)


class AdapterError(RuntimeError):
    """Expected failure with a stable result type and exit status."""

    def __init__(self, error_type: str, message: str, *, exit_code: int) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.exit_code = exit_code


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def provider_url(base_url: str, path: str) -> str:
    if is_http_url(path):
        return path
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def find_curl() -> str:
    for name in ("curl.exe", "curl"):
        path = shutil.which(name)
        if path:
            return path
    raise AdapterError(
        "provider_dependency_missing",
        "System curl was not found; install curl.exe or make curl available on PATH",
        exit_code=EXIT_CONFIG,
    )


def read_prompt(path: Path) -> str:
    if not path.exists() or not path.is_file():
        raise AdapterError("prompt_not_found", f"Prompt file not found: {path}", exit_code=EXIT_INPUT)
    try:
        prompt = path.read_text(encoding="utf-8-sig").rstrip()
    except (OSError, UnicodeError) as exc:
        raise AdapterError("prompt_read_failed", f"Unable to read UTF-8 prompt: {path}: {exc}", exit_code=EXIT_INPUT) from exc
    if not prompt.strip():
        raise AdapterError("prompt_empty", f"Prompt file is empty: {path}", exit_code=EXIT_INPUT)
    return prompt


def requested_canvas(prompt: str) -> str | None:
    match = CANVAS_PATTERN.search(prompt)
    if not match:
        return None
    return f"{int(match.group(1))}x{int(match.group(2))}"


def closest_provider_size(prompt: str, canvas: str | None) -> str:
    if canvas:
        width, height = (int(value) for value in canvas.split("x", 1))
        if width > height:
            return "1536x1024"
        if height > width:
            return "1024x1536"
        return "1024x1024"
    lowered = prompt.lower()
    if "landscape" in lowered:
        return "1536x1024"
    if "portrait" in lowered:
        return "1024x1536"
    return "1024x1024"


def build_payload(prompt: str, *, model: str, size: str) -> dict[str, Any]:
    return {
        "model": model,
        "prompt": prompt,
        "type": "text",
        "images": [],
        "size": size,
        "n": 1,
        "response_format": "url",
    }


def sanitized_text(value: str, api_key: str | None, *, limit: int = 500) -> str:
    text = value.replace(api_key, "[REDACTED]") if api_key else value
    text = " ".join(text.replace("\x00", "").split())
    return text[:limit]


def parse_curl_output(stdout: bytes, api_key: str) -> tuple[str, int | None, str | None]:
    text = stdout.decode("utf-8", errors="replace")
    marker = f"\n{HTTP_STATUS_MARKER}"
    if marker not in text:
        return text, None, None
    body, metadata = text.rsplit(marker, 1)
    lines = metadata.splitlines()
    try:
        status = int(lines[0].strip())
    except (IndexError, ValueError):
        status = None
    content_type = None
    for line in lines[1:]:
        if line.startswith(CONTENT_TYPE_MARKER):
            content_type = line[len(CONTENT_TYPE_MARKER) :].strip() or None
    return body, status, content_type


def curl_write_out() -> str:
    return f"\n{HTTP_STATUS_MARKER}%{{http_code}}\n{CONTENT_TYPE_MARKER}%{{content_type}}"


def run_curl(
    arguments: list[str],
    *,
    timeout: float,
    api_key: str,
    runner: Callable[..., Any] = subprocess.run,
) -> tuple[str, int | None, str | None]:
    try:
        completed = runner(
            arguments,
            capture_output=True,
            check=False,
            shell=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AdapterError("provider_timeout", f"curl exceeded {timeout:g} seconds", exit_code=EXIT_PROVIDER) from exc
    except OSError as exc:
        raise AdapterError("provider_dependency_missing", f"Unable to execute system curl: {exc}", exit_code=EXIT_CONFIG) from exc
    body, status, content_type = parse_curl_output(completed.stdout or b"", api_key)
    if completed.returncode != 0:
        stderr = (completed.stderr or b"").decode("utf-8", errors="replace")
        diagnostic = sanitized_text(stderr or body, api_key)
        status_note = f", HTTP {status}" if status else ""
        raise AdapterError(
            "provider_request_failed",
            f"curl failed with exit code {completed.returncode}{status_note}: {diagnostic or 'no diagnostic output'}",
            exit_code=EXIT_PROVIDER,
        )
    if status is None:
        raise AdapterError("provider_response_invalid", "curl response did not include an HTTP status", exit_code=EXIT_PROVIDER)
    if status < 200 or status >= 300:
        preview = sanitized_text(body, api_key)
        raise AdapterError(
            "provider_http_error",
            f"Provider HTTP request failed with status {status}: {preview or 'empty response body'}",
            exit_code=EXIT_PROVIDER,
        )
    return body, status, content_type


def curl_json_request(
    method: str,
    url: str,
    *,
    curl_path: str,
    api_key: str,
    timeout: float,
    payload: dict[str, Any] | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    request_file: Path | None = None
    try:
        arguments = [
            curl_path,
            "--silent",
            "--show-error",
            "--location",
            "--fail-with-body",
            "--max-time",
            f"{timeout:g}",
            "--request",
            method,
            "--header",
            f"Authorization: Bearer {api_key}",
            "--write-out",
            curl_write_out(),
        ]
        if payload is not None:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, prefix="game-ui-provider-", suffix=".json") as file:
                request_file = Path(file.name)
                json.dump(payload, file, ensure_ascii=False, separators=(",", ":"))
                file.flush()
                os.fsync(file.fileno())
            arguments.extend(
                [
                    "--header",
                    "Content-Type: application/json",
                    "--data-binary",
                    f"@{request_file}",
                ]
            )
        arguments.append(url)
        body, status, _ = run_curl(arguments, timeout=timeout, api_key=api_key, runner=runner)
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            preview = sanitized_text(body, api_key)
            raise AdapterError(
                "provider_response_invalid",
                f"Provider returned invalid JSON (HTTP {status}): {preview or 'empty response body'}",
                exit_code=EXIT_PROVIDER,
            ) from exc
        if not isinstance(data, dict):
            raise AdapterError("provider_response_invalid", "Provider JSON response must be an object", exit_code=EXIT_PROVIDER)
        return data
    finally:
        if request_file is not None:
            try:
                request_file.unlink(missing_ok=True)
            except OSError:
                pass


def submit_task_id(data: dict[str, Any]) -> str | None:
    nested_data = data.get("data")
    candidates = [
        data.get("id"),
        data.get("task_id"),
        nested_data.get("id") if isinstance(nested_data, dict) else None,
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def json_structure(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(key): json_structure(item, depth=depth + 1) for key, item in list(value.items())[:20]}
    if isinstance(value, list):
        return [] if not value else [json_structure(value[0], depth=depth + 1)]
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def submit_generation(
    payload: dict[str, Any],
    *,
    base_url: str,
    api_key: str,
    timeout: float,
    curl_path: str,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    data = curl_json_request(
        "POST",
        provider_url(base_url, "/v1/images/generations"),
        curl_path=curl_path,
        api_key=api_key,
        timeout=timeout,
        payload=payload,
        runner=runner,
    )
    task_id = submit_task_id(data)
    structure = json.dumps(json_structure(data), ensure_ascii=False, separators=(",", ":"))
    if data.get("success") is False:
        raise AdapterError(
            "provider_response_invalid",
            f"Generation submission reported failure; response structure: {structure}",
            exit_code=EXIT_PROVIDER,
        )
    if task_id is None:
        raise AdapterError(
            "provider_response_invalid",
            f"Generation response did not contain a usable task id; response structure: {structure}",
            exit_code=EXIT_PROVIDER,
        )
    if data.get("id") != task_id:
        data = {**data, "id": task_id}
    return data


def positive_number(value: Any, fallback: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return fallback


def poll_task(
    task_id: str,
    submit_data: dict[str, Any],
    *,
    base_url: str,
    api_key: str,
    poll_interval: float,
    max_wait: float,
    timeout: float,
    curl_path: str,
    runner: Callable[..., Any] = subprocess.run,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> None:
    interval = positive_number(submit_data.get("poll_interval"), poll_interval)
    wait_limit = positive_number(submit_data.get("max_wait"), max_wait)
    status_url = provider_url(base_url, f"/v1/tasks/{task_id}/status")
    started = monotonic_fn()
    while True:
        data = curl_json_request(
            "GET",
            status_url,
            curl_path=curl_path,
            api_key=api_key,
            timeout=timeout,
            runner=runner,
        )
        status = str(data.get("task_status") or data.get("status") or "").lower()
        if status in {"completed", "succeeded", "success", "finished"}:
            return
        if status in {"failed", "error", "cancelled", "canceled"}:
            raise AdapterError("provider_task_failed", "Provider task reported failure", exit_code=EXIT_PROVIDER)
        if status not in {"pending", "in_progress", "processing", "queued"}:
            raise AdapterError("provider_response_invalid", f"Unknown provider task status: {status or '<missing>'}", exit_code=EXIT_PROVIDER)
        elapsed = monotonic_fn() - started
        if elapsed >= wait_limit:
            raise AdapterError("provider_timeout", f"Provider task exceeded {wait_limit:g} seconds", exit_code=EXIT_PROVIDER)
        sleep_fn(min(interval, max(0.0, wait_limit - elapsed)))


def extract_image_url(result: dict[str, Any]) -> str | None:
    candidates: list[Any] = []
    try:
        candidates.append(result["items"][0]["url"])
    except (KeyError, IndexError, TypeError):
        pass
    try:
        candidates.append(result["data"]["result"]["data"][0]["url"])
    except (KeyError, IndexError, TypeError):
        pass
    try:
        candidates.append(result["data"][0]["url"])
    except (KeyError, IndexError, TypeError):
        pass
    for value in candidates:
        if is_http_url(value):
            return value
    return None


def fetch_result(
    task_id: str,
    *,
    base_url: str,
    api_key: str,
    timeout: float,
    curl_path: str,
    runner: Callable[..., Any] = subprocess.run,
) -> str:
    data = curl_json_request(
        "GET",
        provider_url(base_url, f"/v1/tasks/{task_id}/result"),
        curl_path=curl_path,
        api_key=api_key,
        timeout=timeout,
        runner=runner,
    )
    image_url = extract_image_url(data)
    if image_url is None:
        raise AdapterError("provider_response_invalid", "Completed task result did not contain an image URL", exit_code=EXIT_PROVIDER)
    return image_url


def image_extension(prefix: bytes, content_type: str, image_url: str) -> str | None:
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if prefix.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        return ".webp"
    normalized_type = content_type.split(";", 1)[0].strip().lower()
    if normalized_type == "image/png":
        return ".png"
    if normalized_type in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if normalized_type == "image/webp":
        return ".webp"
    suffix = Path(urlparse(image_url).path).suffix.lower()
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".webp"} else None


def download_image(
    image_url: str,
    output_dir: Path,
    *,
    timeout: float,
    curl_path: str,
    api_key: str,
    runner: Callable[..., Any] = subprocess.run,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, dir=output_dir, prefix=".preview.", suffix=".part") as file:
            temporary = Path(file.name)
        arguments = [
            curl_path,
            "--silent",
            "--show-error",
            "--location",
            "--fail-with-body",
            "--max-time",
            f"{timeout:g}",
            "--output",
            str(temporary),
            "--write-out",
            curl_write_out(),
            image_url,
        ]
        _, _, content_type = run_curl(arguments, timeout=timeout, api_key=api_key, runner=runner)
        prefix = temporary.read_bytes()[:16]
        extension = image_extension(prefix, content_type or "", image_url)
        if temporary.stat().st_size == 0 or extension is None:
            raise AdapterError("provider_response_invalid", "Provider image response was empty or not a supported image", exit_code=EXIT_PROVIDER)
        if extension == ".jpeg":
            extension = ".jpg"
        output_path = output_dir / f"preview{extension}"
        os.replace(temporary, output_path)
        temporary = None
        return output_path
    except AdapterError:
        raise
    except OSError as exc:
        raise AdapterError("image_save_failed", f"Unable to save provider image: {exc}", exit_code=EXIT_OUTPUT) from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def write_result(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, dir=path.parent, prefix=f".{path.name}.", suffix=".tmp") as file:
            temporary = Path(file.name)
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        temporary = None
    except Exception as exc:
        raise AdapterError("result_write_failed", f"Unable to write result.json: {exc}", exit_code=EXIT_OUTPUT) from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def relative_prompt_source(prompt_path: Path, output_dir: Path) -> str:
    try:
        return Path(os.path.relpath(prompt_path.resolve(strict=False), output_dir.resolve(strict=False))).as_posix()
    except (OSError, ValueError):
        return str(prompt_path)


def validate_positive_args(args: argparse.Namespace) -> None:
    for name in ("poll_interval", "max_wait", "request_timeout", "download_timeout"):
        if getattr(args, name) <= 0:
            raise AdapterError("invalid_argument", f"--{name.replace('_', '-')} must be greater than zero", exit_code=EXIT_INPUT)


def sanitized_message(message: str, api_key: str | None) -> str:
    return message.replace(api_key, "[REDACTED]") if api_key else message


def run(
    args: argparse.Namespace,
    *,
    runner: Callable[..., Any] = subprocess.run,
    curl_path: str | None = None,
) -> tuple[int, dict[str, Any]]:
    prompt_path = Path(args.prompt)
    output_dir = Path(args.output_dir)
    result_path = output_dir / "result.json"
    prompt_source = relative_prompt_source(prompt_path, output_dir)
    api_key = os.environ.get("TOAPIS_API_KEY")
    base_url = os.environ.get("TOAPIS_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    canvas: str | None = None
    provider_size: str | None = args.size
    try:
        validate_positive_args(args)
        prompt = read_prompt(prompt_path)
        canvas = requested_canvas(prompt)
        provider_size = provider_size or closest_provider_size(prompt, canvas)
        if not is_http_url(base_url):
            raise AdapterError("provider_config_invalid", "TOAPIS_BASE_URL must be an HTTP(S) URL", exit_code=EXIT_CONFIG)
        if not api_key:
            raise AdapterError("provider_config_missing", "TOAPIS_API_KEY is required", exit_code=EXIT_CONFIG)
        active_curl = curl_path or find_curl()
        payload = build_payload(prompt, model=args.model, size=provider_size)
        submit_data = submit_generation(
            payload,
            base_url=base_url,
            api_key=api_key,
            timeout=args.request_timeout,
            curl_path=active_curl,
            runner=runner,
        )
        task_id = str(submit_data["id"])
        poll_task(
            task_id,
            submit_data,
            base_url=base_url,
            api_key=api_key,
            poll_interval=args.poll_interval,
            max_wait=args.max_wait,
            timeout=args.request_timeout,
            curl_path=active_curl,
            runner=runner,
        )
        image_url = fetch_result(
            task_id,
            base_url=base_url,
            api_key=api_key,
            timeout=args.request_timeout,
            curl_path=active_curl,
            runner=runner,
        )
        output_path = download_image(
            image_url,
            output_dir,
            timeout=args.download_timeout,
            curl_path=active_curl,
            api_key=api_key,
            runner=runner,
        )
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": "success",
            "provider": PROVIDER,
            "model": args.model,
            "prompt_source": prompt_source,
            "requested_canvas": canvas,
            "provider_size": provider_size,
            "output_image": output_path.name,
        }
        write_result(result_path, result)
        return 0, result
    except AdapterError as exc:
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": "error",
            "provider": PROVIDER,
            "model": args.model,
            "error_type": exc.error_type,
            "error_message": sanitized_message(exc.message, api_key),
        }
        try:
            write_result(result_path, result)
        except AdapterError:
            pass
        return exc.exit_code, result
    except Exception as exc:  # defensive CLI boundary
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": "error",
            "provider": PROVIDER,
            "model": args.model,
            "error_type": "unexpected_adapter_error",
            "error_message": sanitized_message(f"Unexpected adapter error: {exc}", api_key),
        }
        try:
            write_result(result_path, result)
        except AdapterError:
            pass
        return EXIT_OUTPUT, result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate one game UI preview from image-prompt.txt through ToAPIs.")
    parser.add_argument("--prompt", required=True, help="Path to image-prompt.txt")
    parser.add_argument("--output-dir", required=True, help="Directory for preview image and result.json")
    parser.add_argument("--provider", choices=["toapis"], default="toapis")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--size", choices=SUPPORTED_SIZES)
    parser.add_argument("--poll-interval", type=float, default=3.0)
    parser.add_argument("--max-wait", type=float, default=300.0)
    parser.add_argument("--request-timeout", type=float, default=120.0)
    parser.add_argument("--download-timeout", type=float, default=180.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = parse_args(argv)
    exit_code, result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
