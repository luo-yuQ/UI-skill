#!/usr/bin/env python3
"""Probe gpt-image-2 preservation using original reference-file bytes."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from PIL import Image, UnidentifiedImageError


SOURCE_DIR = Path(
    r"D:\Third_Test_1\UI-skill\runs\20260824_stage0_text_extract_003\inputs"
)
DEFAULT_OUTPUT_DIR = Path("outputs/image2-original-reference-probe")
MODEL = "gpt-image-2"
PROMPT = """Use the supplied image as the authoritative visual reference.

Preserve the original UI composition, layout, icons, artwork, controls,
small functional elements, edge elements, and spatial relationships as faithfully as possible.

Do not omit small UI icons.
Do not simplify the interface.
Do not redesign or rearrange components.

Generate a visually faithful version of the supplied game UI."""
IMAGE_NAMES = (
    "analysis-image.png",
    "beibao_wzry.jpg",
    "huodong_wzry.jpg",
    "recharge_ui.jpg",
    "shezhi_wzry.jpg",
    "store_wzry.jpg",
)
DEFAULT_BASE_URL = "https://ai-api.youchu.work"
UPLOAD_TIMEOUT = 120.0
REQUEST_TIMEOUT = 120.0
DOWNLOAD_TIMEOUT = 180.0
POLL_INTERVAL = 3.0
MAX_WAIT = 300.0


def load_module(name: str, relative_path: str) -> ModuleType:
    root = Path(__file__).resolve().parents[2]
    path = root / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


provider = load_module(
    "image2_original_reference_probe_provider",
    "game-ui-image-provider-adapter/scripts/generate_preview.py",
)
toapis = load_module(
    "image2_original_reference_probe_toapis",
    "game-ui-auto-composer-skill/scripts/toapis_preview_adapter.py",
)


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def image_info(path: Path) -> tuple[int, int, str]:
    try:
        with Image.open(path) as image:
            return image.width, image.height, image.format or ""
    except (OSError, UnidentifiedImageError) as exc:
        raise RuntimeError(f"Unable to read image metadata: {path}") from exc


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def requested_size(width: int, height: int) -> str:
    if width < height:
        return "1024x1536"
    if width > height:
        return "1536x1024"
    return "1024x1024"


def ratio(width: int, height: int) -> float:
    return width / height


def upload_original(
    path: Path, *, base_url: str, api_key: str, session: Any, timeout: float
) -> str:
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }.get(path.suffix.lower(), "application/octet-stream")
    with path.open("rb") as original_file:
        response = session.post(
            toapis.provider_url(base_url, "/api/upload"),
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (path.name, original_file, mime)},
            timeout=UPLOAD_TIMEOUT,
        )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"Upload failed with HTTP {response.status_code}: {response.text[:500]}")
    data = response.json()
    url = data.get("url") if isinstance(data, dict) else None
    if not isinstance(url, str) or not url.strip():
        raise RuntimeError("Upload response did not contain a URL")
    return url.strip()


def build_payload(prompt: str, *, requested: str, reference_url: str) -> dict[str, Any]:
    return {
        "model": MODEL,
        "type": "image",
        "prompt": prompt,
        "images": [reference_url],
        "size": requested,
        "n": 1,
        "response_format": "url",
    }


def inspect_output(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            image.load()
            return image.width, image.height
    except (OSError, UnidentifiedImageError) as exc:
        raise RuntimeError(f"Downloaded output is not a readable image: {path}") from exc


def run_case(path: Path, output_dir: Path, *, base_url: str, api_key: str, curl_path: str, session: Any, args: argparse.Namespace) -> dict[str, Any]:
    width, height, image_format = image_info(path)
    requested = requested_size(width, height)
    case_dir = output_dir / path.stem
    case_dir.mkdir(parents=True, exist_ok=True)
    output_path = case_dir / "output.png"
    output_path.unlink(missing_ok=True)

    source = {
        "file": str(path),
        "width": width,
        "height": height,
        "format": image_format,
    }
    try:
        reference_url = upload_original(
            path,
            base_url=base_url,
            api_key=api_key,
            session=session,
            timeout=args.upload_timeout,
        )
        payload = build_payload(PROMPT, requested=requested, reference_url=reference_url)
        write_json(
            case_dir / "request.json",
            {
                "source_file": str(path),
                "source_size": {"width": width, "height": height},
                "source_format": image_format,
                "source_sha256": sha256(path),
                "requested_size": requested,
                "reference_count": 1,
                "model": MODEL,
                "prompt": PROMPT,
                "payload": payload,
            },
        )
        submitted = provider.curl_json_request(
            "POST",
            provider.provider_url(base_url, "/v1/images/generations"),
            curl_path=curl_path,
            api_key=api_key,
            timeout=args.request_timeout,
            payload=payload,
        )
        task_id = provider.submit_task_id(submitted)
        if not task_id:
            raise RuntimeError("Generation response did not contain a task id")
        write_json(case_dir / "submit.json", submitted)
        provider.poll_task(
            task_id,
            submitted,
            base_url=base_url,
            api_key=api_key,
            poll_interval=args.poll_interval,
            max_wait=args.max_wait,
            timeout=args.request_timeout,
            curl_path=curl_path,
        )
        result_response = provider.curl_json_request(
            "GET",
            provider.provider_url(base_url, f"/v1/tasks/{task_id}/result"),
            curl_path=curl_path,
            api_key=api_key,
            timeout=args.request_timeout,
        )
        write_json(case_dir / "result-response.json", result_response)
        image_url = provider.extract_image_url(result_response)
        if not image_url:
            raise RuntimeError("Task result did not contain an image URL")
        toapis.download_image(image_url, output_path, timeout=args.download_timeout, session=session)
        actual_width, actual_height = inspect_output(output_path)
        result = {
            "status": "success",
            "source_file": str(path),
            "source_size": {"width": width, "height": height},
            "requested_size": requested,
            "actual_output_size": {"width": actual_width, "height": actual_height},
            "source_aspect_ratio": ratio(width, height),
            "requested_aspect_ratio": ratio(*map(int, requested.split("x"))),
            "output_aspect_ratio": ratio(actual_width, actual_height),
            "output_to_source_scale_x": actual_width / width,
            "output_to_source_scale_y": actual_height / height,
            "task_id": task_id,
            "image_url": image_url,
            "output_file": "output.png",
            "manual_visual_check": None,
        }
    except Exception as exc:
        result = {
            "status": "error",
            "source_file": str(path),
            "source_size": {"width": width, "height": height},
            "requested_size": requested,
            "actual_output_size": None,
            "source_aspect_ratio": ratio(width, height),
            "requested_aspect_ratio": ratio(*map(int, requested.split("x"))),
            "output_aspect_ratio": None,
            "output_to_source_scale_x": None,
            "output_to_source_scale_y": None,
            "task_id": None,
            "image_url": None,
            "output_file": "output.png",
            "manual_visual_check": None,
            "error": str(exc),
        }
    write_json(case_dir / "result.json", result)
    return result


def print_summary(results: list[dict[str, Any]]) -> None:
    headers = ("IMAGE", "SOURCE_SIZE", "REQUESTED_SIZE", "ACTUAL_SIZE", "SOURCE_AR", "OUTPUT_AR", "SCALE_X", "SCALE_Y")
    rows = []
    for result in results:
        source = result["source_size"]
        actual = result.get("actual_output_size")
        rows.append((
            Path(result["source_file"]).name,
            f"{source['width']}x{source['height']}",
            result["requested_size"],
            f"{actual['width']}x{actual['height']}" if actual else "ERROR",
            f"{result['source_aspect_ratio']:.6f}",
            f"{result['output_aspect_ratio']:.6f}" if result["output_aspect_ratio"] else "-",
            f"{result['output_to_source_scale_x']:.6f}" if result["output_to_source_scale_x"] else "-",
            f"{result['output_to_source_scale_y']:.6f}" if result["output_to_source_scale_y"] else "-",
        ))
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))]
    print("\n" + "  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(row))))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--upload-timeout", type=float, default=UPLOAD_TIMEOUT)
    parser.add_argument("--request-timeout", type=float, default=REQUEST_TIMEOUT)
    parser.add_argument("--download-timeout", type=float, default=DOWNLOAD_TIMEOUT)
    parser.add_argument("--poll-interval", type=float, default=POLL_INTERVAL)
    parser.add_argument("--max-wait", type=float, default=MAX_WAIT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    api_key = os.environ.get("TOAPIS_API_KEY")
    base_url = os.environ.get("TOAPIS_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    if not api_key:
        print("TOAPIS_API_KEY is required", file=sys.stderr)
        return 2
    if not provider.is_http_url(base_url) or toapis.requests is None:
        print("Invalid ToAPIs configuration or missing requests dependency", file=sys.stderr)
        return 2
    args.output_dir.mkdir(parents=True, exist_ok=True)
    curl_path = provider.find_curl()
    session = toapis.requests.Session()
    results = []
    try:
        for name in IMAGE_NAMES:
            source_path = args.source_dir / name
            width, height, image_format = image_info(source_path)
            print(f"SOURCE_FILE = {source_path}")
            print(f"SOURCE_SIZE = {width}x{height}")
            print(f"SOURCE_FORMAT = {image_format}")
            results.append(run_case(source_path, args.output_dir, base_url=base_url, api_key=api_key, curl_path=curl_path, session=session, args=args))
    finally:
        session.close()
    summary = {"model": MODEL, "prompt": PROMPT, "cases": results}
    write_json(args.output_dir / "summary.json", summary)
    print_summary(results)
    return 0 if all(item["status"] == "success" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
