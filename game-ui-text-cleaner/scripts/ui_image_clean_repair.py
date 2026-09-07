#!/usr/bin/env python3
"""Stage0 Image-2 clean repair — migrated from game-ui-auto-composer-skill/scripts/ui_image_clean_repair_poc.py.

Contract: IMAGE 1 = authoritative source UI screenshot; IMAGE 2 = aligned repair
guide overlay (only for locating repair regions). The provider must repaint only
what the overlay marks, keeping everything else pixel-identical.

Input modes:
- source_plus_overlay (production): source + overlay + frozen prompt
- alpha_hole_only (experimental): transparent-hole source + frozen prompt + alpha probe
- source_only: unsupported / non-production — fails fast (no built-in prompt)

Provider notes:
- Sync image-result protocol (image items directly in the create response) and
  async task protocol (task_id polling) are both supported. Protocol detection
  lives in this module (self-contained) — the shared adapters are NOT modified.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from importlib.util import module_from_spec, spec_from_file_location
from types import ModuleType


def _load_repository_module(name: str, relative_path: str) -> ModuleType:
    repository_root = Path(__file__).resolve().parents[2]
    module_path = repository_root / relative_path
    spec = spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load repository helper: {module_path}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


toapis = _load_repository_module(
    "_clean_repair_toapis_preview_adapter",
    "game-ui-auto-composer-skill/scripts/toapis_preview_adapter.py",
)
provider_helpers = _load_repository_module(
    "_clean_repair_image_provider_adapter",
    "game-ui-image-provider-adapter/scripts/generate_preview.py",
)
_provider_sanitized_text = provider_helpers.sanitized_text


def _provider_error_preview(
    value: str, api_key: str | None, *, limit: int = 2000
) -> str:
    """Keep the verified helper's redaction with a 2000-character body cap."""

    return _provider_sanitized_text(value, api_key, limit=limit)


provider_helpers.sanitized_text = _provider_error_preview

SCHEMA_VERSION = "image2-clean-repair-v0.1"

SYNC_RESULT_PROTOCOL = "openai_images_sync"
ASYNC_TASK_PROTOCOL = "async_task"

SOURCE_PLUS_OVERLAY_PROMPT = (
    "IMAGE 1 is the authoritative game UI screenshot. IMAGE 2 is an aligned "
    "repair guide overlay: regions that need text cleanup are marked with "
    "semi-transparent color. Repaint ONLY the marked regions in IMAGE 1 so the "
    "text is cleanly removed, filling them with a natural continuation of the "
    "surrounding background and decoration. Keep every unmarked pixel of IMAGE 1 "
    "exactly identical: same composition, same colors, same lighting, same "
    "geometry, same rendering style. Do not move, resize, restyle, or redesign "
    "any UI element. Do not add new elements. Output a single image at the same "
    "resolution as IMAGE 1."
)

ALPHA_HOLE_ONLY_PROMPT = (
    "The image contains one or more fully transparent holes where text used to "
    "be. Fill each transparent hole with a natural continuation of the "
    "surrounding background and decoration so the result looks like the text "
    "was never there. Keep every non-transparent pixel exactly identical. Do "
    "not move, resize, restyle, or redesign any UI element. Do not add new "
    "elements. Output a single image at the same resolution as the input, with "
    "fully opaque pixels everywhere."
)


def detect_result_protocol(submit_data: Dict[str, Any]) -> str:
    """Classify a provider create-response as sync image result or async task."""
    if isinstance(submit_data, dict):
        data = submit_data.get("data")
        if isinstance(data, list) and data:
            if all(isinstance(item, dict) and ("b64_json" in item or "url" in item) for item in data):
                return SYNC_RESULT_PROTOCOL
        if submit_data.get("task_id") or submit_data.get("id"):
            return ASYNC_TASK_PROTOCOL
    raise ValueError(
        "Unrecognized provider response shape: no sync image items and no task id"
    )


