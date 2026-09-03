#!/usr/bin/env python3
"""Experimental reference-guided clean repair through ToAPIs gpt-image-2.

This PoC deliberately uses the repository's verified image-generation protocol.
The optional overlay is a second ordinary reference image, not an API mask.
"""

from __future__ import annotations

import argparse
import base64
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

SOURCE_PLUS_OVERLAY_PROMPT = """
IMAGE 1 is the authoritative original UI.

IMAGE 2 is a binary spatial repair guide aligned exactly with IMAGE 1.

White regions in IMAGE 2 indicate text-removal targets.
Black regions indicate areas that are not repair targets.

Use IMAGE 2 only to locate repair regions.

Remove the text inside the corresponding white regions and reconstruct the
underlying UI surface.

Preserve all non-target visual content from IMAGE 1 as faithfully as possible.
Do not reproduce IMAGE 2 in the output.
"""

ALPHA_HOLE_ONLY_PROMPT = """
IMAGE 1 is a partially transparent game UI image.

Transparent regions in IMAGE 1 (alpha = 0) are missing areas that must be reconstructed.

Non-transparent regions are authoritative existing UI content and must be preserved as faithfully as possible.

Fill every transparent region naturally by reconstructing the underlying UI surface from the surrounding visual context.

The transparent regions originally contained removable text or text-overlaid UI content. The transparent regions are intentionally erased areas.

Reconstruct background and UI surface only.

Do not reconstruct any original text that may have existed in these regions.

The repaired regions must contain no letters, numbers, words, glyphs, labels, captions, counters, or other textual symbols.

Use the surrounding non-transparent pixels only to infer the underlying non-text visual surface.

Preserve all non-transparent visual content, including layout, colors, icons, shapes, decorations, and UI elements.

Do not redesign the interface.
Do not add new text.
Do not add new UI elements.
Do not leave transparent holes.
Do not fill the holes with black, white, checkerboard patterns, or flat placeholder colors.

Return a complete clean game UI image with all transparent regions naturally repaired.
"""


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


def inspect_alpha_hole(path: Path) -> tuple[int, int]:
    """Validate a real alpha-hole PNG and return its size.

    Requirements:
    - the image must carry a real alpha channel (no local flattening)
    - at least one alpha=0 pixel must exist
    - the image must not be fully transparent
    """

    size = inspect_image(path, "Alpha hole image")
    try:
        with Image.open(path) as image:
            if "A" not in image.getbands():
                raise CleanRepairError(
                    "alpha_hole_only requires an image with an alpha channel"
                )
            alpha = image.getchannel("A")
            alpha_min, alpha_max = alpha.getextrema()
    except (OSError, UnidentifiedImageError) as exc:
        raise CleanRepairError(f"Alpha hole image is not a readable image: {path}") from exc
    if alpha_min > 0:
        raise CleanRepairError(
            f"alpha_hole_only input contains no fully transparent pixels: {path}"
        )
    if alpha_max == 0:
        raise CleanRepairError(f"alpha_hole_only input is fully transparent: {path}")
    return size


def _alpha_extrema(path: Path) -> tuple[str, tuple[int, int] | None]:
    """Return (image_mode, alpha_extrema_or_None) without flattening anything."""

    with Image.open(path) as image:
        image.load()
        mode = image.mode
        if "A" in image.getbands():
            extrema = image.getchannel("A").getextrema()
            return mode, (int(extrema[0]), int(extrema[1]))
        return mode, None


