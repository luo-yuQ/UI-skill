#!/usr/bin/env python3
"""Experimental reference-guided clean repair through ToAPIs gpt-image-2.

This PoC deliberately uses the repository's verified image-generation protocol.
The optional overlay is a second ordinary reference image, not an API mask.
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


SCHEMA_VERSION = "clean-repair-poc-v0.1"
PROVIDER = "toapis"
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_BASE_URL = "https://ai-api.youchu.work"
SUPPORTED_SIZES = ("1024x1024", "1024x1536", "1536x1024")

SOURCE_ONLY_PROMPT = """Edit the provided game UI screenshot.

This is a restoration task, not a redesign task.

Remove visible UI text and numeric labels that are rendered on top of
buttons, panels, badges, navigation labels, counters, titles and other
interface surfaces.

Reconstruct the original underlying game UI surfaces naturally where
the text was removed.

Preserve the original UI as closely as possible.

Preserve:
- layout
- component positions
- component sizes
- icons
- illustrations
- item artwork
- logos and decorative artwork
- colors
- gradients
- borders
- shadows
- highlights
- textures

Do not redesign the interface.
Do not move components.
Do not replace icons or artwork.
Do not add new content.
Do not change the overall composition.

Text embedded as part of item artwork, logos, illustrations or decorative
assets should remain intact.

The final result should look like the same original screenshot with the
ordinary UI text removed and the underlying surfaces cleanly restored."""

SOURCE_PLUS_OVERLAY_PROMPT = """You are given two reference images.

The FIRST image is the original game UI screenshot.

The SECOND image is only a repair-region guide derived from the same
screenshot.

Colored/red highlighted regions in the second image indicate areas where
ordinary UI text should be removed and the underlying interface surface
should be reconstructed.

The second image is NOT the desired visual appearance.
Do not reproduce the red overlays or annotations.

Edit the FIRST image.

Only use the SECOND image to understand which areas require text removal.

Restore the underlying:
- button surfaces
- panels
- badges
- gradients
- borders
- shadows
- highlights
- textures

Preserve all other visual content from the first image as closely as
possible.

Do not redesign, rearrange, move or resize UI components.
Do not replace artwork or icons.
Do not reproduce annotation colors.

Preserve text that is visually embedded inside illustrations, item artwork,
logos or decorative assets unless the guide explicitly indicates otherwise.

The desired output is the same original UI with the indicated UI text
naturally removed."""


class CleanRepairError(RuntimeError):
    """Expected PoC input, configuration, provider, or output failure."""


def _load_repository_module(name: str, relative_path: str) -> ModuleType:
    repository_root = Path(__file__).resolve().parents[2]
    module_path = repository_root / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load repository helper: {module_path}")
    module = importlib.util.module_from_spec(spec)
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


def inspect_image(path: Path, label: str) -> tuple[int, int]:
    if not path.exists() or not path.is_file():
        raise CleanRepairError(f"{label} does not exist or is not a file: {path}")
    try:
        with Image.open(path) as image:
            width, height = image.size
            image.verify()
    except (OSError, UnidentifiedImageError) as exc:
        raise CleanRepairError(f"{label} is not a readable image: {path}") from exc
    if width <= 0 or height <= 0:
        raise CleanRepairError(f"{label} dimensions must be positive: {width}x{height}")
    return width, height


def select_provider_size(source_size: tuple[int, int]) -> str:
    """Use the existing provider's square/portrait/landscape size mapping."""

    width, height = source_size
    if width > height:
        return "1536x1024"
    if height > width:
        return "1024x1536"
    return "1024x1024"


def read_prompt(path: Path | None, *, has_overlay: bool) -> str:
    if path is None:
        return SOURCE_PLUS_OVERLAY_PROMPT if has_overlay else SOURCE_ONLY_PROMPT
    if not path.exists() or not path.is_file():
        raise CleanRepairError(f"Prompt file does not exist or is not a file: {path}")
    try:
        prompt = path.read_text(encoding="utf-8-sig").rstrip()
    except (OSError, UnicodeError) as exc:
        raise CleanRepairError(f"Unable to read UTF-8 prompt file: {path}") from exc
    if not prompt.strip():
        raise CleanRepairError(f"Prompt file is empty: {path}")
    return prompt


def build_generation_payload(
    *, model: str, prompt: str, image_urls: list[str], provider_size: str
) -> dict[str, Any]:
    if not image_urls or len(image_urls) > 2:
        raise CleanRepairError("Clean repair requires one or two reference image URLs")
    if provider_size not in SUPPORTED_SIZES:
        raise CleanRepairError(f"Unsupported provider size: {provider_size}")
    return {
        "model": model,
        "type": "image",
        "images": list(image_urls),
        "prompt": prompt,
        "size": provider_size,
        "n": 1,
        "response_format": "url",
    }