def extract_sync_image_items(submit_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = submit_data.get("data")
    if not isinstance(items, list) or not items:
        raise ValueError("Sync protocol response has no image items")
    return [item for item in items if isinstance(item, dict)]


def inspect_image(path: Path) -> Tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        return im.size


def inspect_alpha_hole(path: Path) -> Dict[str, Any]:
    from PIL import Image

    with Image.open(path) as im:
        rgba = im.convert("RGBA")
        width, height = rgba.size
        alpha = rgba.getchannel("A")
        min_alpha = min(alpha.getdata())
        histogram = alpha.histogram()
        fully_transparent = histogram[0]
        return {
            "size": [width, height],
            "min_alpha": min_alpha,
            "fully_transparent_pixels": fully_transparent,
            "has_fully_transparent_hole": fully_transparent > 0,
        }


def select_provider_size(source_size: Tuple[int, int]) -> str:
    width, height = source_size
    return f"{width}x{height}"


def read_prompt(args: argparse.Namespace, mode: str) -> str:
    """Resolve the effective prompt; source_only fails fast (no invented prompt)."""
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
        if prompt:
            return prompt
    if mode == "source_plus_overlay":
        return SOURCE_PLUS_OVERLAY_PROMPT
    if mode == "alpha_hole_only":
        return ALPHA_HOLE_ONLY_PROMPT
    raise ValueError(
        "source_only mode has no built-in prompt and is not a production path; "
        "use --prompt-file or choose source_plus_overlay / alpha_hole_only"
    )


def build_generation_payload(
    *,
    model: str,
    prompt: str,
    size: str,
    image_urls: List[str],
) -> Dict[str, Any]:
    return {
        "model": model,
        "type": "image",
        "images": image_urls,
        "prompt": prompt,
        "size": size,
        "n": 1,
        "response_format": "url",
    }


def upload_image_for_clean_repair(
    *,
    base_url: str,
    api_key: str,
    timeout: float,
    session: requests.Session,
    image_path: Path,
) -> str:
    """Upload one local image and return its public URL (explicit MIME required)."""
    suffix = image_path.suffix.lower()
    mime_by_suffix = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    mime = mime_by_suffix.get(suffix)
    if mime is None:
        raise ValueError(f"Unsupported image suffix for upload: {suffix}")
    with image_path.open("rb") as fh:
        response = session.post(
            f"{base_url}/api/upload",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (image_path.name, fh, mime)},
            timeout=timeout,
        )
    response.raise_for_status()
    payload = response.json()
    url = payload.get("url")
    if not isinstance(url, str) or not toapis.is_http_url(url):
        raise ValueError(
            f"Upload response has no valid url: {_provider_error_preview(str(payload), api_key)}"
        )
    return url


def _download_clean_image(
    *,
    image_url: str,
    api_key: str,
    timeout: float,
    session: requests.Session,
    output_path: Path,
) -> Tuple[int, int]:
    from PIL import Image

    response = session.get(
        image_url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
        stream=True,
    )
    response.raise_for_status()
    raw = response.content
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(raw)
    import io

    with Image.open(io.BytesIO(raw)) as im:
        return im.size


def _save_base64_clean_image(b64_data: str, output_path: Path) -> Tuple[int, int]:
    from PIL import Image
    import io

    raw = base64.b64decode(b64_data)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(raw)
    with Image.open(io.BytesIO(raw)) as im:
        return im.size


def _save_sync_clean_image(
    item: Dict[str, Any],
    *,
    output_path: Path,
    api_key: str,
    timeout: float,
    session: requests.Session,
) -> Tuple[str, Optional[str], Optional[Tuple[int, int]]]:
    if item.get("b64_json"):
        size = _save_base64_clean_image(item["b64_json"], output_path)
        return "b64", None, size
    image_url = item.get("url")
    if isinstance(image_url, str) and toapis.is_http_url(image_url):
        size = _download_clean_image(
            image_url=image_url,
            api_key=api_key,
            timeout=timeout,
            session=session,
            output_path=output_path,
        )
        return "url", image_url, size
    raise ValueError("Sync image item has neither b64_json nor a valid url")


def submit_generation_for_clean_repair(
    *,
    payload: Dict[str, Any],
    base_url: str,
    api_key: str,
    timeout: float,
    session: requests.Session,
) -> Dict[str, Any]:
    """Submit via main generate_preview curl path (curl_path discovered here)."""
    return provider_helpers.submit_generation(
        payload,
        base_url=base_url,
        api_key=api_key,
        timeout=timeout,
        curl_path=provider_helpers.find_curl(),
    )