def probe_uploaded_alpha_hole(
    source_image: Path,
    source_url: str,
    output_dir: Path,
    *,
    timeout: float,
    session: Any,
) -> dict[str, Any]:
    """Experimental diagnostic only: check whether alpha survives /api/upload.

    Downloads the uploaded source_url back and compares alpha facts with the
    local file. Never modifies the image or the provider request flow.
    """

    probe: dict[str, Any] = {}
    temporary: Path | None = None
    try:
        local_mode, local_extrema = _alpha_extrema(source_image)
        probe["local_alpha_mode"] = local_mode
        probe["local_alpha_extrema"] = (
            list(local_extrema) if local_extrema is not None else None
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=output_dir,
            prefix=".alpha-probe.",
            suffix=".png",
        ) as file:
            temporary = Path(file.name)
        toapis.download_image(source_url, temporary, timeout=timeout, session=session)
        uploaded_mode, uploaded_extrema = _alpha_extrema(temporary)
        probe["uploaded_alpha_mode"] = uploaded_mode
        probe["uploaded_alpha_extrema"] = (
            list(uploaded_extrema) if uploaded_extrema is not None else None
        )
        probe["uploaded_alpha_preserved"] = bool(
            local_extrema is not None
            and local_extrema[0] == 0
            and uploaded_extrema is not None
            and uploaded_extrema[0] == 0
        )
        return probe
    except Exception as exc:
        probe["error"] = str(exc)
        return probe
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def select_provider_size(source_size: tuple[int, int]) -> str:
    """Use the existing provider's square/portrait/landscape size mapping."""

    width, height = source_size
    if width > height:
        return "1536x1024"
    if height > width:
        return "1024x1536"
    return "1024x1024"


def read_prompt(path: Path | None, *, mode: str) -> str:
    if path is None:
        if mode == "source_plus_overlay":
            return SOURCE_PLUS_OVERLAY_PROMPT
        if mode == "alpha_hole_only":
            return ALPHA_HOLE_ONLY_PROMPT
        # NOTE: SOURCE_ONLY_PROMPT is referenced but was never defined in this
        # PoC, so the previous source_only behavior without a prompt file was
        # a NameError failure. Keep failing fast with an explicit message
        # instead of inventing a prompt.
        raise CleanRepairError(
            "source_only has no built-in SOURCE_ONLY_PROMPT in this PoC; "
            "provide --prompt-file"
        )
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


def _save_base64_clean_image(raw_b64: str, output_dir: Path) -> tuple[Path, tuple[int, int]]:
    """Decode a base64 sync-result payload and save it as the clean image."""

    encoded = raw_b64.strip()
    if encoded.startswith("data:"):
        marker = encoded.find(",")
        if marker < 0:
            raise CleanRepairError("Invalid image data URL")
        encoded = encoded[marker + 1 :]
    try:
        raw = base64.b64decode("".join(encoded.split()), validate=True)
    except Exception as exc:
        raise CleanRepairError("Provider returned invalid base64 image data") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, dir=output_dir, prefix=".clean.", suffix=".b64part"
        ) as file:
            temporary = Path(file.name)
        temporary.write_bytes(raw)
        extension = provider_helpers.image_extension(raw[:16], "", "image.png")
        if extension is None:
            raise CleanRepairError("Sync provider result is not a supported PNG, JPEG, or WebP image")
        if extension == ".jpeg":
            extension = ".jpg"
        output_size = inspect_image(temporary, "Sync provider image")
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