def _redact(value: Any, api_key: str | None) -> Any:
    if not api_key:
        return value
    if isinstance(value, str):
        return value.replace(api_key, "[REDACTED]")
    if isinstance(value, dict):
        return {key: _redact(item, api_key) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, api_key) for item in value]
    return value

def upload_image_for_clean_repair(
    image_path: Path,
    *,
    base_url: str,
    api_key: str,
    timeout: float,
    session: Any,
) -> str:
    upload_url = toapis.provider_url(base_url, "/api/upload")

    suffix = image_path.suffix.lower()
    if suffix == ".png":
        mime_type = "image/png"
    elif suffix in {".jpg", ".jpeg"}:
        mime_type = "image/jpeg"
    elif suffix == ".webp":
        mime_type = "image/webp"
    else:
        raise CleanRepairError(
            f"Unsupported image type for upload: {image_path}"
        )

    try:
        with image_path.open("rb") as file:
            response = session.post(
                upload_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                },
                files={
                    "file": (
                        image_path.name,
                        file,
                        mime_type,
                    )
                },
                timeout=timeout,
            )
    except Exception as exc:
        raise CleanRepairError(
            f"Upload request failed for {image_path.name}: {exc}"
        ) from exc

    if not 200 <= response.status_code < 300:
        body = response.text[:2000]
        raise CleanRepairError(
            f"Upload failed for {image_path.name}: "
            f"HTTP {response.status_code}, body={body}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise CleanRepairError(
            f"Upload response was not JSON for {image_path.name}"
        ) from exc

    image_url = data.get("url")

    if not isinstance(image_url, str) or not image_url.strip():
        raise CleanRepairError(
            f"Upload response missing url for {image_path.name}"
        )

    return image_url


# Keep the verified clean-repair multipart upload implementation while exposing
# the adapter's existing injection boundary to offline tests.
toapis.upload_image = upload_image_for_clean_repair


def _download_clean_image(
    image_url: str,
    output_dir: Path,
    *,
    timeout: float,
    session: Any,
) -> tuple[Path, tuple[int, int]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, dir=output_dir, prefix=".clean.", suffix=".download"
        ) as file:
            temporary = Path(file.name)
        toapis.download_image(image_url, temporary, timeout=timeout, session=session)
        prefix = temporary.read_bytes()[:16]
        extension = provider_helpers.image_extension(prefix, "", image_url)
        if extension is None:
            raise CleanRepairError("Provider result is not a supported PNG, JPEG, or WebP image")
        if extension == ".jpeg":
            extension = ".jpg"
        output_size = inspect_image(temporary, "Downloaded provider image")
        output_path = output_dir / f"clean{extension}"
        os.replace(temporary, output_path)
        temporary = None
        return output_path, output_size
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _result_base(
    *,
    status: str,
    mode: str,
    model: str,
    source_image: Path,
    mask_overlay: Path | None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "mode": mode,
        "provider": PROVIDER,
        "model": model,
        "source_image": str(source_image),
        "mask_overlay": str(mask_overlay) if mask_overlay is not None else None,
    }

def submit_generation_for_clean_repair(
    payload: dict[str, Any],
    *,
    base_url: str,
    api_key: str,
    timeout: float,
    session: Any,
) -> dict[str, Any]:
    del session  # Only upload, poll, fetch, and download use requests.
    try:
        data = provider_helpers.submit_generation(
            payload,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            curl_path=provider_helpers.find_curl(),
        )
    except Exception as exc:
        message = str(exc).replace(api_key, "[REDACTED]")
        raise CleanRepairError(f"Generation submit request failed: {message}") from exc

    task_id = provider_helpers.submit_task_id(data)
    if not isinstance(task_id, str) or not task_id.strip():
        raise CleanRepairError(
            "Generation submit response missing a non-empty task_id"
        )

    return data


# This module is loaded privately for the PoC. Preserve its injectable submit
# boundary while making the production implementation use verified curl.
toapis.submit_generation = submit_generation_for_clean_repair


