#!/usr/bin/env python3
"""Generate one pure-text game UI preview through the ToAPIs provider."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - environment-specific
    requests = None  # type: ignore[assignment]


SCHEMA_VERSION = "0.1"
PROVIDER = "ToAPIs"
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_BASE_URL = "https://ai-api.youchu.work"
SUPPORTED_SIZES = ("1024x1024", "1536x1024", "1024x1536")

EXIT_INPUT = 2
EXIT_CONFIG = 3
EXIT_PROVIDER = 4
EXIT_OUTPUT = 5

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


def authorization(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def response_json(response: Any, error_type: str) -> dict[str, Any]:
    try:
        response.raise_for_status()
    except Exception as exc:
        status = getattr(response, "status_code", "unknown")
        raise AdapterError(error_type, f"Provider HTTP request failed with status {status}", exit_code=EXIT_PROVIDER) from exc
    try:
        data = response.json()
    except (ValueError, TypeError) as exc:
        raise AdapterError("provider_response_invalid", "Provider response was not valid JSON", exit_code=EXIT_PROVIDER) from exc
    if not isinstance(data, dict):
        raise AdapterError("provider_response_invalid", "Provider response must be a JSON object", exit_code=EXIT_PROVIDER)
    return data


def submit_generation(
    payload: dict[str, Any], *, base_url: str, api_key: str, timeout: float, session: Any
) -> dict[str, Any]:
    try:
        response = session.post(
            provider_url(base_url, "/v1/images/generations"),
            headers={**authorization(api_key), "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
    except Exception as exc:
        raise AdapterError("provider_request_failed", f"Generation submission failed: {exc}", exit_code=EXIT_PROVIDER) from exc
    data = response_json(response, "provider_http_error")
    task_id = data.get("id")
    if data.get("success") is False or not isinstance(task_id, str) or not task_id.strip():
        raise AdapterError("provider_response_invalid", "Generation response did not contain an id", exit_code=EXIT_PROVIDER)
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
    session: Any,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> None:
    interval = positive_number(submit_data.get("poll_interval"), poll_interval)
    wait_limit = positive_number(submit_data.get("max_wait"), max_wait)
    status_url = provider_url(base_url, f"/v1/tasks/{task_id}/status")
    started = monotonic_fn()
    while True:
        try:
            response = session.get(status_url, headers=authorization(api_key), timeout=timeout)
        except Exception as exc:
            raise AdapterError("provider_request_failed", f"Task status request failed: {exc}", exit_code=EXIT_PROVIDER) from exc
        data = response_json(response, "provider_http_error")
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


def fetch_result(task_id: str, *, base_url: str, api_key: str, timeout: float, session: Any) -> str:
    try:
        response = session.get(
            provider_url(base_url, f"/v1/tasks/{task_id}/result"),
            headers=authorization(api_key),
            timeout=timeout,
        )
    except Exception as exc:
        raise AdapterError("provider_request_failed", f"Task result request failed: {exc}", exit_code=EXIT_PROVIDER) from exc
    image_url = extract_image_url(response_json(response, "provider_http_error"))
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


def download_image(image_url: str, output_dir: Path, *, timeout: float, session: Any) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    prefix = bytearray()
    total = 0
    try:
        response = session.get(image_url, stream=True, timeout=timeout)
        response.raise_for_status()
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, dir=output_dir, prefix=".preview.", suffix=".part") as file:
            temporary = Path(file.name)
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if len(prefix) < 16:
                    prefix.extend(chunk[: 16 - len(prefix)])
                file.write(chunk)
                total += len(chunk)
            file.flush()
            os.fsync(file.fileno())
        headers = getattr(response, "headers", {}) or {}
        extension = image_extension(bytes(prefix), str(headers.get("Content-Type", "")), image_url)
        if total == 0 or extension is None:
            raise AdapterError("provider_response_invalid", "Provider image response was empty or not a supported image", exit_code=EXIT_PROVIDER)
        if extension == ".jpeg":
            extension = ".jpg"
        output_path = output_dir / f"preview{extension}"
        os.replace(temporary, output_path)
        temporary = None
        return output_path
    except AdapterError:
        raise
    except Exception as exc:
        raise AdapterError("image_save_failed", f"Unable to download or save provider image: {exc}", exit_code=EXIT_OUTPUT) from exc
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


def run(args: argparse.Namespace, *, session: Any = None) -> tuple[int, dict[str, Any]]:
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
        if requests is None and session is None:
            raise AdapterError("provider_dependency_missing", "The requests package is required", exit_code=EXIT_CONFIG)
        active_session = session if session is not None else requests.Session()
        payload = build_payload(prompt, model=args.model, size=provider_size)
        submit_data = submit_generation(payload, base_url=base_url, api_key=api_key, timeout=args.request_timeout, session=active_session)
        task_id = str(submit_data["id"])
        poll_task(
            task_id,
            submit_data,
            base_url=base_url,
            api_key=api_key,
            poll_interval=args.poll_interval,
            max_wait=args.max_wait,
            timeout=args.request_timeout,
            session=active_session,
        )
        image_url = fetch_result(task_id, base_url=base_url, api_key=api_key, timeout=args.request_timeout, session=active_session)
        output_path = download_image(image_url, output_dir, timeout=args.download_timeout, session=active_session)
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