def run(
    *,
    source_image: Path,
    output_dir: Path,
    mask_overlay: Optional[Path],
    mode: str,
    provider_base_url: str,
    api_key: str,
    model: str,
    timeout: float,
    prompt_file: Optional[Path],
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    source_size = inspect_image(source_image)
    provider_size = select_provider_size(source_size)

    if mode == "source_plus_overlay":
        if mask_overlay is None:
            raise ValueError("source_plus_overlay mode requires --mask-overlay")
        upload_sources = [source_image, mask_overlay]
    elif mode == "alpha_hole_only":
        upload_sources = [source_image]
    elif mode == "source_only":
        upload_sources = [source_image]
    else:
        raise ValueError(f"Unknown mode: {mode}")

    # Prompt resolution happens first so source_only fails fast
    # before any network activity.
    class _PromptArgs:
        pass

    ns = _PromptArgs()
    ns.prompt_file = prompt_file
    prompt = read_prompt(ns, mode)

    image_urls = []
    for path in upload_sources:
        image_urls.append(
            upload_image_for_clean_repair(
                base_url=provider_base_url,
                api_key=api_key,
                timeout=timeout,
                session=session,
                image_path=path,
            )
        )

    payload = build_generation_payload(
        model=model,
        prompt=prompt,
        size=provider_size,
        image_urls=image_urls,
    )

    submit_data = submit_generation_for_clean_repair(
        payload=payload,
        base_url=provider_base_url,
        api_key=api_key,
        timeout=timeout,
        session=session,
    )

    result_protocol = detect_result_protocol(submit_data)

    result: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "mode": mode,
        "provider": "toapis",
        "model": model,
        "source_image": str(source_image),
        "mask_overlay": str(mask_overlay) if mask_overlay else None,
        "source_size": list(source_size),
        "provider_size": provider_size,
        "output_size": None,
        "output_matches_source_size": None,
        "output_image": None,
        "task_id": None,
        "image_url": None,
        "prompt": prompt,
        "result_protocol": result_protocol,
        "create_debug": None,
        "poll_debug": None,
        "alpha_probe": None,
    }

    if mode == "alpha_hole_only":
        result["alpha_probe"] = inspect_alpha_hole(source_image)

    if result_protocol == SYNC_RESULT_PROTOCOL:
        items = extract_sync_image_items(submit_data)
        source_ext = source_image.suffix.lower()
        ext = source_ext if source_ext in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
        output_path = output_dir / f"clean{ext}"
        transport, image_url, output_size = _save_sync_clean_image(
            items[0],
            output_path=output_path,
            api_key=api_key,
            timeout=timeout,
            session=session,
        )
        result["output_image"] = str(output_path)
        result["image_url"] = image_url
        result["output_size"] = list(output_size) if output_size else None
        result["output_matches_source_size"] = (
            output_size == source_size if output_size else None
        )
    else:
        task_id = provider_helpers.submit_task_id(submit_data)
        result["task_id"] = task_id
        poll_data = toapis.poll_task_status(
            base_url=provider_base_url,
            api_key=api_key,
            timeout=timeout,
            session=session,
            task_id=task_id,
        )
        result["poll_debug"] = _provider_error_preview(str(poll_data), api_key)
        fetch_data = toapis.fetch_task_result(
            base_url=provider_base_url,
            api_key=api_key,
            timeout=timeout,
            session=session,
            task_id=task_id,
        )
        image_url = toapis.download_image(
            fetch_data,
            output_dir=output_dir,
            session=session,
        )
        output_path = Path(image_url) if image_url else None
        if output_path is not None:
            output_size = inspect_image(output_path)
            result["output_image"] = str(output_path)
            result["image_url"] = None
            result["output_size"] = list(output_size)
            result["output_matches_source_size"] = output_size == source_size
        else:
            result["status"] = "no_image_in_result"

    result_path = output_dir / "result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage0 Image-2 clean repair (source + overlay guide)"
    )
    parser.add_argument("--source-image", required=True, type=Path)
    parser.add_argument("--mask-overlay", type=Path, default=None)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--mode",
        choices=["source_plus_overlay", "alpha_hole_only", "source_only"],
        default="source_plus_overlay",
    )
    parser.add_argument("--provider-base-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--prompt-file", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        result = run(
            source_image=args.source_image,
            output_dir=args.output_dir,
            mask_overlay=args.mask_overlay,
            mode=args.mode,
            provider_base_url=args.provider_base_url,
            api_key=args.api_key,
            model=args.model,
            timeout=args.timeout,
            prompt_file=args.prompt_file,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": result["status"], "output_image": result["output_image"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
