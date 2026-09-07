#!/usr/bin/env python3
"""Generic gpt-image-2 CLI (user controls input images; prompt is a constant).

Reference-image input chain (2026-09-07, after "base64 image is not allowed"):
  - Local files are UPLOADED via the verified ToAPIs multipart contract
    (contract source: game-ui-auto-composer-skill/scripts/toapis_preview_adapter.py):
    POST {base}/api/upload, files={"file": (name, file, <mime>)}, Bearer auth,
    response data["url"] -> public HTTPS URL, e.g. transfer-hk.youchu.xyz/d/xxx.png
    NOTE: the part MIME must be explicit — the relay rejects
    application/octet-stream parts with HTTP 400 "Unsupported file type".
  - POST /v1/images/generations  with type=image_text + images=[public URLs]
    (data URIs are rejected by the relay: "base64 image is not allowed")
  - GET /v1/tasks/{id}/status  until completed
  - GET /v1/tasks/{id}/result  -> download image URL
Requires a browser User-Agent (relay WAF rejects python-requests UA).

Command line (PowerShell):
  python image2_cli.py --image IMG1.png --image IMG2.png --out C:\\tmp\\out --size 1024x1024

  --image : repeatable; order matters (IMAGE 1, IMAGE 2, ...)
  --out   : output directory (gets output.png / request.json / response.json)
  --size  : 1024x1024 (default) | 1024x1536 | 1536x1024

The repair-guide prompt lives in the PROMPT constant below - edit it
directly in this file, like MODEL / BASE_URL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import winreg
from pathlib import Path
from typing import Any

import requests

# --- reuse the verified upload contract (thin reuse, no protocol redesign) ---
# Contract source: game-ui-auto-composer-skill/scripts/toapis_preview_adapter.py
# The adapter's upload_image helper sends the file object without an explicit
# part MIME, and the relay now rejects application/octet-stream parts with
# HTTP 400 ("Unsupported file type") — verified 2026-09-07. This CLI therefore
# re-implements only the verified request with an explicit part MIME added;
# endpoint, form field, auth, and response contract are unchanged.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADAPTER_SCRIPTS = _REPO_ROOT / "game-ui-auto-composer-skill" / "scripts"
if str(_ADAPTER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_SCRIPTS))

from toapis_preview_adapter import provider_url, is_http_url

BASE_URL = "https://ai-api.youchu.work/v1"
UPLOAD_BASE_URL = "https://ai-api.youchu.work"  # upload contract base (provider_url resolves against this)
MODEL = "gpt-image-2-official"
SUPPORTED_SIZES = ("1024x1024", "1024x1536", "1536x1024")
SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
UPLOAD_MIME = {".png": "image/png", ".jpg": "image/jpeg",
               ".jpeg": "image/jpeg", ".webp": "image/webp"}
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
UPLOAD_TIMEOUT = 120.0

PROMPT = """IMAGE 1 is the source UI crop used only as visual context.

IMAGE 2 is a binary removal mask aligned exactly with IMAGE 1.

IMPORTANT:
The visible foreground objects covered by WHITE regions in IMAGE 2 are OCCLUDERS.
They MUST BE REMOVED from the output.
Do NOT preserve them.
Do NOT redraw them.
Do NOT reconstruct them.
Do NOT copy any icon, button, card, gift box, shop icon, arrow, or other foreground
object that lies inside a WHITE mask region.

WHITE in IMAGE 2 means:
REMOVE the visible foreground object from IMAGE 1 and reconstruct ONLY the
hidden background surface underneath it.

BLACK in IMAGE 2 means:
use IMAGE 1 only as surrounding visual evidence.

The desired result is a CLEAN BACKGROUND PLATE.

For every WHITE region, continue the underlying UI background naturally from
the surrounding pixels:
- continue the teal background
- continue the horizontal borders
- continue the gradient
- continue lighting and shading
- continue texture and structural lines where applicable

The repaired WHITE regions should contain only the background that would exist
if the foreground UI assets had never been placed there.

Masked foreground assets must be completely absent from the final result.