def run(args: argparse.Namespace, *, session: Any = None) -> tuple[int, dict[str, Any]]:
    source_image = Path(args.image)
    mask_overlay = Path(args.mask_overlay) if args.mask_overlay else None
    prompt_file = Path(args.prompt_file) if args.prompt_file else None
    output_dir = Path(args.output_dir)
    result_path = output_dir / "result.json"
    mode = "source_plus_overlay" if mask_overlay is not None else "source_only"
    api_key = os.environ.get("TOAPIS_API_KEY")
    base_url = os.environ.get("TOAPIS_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    task_id: str | None = None

    try:
        for name in (
            "upload_timeout",
            "request_timeout",
            "download_timeout",
            "poll_interval",
            "max_wait",
        ):
            if getattr(args, name) <= 0:
                raise CleanRepairError(f"--{name.replace('_', '-')} must be greater than zero")
        if not args.model.strip():
            raise CleanRepairError("--model must not be empty")
        if not toapis.is_http_url(base_url):
            raise CleanRepairError("TOAPIS_BASE_URL must be an absolute HTTP(S) URL")
        if not api_key:
            raise CleanRepairError("TOAPIS_API_KEY is required")
        if session is None and toapis.requests is None:
            raise CleanRepairError("The requests package is required for real provider calls")

        source_size = inspect_image(source_image, "Source image")
        if mask_overlay is not None:
            overlay_size = inspect_image(mask_overlay, "Mask overlay")
            if overlay_size != source_size:
                raise CleanRepairError(
                    "Mask overlay dimensions must match the source image: "
                    f"{overlay_size[0]}x{overlay_size[1]} != {source_size[0]}x{source_size[1]}"
                )
        prompt = read_prompt(prompt_file, has_overlay=mask_overlay is not None)
        provider_size = select_provider_size(source_size)
        active_session = session if session is not None else toapis.requests

        source_url = toapis.upload_image(
            source_image,
            base_url=base_url,
            api_key=api_key,
            timeout=args.upload_timeout,
            session=active_session,
        )
        image_urls = [source_url]
        if mask_overlay is not None:
            overlay_url = toapis.upload_image(
                mask_overlay,
                base_url=base_url,
                api_key=api_key,
                timeout=args.upload_timeout,
                session=active_session,
            )
            image_urls.append(overlay_url)

        payload = build_generation_payload(
            model=args.model,
            prompt=prompt,
            image_urls=image_urls,
            provider_size=provider_size,
        )
        submit_data = toapis.submit_generation(
            payload,
            base_url=base_url,
            api_key=api_key,
            timeout=args.request_timeout,
            session=active_session,
        )
        parsed_task_id = provider_helpers.submit_task_id(submit_data)
        if not isinstance(parsed_task_id, str) or not parsed_task_id.strip():
            raise CleanRepairError(
                "Generation submit response missing a non-empty task_id"
            )
        task_id = parsed_task_id
        toapis.poll_task_status(
            task_id,
            submit_data,
            base_url=base_url,
            api_key=api_key,
            poll_interval=args.poll_interval,
            max_wait=args.max_wait,
            timeout=args.request_timeout,
            session=active_session,
        )
        _, image_url = toapis.fetch_task_result(
            task_id,
            base_url=base_url,
            api_key=api_key,
            timeout=args.request_timeout,
            session=active_session,
        )
        output_path, output_size = _download_clean_image(
            image_url,
            output_dir,
            timeout=args.download_timeout,
            session=active_session,
        )
        result = {
            **_result_base(
                status="success",
                mode=mode,
                model=args.model,
                source_image=source_image,
                mask_overlay=mask_overlay,
            ),
            "source_size": {"width": source_size[0], "height": source_size[1]},
            "provider_size": provider_size,
            "output_size": {"width": output_size[0], "height": output_size[1]},
            "output_matches_source_size": output_size == source_size,
            "output_image": output_path.name,
            "task_id": task_id,
            "image_url": image_url,
            "prompt": prompt,
        }
        result = _redact(result, api_key)
        toapis.write_result_json(result_path, result)
        return 0, result
    except Exception as exc:
        result = {
            **_result_base(
                status="error",
                mode=mode,
                model=args.model,
                source_image=source_image,
                mask_overlay=mask_overlay,
            ),
            "task_id": task_id,
            "error_type": getattr(exc, "error_code", type(exc).__name__),
            "error_message": str(exc),
        }
        result = _redact(result, api_key)
        try:
            toapis.write_result_json(result_path, result)
        except Exception:
            result["result_json_write_failed"] = True
        return 2, result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Experimentally remove ordinary game UI text with ToAPIs gpt-image-2 reference generation."
    )
    parser.add_argument("--image", required=True, help="Source game UI screenshot")
    parser.add_argument("--output-dir", required=True, help="Directory for clean image and result.json")
    parser.add_argument("--mask-overlay", help="Optional repair-mask-overlay.png used only as a visual guide")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt-file", help="Optional UTF-8 prompt override")
    parser.add_argument("--upload-timeout", type=float, default=120.0)
    parser.add_argument("--request-timeout", type=float, default=120.0)
    parser.add_argument("--download-timeout", type=float, default=180.0)
    parser.add_argument("--poll-interval", type=float, default=3.0)
    parser.add_argument("--max-wait", type=float, default=300.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    code, result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