def _save_sync_clean_image(
    item: Any,
    output_dir: Path,
    *,
    timeout: float,
    session: Any,
) -> tuple[Path, str | None, tuple[int, int]]:
    """Consume one direct-result item from a synchronous create response.

    Returns (output_path, image_url_or_None, size). Polling is never used here:
    the create response already carries the final image.
    """

    if isinstance(item, str):
        if toapis.is_http_url(item):
            path, size = _download_clean_image(item, output_dir, timeout=timeout, session=session)
            return path, item, size
        path, size = _save_base64_clean_image(item, output_dir)
        return path, None, size

    if isinstance(item, dict):
        url = item.get("url")
        if toapis.is_http_url(url):
            path, size = _download_clean_image(url, output_dir, timeout=timeout, session=session)
            return path, url, size
        for field in ("b64_json", "base64", "image_base64"):
            value = item.get(field)
            if isinstance(value, str) and value.strip():
                path, size = _save_base64_clean_image(value, output_dir)
                return path, None, size
        nested = item.get("image")
        if isinstance(nested, (dict, str)):
            return _save_sync_clean_image(nested, output_dir, timeout=timeout, session=session)

    raise CleanRepairError("Sync create response item has no usable image URL or base64 data")


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
    debug_sink: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del session  # Only upload, poll, fetch, and download use requests.
    try:
        data = provider_helpers.submit_generation(
            payload,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            curl_path=provider_helpers.find_curl(),
            debug_sink=debug_sink,
        )
    except Exception as exc:
        message = str(exc).replace(api_key, "[REDACTED]")
        raise CleanRepairError(f"Generation submit request failed: {message}") from exc

    # A task_id is mandatory only for the async task protocol. A synchronous
    # create response may already carry the final image without any task id.
    task_id = provider_helpers.submit_task_id(data)
    if toapis.detect_result_protocol(data) == toapis.ASYNC_TASK_PROTOCOL and not (
        isinstance(task_id, str) and task_id.strip()
    ):
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
    if args.input_mode is not None:
        mode = args.input_mode
    else:
        mode = "source_plus_overlay" if mask_overlay is not None else "source_only"
    api_key = os.environ.get("TOAPIS_API_KEY")
    base_url = os.environ.get("TOAPIS_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    task_id: str | None = None
    alpha_probe: dict[str, Any] | None = None
    debug_sink: dict[str, Any] = {}
    poll_debug: dict[str, Any] = {}
    result_protocol: str | None = None

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

        if mode == "alpha_hole_only" and mask_overlay is not None:
            raise CleanRepairError("alpha_hole_only must not be combined with --mask-overlay")
        if mode == "source_only" and mask_overlay is not None:
            raise CleanRepairError("source_only must not be combined with --mask-overlay")
        if mode == "source_plus_overlay" and mask_overlay is None:
            raise CleanRepairError("source_plus_overlay requires --mask-overlay")

        if mode == "alpha_hole_only":
            source_size = inspect_alpha_hole(source_image)
        else:
            source_size = inspect_image(source_image, "Source image")
        if mask_overlay is not None:
            overlay_size = inspect_image(mask_overlay, "Mask overlay")
            if overlay_size != source_size:
                raise CleanRepairError(
                    "Mask overlay dimensions must match the source image: "
                    f"{overlay_size[0]}x{overlay_size[1]} != {source_size[0]}x{source_size[1]}"
                )
        prompt = read_prompt(prompt_file, mode=mode)
        # provider_size = select_provider_size(source_size)
        provider_size = (
            args.provider_size
            if args.provider_size is not None
            else select_provider_size(source_size)
        )
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

        if mode == "alpha_hole_only":
            alpha_probe = probe_uploaded_alpha_hole(
                source_image,
                source_url,
                output_dir,
                timeout=args.download_timeout,
                session=active_session,
            )

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
            debug_sink=debug_sink,
        )
        # Centralized protocol routing (no model-name special cases here):
        # a create response that already carries the final image
        # (openai_images_sync) must be consumed directly and never polled.
        # Only the toapis_async shape (gpt-image-2) continues to polling.
        result_protocol = toapis.detect_result_protocol(submit_data)
        image_url: str | None = None
        if result_protocol == toapis.SYNC_RESULT_PROTOCOL:
            sync_items = toapis.extract_sync_image_items(submit_data)
            if not sync_items:
                raise CleanRepairError(
                    "Sync create response did not contain direct image items"
                )
            output_path, image_url, output_size = _save_sync_clean_image(
                sync_items[0],
                output_dir,
                timeout=args.download_timeout,
                session=active_session,
            )
        else:
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
                debug_info=poll_debug,
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
            "result_protocol": result_protocol,
            "create_debug": debug_sink,
            "poll_debug": poll_debug,
        }
        if alpha_probe is not None:
            result["alpha_probe"] = alpha_probe
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
            "result_protocol": result_protocol,
            "create_debug": debug_sink,
            "poll_debug": poll_debug,
        }
        if alpha_probe is not None:
            result["alpha_probe"] = alpha_probe
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
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for clean image and result.json",
    )
    parser.add_argument(
        "--mask-overlay",
        help="Optional repair-mask-overlay.png used only as a visual guide",
    )
    parser.add_argument(
        "--input-mode",
        choices=(
            "source_only",
            "source_plus_overlay",
            "alpha_hole_only",
        ),
        default=None,
        help=(
            "Input experiment mode. Default: source_plus_overlay "
            "when --mask-overlay is provided, otherwise source_only."
        ),
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)

    parser.add_argument(
        "--provider-size",
        choices=SUPPORTED_SIZES,
        default=None,
        help=(
            "Override provider output size. "
            "If omitted, infer from source orientation."
        ),
    )
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