Do not restore the removed icons.
Do not invent replacement icons.
Do not add text.
Do not add buttons.
Do not add decorations.
Do not redesign the panel.
"""


class CLIError(RuntimeError):
    pass


def read_api_key() -> str:
    key = os.environ.get("TOAPIS_API_KEY", "").strip()
    if not key:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                key = str(winreg.QueryValueEx(k, "YOUCHU_API_KEY")[0]).strip()
        except OSError:
            key = ""
    if not key:
        raise CLIError("no API key: set TOAPIS_API_KEY or configure YOUCHU_API_KEY")
    return key


def redact(text: str, api_key: str) -> str:
    return text.replace(api_key, "[REDACTED]") if api_key else text


def validate_local_image(path: Path) -> Path:
    if not path.is_file():
        raise CLIError(f"image not found: {path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise CLIError(f"unsupported image type: {path.suffix} ({path})")
    return path


def upload_local_images(paths: list[Path], session: requests.Session,
                        api_key: str) -> list[str]:
    """Upload each local file through the verified /api/upload contract.

    Same endpoint / multipart form / auth as toapis_preview_adapter.upload_image;
    the only addition is an explicit part MIME type: the relay rejects parts
    typed application/octet-stream (400 "Unsupported file type", verified
    2026-09-07). Order is preserved: paths[i] -> urls[i]. Any failure aborts
    the run before the Image2 submit step.
    """
    urls: list[str] = []
    print(f"[1/4] uploading {len(paths)} reference image(s) ...")
    for path in paths:
        mime = UPLOAD_MIME[path.suffix.lower()]
        upload_url = provider_url(UPLOAD_BASE_URL, "/api/upload")
        try:
            with path.open("rb") as file:
                response = session.post(
                    upload_url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": (path.name, file, mime)},
                    timeout=UPLOAD_TIMEOUT)
        except OSError as exc:
            raise CLIError(f"upload failed for {path}: cannot read file: {exc}") from exc
        except requests.RequestException as exc:
            raise CLIError(f"upload failed for {path}: network error: {exc}") from exc
        if not response.ok:
            raise CLIError(redact(
                f"upload failed for {path}: HTTP {response.status_code}: {response.text[:300]}",
                api_key))
        try:
            body = response.json()
        except ValueError as exc:
            raise CLIError(
                f"upload failed for {path}: non-JSON response HTTP {response.status_code}: "
                f"{redact(response.text[:200], api_key)}") from exc
        url = body.get("url") if isinstance(body, dict) else None
        if not isinstance(url, str) or not is_http_url(url):
            raise CLIError(
                f"upload for {path} did not return a usable public URL: "
                f"{redact(json.dumps(body)[:200], api_key)}")
        urls.append(url)
        print(f"      {path.name} -> {url}")
    return urls


def extract_task_id(body: dict[str, Any]) -> str:
    for cand in (body.get("task_id"), body.get("id"),
                 body.get("data", {}).get("id") if isinstance(body.get("data"), dict) else None):
        if isinstance(cand, str) and cand:
            return cand
    raise CLIError(f"no task id: {json.dumps(body)[:300]}")


def task_status(body: dict[str, Any]) -> str:
    return str(body.get("task_status") or body.get("status") or "").lower()


def first_image_url(body: dict[str, Any]) -> str:
    def walk(v: Any) -> str | None:
        if isinstance(v, dict):
            for key, item in v.items():
                if key == "url" and isinstance(item, str) and item.startswith("http"):
                    return item
                found = walk(item)
                if found:
                    return found
        elif isinstance(v, list):
            for item in v:
                found = walk(item)
                if found:
                    return found
        return None
    url = walk(body)
    if not url:
        raise CLIError(f"no image url in result: {json.dumps(body)[:300]}")
    return url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="gpt-image-2 CLI (prompt is embedded in this file)")
    parser.add_argument("--image", action="append", required=True,
                        help="reference image path; repeat for multiple (order matters: IMAGE 1, IMAGE 2, ...)")
    parser.add_argument("--out", required=True, help="output directory (gets output.png / request.json / response.json)")
    parser.add_argument("--size", default="1024x1024", choices=SUPPORTED_SIZES)
    parser.add_argument("--max-wait", type=float, default=600.0)
    args = parser.parse_args(argv)

    api_key = ""
    try:
        if not PROMPT.strip():
            raise CLIError("PROMPT constant in this file is empty")
        api_key = read_api_key()
        images = [validate_local_image(Path(p)) for p in args.image]
        if not 1 <= len(images) <= 4:
            raise CLIError("1..4 reference images supported")
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)

        session = requests.Session()
        session.headers.update({"Authorization": f"Bearer {api_key}",
                                "User-Agent": BROWSER_UA})

        # upload first; abort before submit if any upload fails
        image_urls = upload_local_images(images, session, api_key)

        payload = {"model": MODEL, "prompt": PROMPT, "type": "image_text",
                   "images": image_urls,
                   "size": args.size, "n": 1, "response_format": "url"}
        # request.json is the audit file: local_path + url per image,
        # never the payload actually sent (that one carries plain URL strings).
        audit = {**payload,
                 "images": [{"local_path": str(p), "url": u}
                            for p, u in zip(images, image_urls)]}
        (out_dir / "request.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        print(f"[2/4] submitting ({len(images)} ref image(s), size={args.size}, prompt=embedded constant) ...")
        r = session.post(f"{BASE_URL}/images/generations", json=payload, timeout=120)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if not r.ok or body.get("success") is False:
            raise CLIError(redact(
                f"submit failed HTTP {r.status_code}: {r.text[:300]}", api_key))
        task_id = extract_task_id(body)
        print(f"      task_id: {task_id}")

        print("[3/4] polling ...")
        deadline = time.monotonic() + args.max_wait
        last = ""
        while True:
            time.sleep(5)
            sr = session.get(f"{BASE_URL}/tasks/{task_id}/status", timeout=60)
            sbody = sr.json() if sr.ok else {}
            status = task_status(sbody)
            if status != last:
                print(f"      {status or '(unknown)'}")
                last = status
            if status in {"completed", "succeeded", "success", "finished"}:
                break
            if status in {"failed", "error", "cancelled", "canceled"}:
                raise CLIError(redact(f"task failed: {json.dumps(sbody)[:300]}", api_key))
            if time.monotonic() > deadline:
                raise CLIError(f"timeout after {args.max_wait}s (last status: {status!r})")
        rr = session.get(f"{BASE_URL}/tasks/{task_id}/result", timeout=60)
        result_body = rr.json() if rr.ok else {}
        (out_dir / "response.json").write_text(
            json.dumps(result_body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        image_url = first_image_url(result_body)

        print("[4/4] downloading ...")
        img = session.get(image_url, timeout=180)
        img.raise_for_status()
        out_path = out_dir / "output.png"
        out_path.write_bytes(img.content)
        print(f"DONE: {out_path}")
        print(f"      image url: {image_url}")
        return 0
    except CLIError as exc:
        print(f"ERROR: {redact(str(exc), api_key)}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"ERROR: network: {redact(str(exc), api_key)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
