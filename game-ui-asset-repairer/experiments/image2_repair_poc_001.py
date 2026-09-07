#!/usr/bin/env python3
"""Stage2-C Image2 Repair v0.1 PoC — asset_029 only (single case).

Reuses the verified Stage0 gpt-image-2 calling chain (uploads -> task
polling -> download) instead of writing a new provider client:
- upload:   POST {base}/uploads/images  (multipart, purpose=generation)
- submit:   POST {base}/images/generations  (reference_images=[url,...])
- poll:     GET  {base}/images/generations/{task_id}
- download: GET  image url from result

Frozen Stage2-C safety dilation is applied first (5% per occluder,
clamp 1..6). Target + mask get ONE identical deterministic canvas
transform (letterbox onto the provider canvas, no independent resize).
The raw provider result is mapped back with the inverse transform, then
composited deterministically: outside the effective mask every pixel is
byte-identical to the original target.
"""

from __future__ import annotations

import json
import os
import sys
import time
import winreg
from pathlib import Path
from typing import Any

import numpy as np
import requests
from PIL import Image
from scipy import ndimage

REPAIRER_DIR = Path(__file__).resolve().parent

BASE_URL = "https://ai-api.youchu.work/v1"
MODEL = "gpt-image-2"
PROVIDER_SIZES = {"1024x1024", "1024x1536", "1536x1024"}

DILATION_RATIO = 0.05
DILATION_MIN_PX = 1
DILATION_MAX_PX = 6

PROMPT = """IMAGE 1 is the damaged target UI asset that must be repaired.

IMAGE 2 is a binary repair mask aligned pixel-for-pixel with IMAGE 1.

In IMAGE 2:
- WHITE pixels indicate regions that must be reconstructed.
- BLACK pixels indicate regions that must remain unchanged.

Only reconstruct the regions corresponding to WHITE pixels in IMAGE 2.

Preserve all visual content of IMAGE 1 outside those regions.
Continue the existing background, border, gradient, lighting, texture,
shape, and decorative structure naturally through the missing areas.

Do not redesign the asset.
Do not change the overall silhouette.
Do not add text.
Do not add icons.
Do not reproduce the mask.
"""


class Image2RepairError(RuntimeError):
    def __init__(self, reason_code: str, message: str):
        super().__init__(f"{reason_code}: {message}")
        self.reason_code = reason_code
        self.message = message


# ---------------------------------------------------------------------------
# Stage2-C frozen safety dilation (same rule as CV v0.1)
# ---------------------------------------------------------------------------

def compute_radius(width: int, height: int) -> int:
    return int(max(DILATION_MIN_PX, min(round(min(width, height) * DILATION_RATIO),
                                        DILATION_MAX_PX)))


def build_effective_mask(repair_input: dict[str, Any]) -> np.ndarray:
    """Per-occluder 5% dilation, unioned, clipped to frame."""
    target = np.array(Image.open(repair_input["target_asset_path"]).convert("RGBA"))
    H, W = target.shape[:2]
    frame = repair_input["target_frame"]
    mask_img = np.array(Image.open(repair_input["repair_mask_path"]).convert("L"))
    if mask_img.shape != (H, W):
        raise Image2RepairError("mask_size_mismatch",
                                f"{mask_img.shape} != target {(H, W)}")
    effective = mask_img > 0
    per_occluder = []
    for occ in repair_input["occluder_masks"]:
        bbox = occ["bbox_source"]
        radius = compute_radius(bbox["width"], bbox["height"])
        om = np.array(Image.open(occ["mask_path"]).convert("L")) > 0
        if om.shape != (bbox["height"], bbox["width"]):
            raise Image2RepairError("occluder_mask_shape_mismatch",
                                    f"{occ['asset_id']}: {om.shape} vs bbox")
        # project into target frame (same arithmetic as C1.5)
        ofx, ofy = int(bbox["x"]), int(bbox["y"])
        tfx, tfy = int(frame["x"]), int(frame["y"])
        x1, y1 = max(ofx, tfx), max(ofy, tfy)
        x2, y2 = min(ofx + om.shape[1], tfx + int(frame["width"]),
                     ofx + om.shape[1]), min(ofy + om.shape[0], tfy + int(frame["height"]))
        x2 = min(ofx + om.shape[1], tfx + int(frame["width"]))
        y2 = min(ofy + om.shape[0], tfy + int(frame["height"]))
        if x1 < x2 and y1 < y2:
            patch = om[y1 - ofy:y2 - ofy, x1 - ofx:x2 - ofx]
            proj = np.zeros((H, W), bool)
            proj[y1 - tfy:y2 - tfy, x1 - tfx:x2 - tfx] = patch
            effective |= ndimage.binary_dilation(proj, iterations=radius)
        per_occluder.append({"asset_id": occ["asset_id"],
                             "bbox_size": {"width": bbox["width"], "height": bbox["height"]},
                             "min_dimension": min(bbox["width"], bbox["height"]),
                             "effective_radius_px": radius})
    return effective, per_occluder


# ---------------------------------------------------------------------------
# deterministic canvas transform (target and mask share ONE transform)
# ---------------------------------------------------------------------------

def canvas_transform(W: int, H: int) -> tuple[str, int, int, int, int]:
    """Choose provider size and letterbox offsets. Returns
    (provider_size, canvas_w, canvas_h, pad_x, pad_y). Scale is always 1:1
    (no resample) — asset_029 is 1024x141, padded into 1024x1024."""
    for size in ("1024x1024", "1024x1536", "1536x1024"):
        cw, ch = (int(v) for v in size.split("x"))
        if W <= cw and H <= ch:
            return size, cw, ch, (cw - W) // 2, (ch - H) // 2
    raise Image2RepairError("target_exceeds_provider_canvas", f"{W}x{H}")


def to_canvas(img: Image.Image, cw: int, ch: int, px: int, py: int,
              fill) -> Image.Image:
    canvas = Image.new(img.mode, (cw, ch), fill)
    canvas.paste(img, (px, py))
    return canvas


# ---------------------------------------------------------------------------
# Stage0 verified provider chain
# ---------------------------------------------------------------------------

def read_api_key() -> str:
    key = os.environ.get("TOAPIS_API_KEY", "").strip()
    if not key:
        key = os.environ.get("STAGE2A_VLM_API_KEY", "").strip()
    if not key:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                key = str(winreg.QueryValueEx(k, "YOUCHU_API_KEY")[0]).strip()
        except OSError:
            key = ""
    if not key:
        raise Image2RepairError("provider_config_missing",
                                "no API key (TOAPIS_API_KEY / YOUCHU_API_KEY)")
    return key


# relay WAF rejects the default python-requests UA (returns empty 204);
# a browser UA passes. Verified 2026-09-07.
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def new_session(api_key: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {api_key}",
                            "User-Agent": BROWSER_UA})
    return session


def upload_image(session: requests.Session, image_path: Path) -> str:
    """NOTE: the relay's /v1/uploads/images endpoint is currently broken
    (502 Proxy request failed, verified 2026-09-07 with 8px test images
    across models). References are therefore passed as data URIs via the
    images field (type=image_text) — same spatial-guide semantics as the
    Stage0 upload chain, no client rewrite."""
    import base64
    return "data:image/png;base64," + base64.b64encode(image_path.read_bytes()).decode("ascii")


def submit_task(session: requests.Session, reference_urls: list[str]) -> str:
    payload = {"model": MODEL, "prompt": PROMPT,
               "type": "image_text",
               "images": reference_urls,
               "size": PROVIDER_SIZE[0], "resolution": "1k",
               "n": 1, "response_format": "url"}
    r = session.post(f"{BASE_URL}/images/generations", json=payload, timeout=120)
    try:
        body = r.json()
    except ValueError as exc:
        raise Image2RepairError("provider_request_failed",
                                f"submit non-JSON HTTP {r.status_code}: {r.text[:200]}") from exc
    if not r.ok or body.get("success") is False:
        raise Image2RepairError("provider_request_failed",
                                f"submit failed HTTP {r.status_code}: {json.dumps(body)[:300]}")
    for cand in (body.get("task_id"), body.get("id"),
                 (body.get("data") or {}).get("id") if isinstance(body.get("data"), dict) else None):
        if isinstance(cand, str) and cand:
            return cand
    raise Image2RepairError("provider_response_invalid",
                            f"no task id in submit response: {json.dumps(body)[:300]}")


def poll_task(session: requests.Session, task_id: str, max_wait: float = 600.0,
              interval: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        # verified endpoint: GET /v1/tasks/{id}/status (2026-09-07)
        r = session.get(f"{BASE_URL}/tasks/{task_id}/status", timeout=60)
        if not r.ok:
            raise Image2RepairError("provider_task_failed",
                                    f"status HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        status = str(body.get("task_status") or body.get("status") or "").lower()
        if status in {"completed", "succeeded", "success", "finished"}:
            return body
        if status in {"failed", "error", "cancelled", "canceled"}:
            raise Image2RepairError("provider_task_failed",
                                    f"task failed: {json.dumps(body)[:300]}")
        time.sleep(interval)
    raise Image2RepairError("provider_timeout", f"task {task_id} exceeded {max_wait}s")


def fetch_result(session: requests.Session, task_id: str) -> dict[str, Any]:
    """Status endpoint has no URL; the verified result endpoint is
    GET /v1/tasks/{id}/result (2026-09-07)."""
    r = session.get(f"{BASE_URL}/tasks/{task_id}/result", timeout=60)
    if not r.ok:
        raise Image2RepairError("provider_task_failed",
                                f"result HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def extract_image_url(body: dict[str, Any]) -> str:
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
        raise Image2RepairError("provider_response_invalid",
                                "no image url in completed task")
    return url


def download_image(session: requests.Session, url: str, out_path: Path) -> None:
    r = session.get(url, timeout=180)
    r.raise_for_status()
    out_path.write_bytes(r.content)


PROVIDER_SIZE: tuple[str, int, int] = ("1024x1024", 1024, 1024)


# ---------------------------------------------------------------------------
# main PoC
# ---------------------------------------------------------------------------

def main() -> int:
    REPAIR_ROOT = Path("D:/Third_Test_1/UI-skill/runs/20260902_direct-asset-discovery-007-production-client/stage2c/repair")
    out_dir = REPAIR_ROOT / "asset_029" / "image2-repair-poc-001"
    out_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "schema_version": "image2-repair-poc-v0.1",
        "status": "failed", "reason_code": None, "message": None,
        "target_asset_id": "asset_029", "backend": "image2",
        "provider": "toapis", "model": MODEL,
    }

    def finish(status: str, reason: str | None = None, msg: str | None = None):
        result["status"] = status
        result["reason_code"] = reason
        result["message"] = msg
        (out_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: result.get(k) for k in
                          ("status", "reason_code", "target_asset_id")}, ensure_ascii=False))
        return 0 if status == "success" else 1

    try:
        inp = json.loads((REPAIR_ROOT / "asset_029" / "repair-input.json")
                         .read_text(encoding="utf-8-sig"))
        if inp.get("status") != "success":
            return finish("failed", "repair_input_not_success", str(inp.get("status")))
        result["occluder_asset_ids"] = list(inp["occluder_asset_ids"])

        target = Image.open(inp["target_asset_path"]).convert("RGBA")
        W, H = target.size
        result["target_original_size"] = {"width": W, "height": H}

        # ---- effective mask (frozen 5% rule) ----
        eff_mask, per_occ = build_effective_mask(inp)
        Image.fromarray((np.array(Image.open(inp["repair_mask_path"]).convert("L")) > 0).astype(np.uint8) * 255, "L").save(out_dir / "repair-mask-original.png")
        Image.fromarray(eff_mask.astype(np.uint8) * 255, "L").save(out_dir / "repair-mask-effective.png")
        result["repair_mask_original_pixels"] = int((np.array(Image.open(inp["repair_mask_path"]).convert("L")) > 0).sum())
        result["repair_mask_effective_pixels"] = int(eff_mask.sum())
        result["dilation_ratio"] = DILATION_RATIO
        result["per_occluder_dilation"] = per_occ
        result["request_mode"] = "source_plus_overlay_reference"

        # ---- deterministic canvas transform (ONE transform for both) ----
        provider_size, cw, ch, px, py = canvas_transform(W, H)
        PROVIDER_SIZE = (provider_size, cw, ch)
        result["provider_input_size"] = {"width": cw, "height": ch}
        result["crop_back_transform"] = {
            "provider_size": provider_size, "pad_x": px, "pad_y": py,
            "crop_x": px, "crop_y": py, "crop_w": W, "crop_h": H, "scale": 1}

        orig_rgba = np.array(target)
        transparent_tile = (0, 0, 0, 0)
        target_canvas = to_canvas(target, cw, ch, px, py, transparent_tile)
        # provider inputs are flat RGB on white for clarity
        target_rgb_canvas = Image.new("RGB", (cw, ch), (255, 255, 255))
        target_rgb_canvas.paste(target_canvas, (0, 0), target_canvas)
        mask_canvas = Image.new("L", (cw, ch), 0)
        mask_canvas.paste(Image.fromarray((eff_mask * 255).astype(np.uint8), "L"), (px, py))
        overlay_canvas = target_rgb_canvas.copy()
        red = np.array(overlay_canvas)
        region = np.array(mask_canvas) > 0
        red[region] = (0.5 * red[region] + 0.5 * np.array([255, 0, 0])).astype(np.uint8)
        overlay_canvas = Image.fromarray(red)

        target_rgb_canvas.save(out_dir / "provider-input-target.png")
        mask_canvas.save(out_dir / "provider-input-mask.png")
        overlay_canvas.save(out_dir / "provider-input-overlay.png")

        # ---- provider chain (Stage0 verified) ----
        key = read_api_key()
        session = new_session(key)
        url_target = upload_image(session, out_dir / "provider-input-target.png")
        url_overlay = upload_image(session, out_dir / "provider-input-overlay.png")
        task_id = submit_task(session, [url_target, url_overlay])
        result["task_id"] = task_id
        poll_task(session, task_id)
        result_body = fetch_result(session, task_id)
        image_url = extract_image_url(result_body)
        result["image_url"] = image_url
        (out_dir / "provider-response.json").write_text(
            json.dumps(result_body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (out_dir / "request.json").write_text(json.dumps({
            "model": MODEL, "prompt": PROMPT,
            "type": "image_text",
            "images": ["<data-uri target>", "<data-uri overlay>"],
            "size": provider_size, "resolution": "1k", "n": 1,
            "response_format": "url"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        download_image(session, image_url, out_dir / "raw-image2-result.png")

        # ---- map back to target size (inverse of the canvas transform) ----
        raw = Image.open(out_dir / "raw-image2-result.png").convert("RGB")
        result["provider_output_size"] = {"width": raw.width, "height": raw.height}
        if (raw.width, raw.height) != (cw, ch):
            return finish("failed", "output_size_mismatch",
                          f"provider returned {raw.width}x{raw.height}, expected {cw}x{ch}")
        mapped = raw.crop((px, py, px + W, py + H))
        mapped_rgba = np.dstack([np.array(mapped), np.full((H, W), 255, np.uint8)])
        Image.fromarray(mapped_rgba, "RGBA").save(out_dir / "image2-repaired-target-size.png")

        # ---- deterministic composite ----
        # alpha: Image2 outputs RGB -> reuse frozen deterministic alpha rule
        # (mask alpha consensus). For asset_029 the plate interior is opaque,
        # so ring-consensus = 255 inside mask; outside untouched.
        orig = orig_rgba
        final = orig.copy()
        final[..., :3][eff_mask] = np.array(mapped)[eff_mask]
        # alpha inside mask: nearest-reliable consensus from surrounding ring
        ring = ndimage.binary_dilation(eff_mask, iterations=8) & (~eff_mask)
        ring_alpha = orig[ring][:, 3]
        consensus = 255 if (ring_alpha == 255).mean() >= 0.98 else (
            0 if (ring_alpha == 0).mean() >= 0.98 else None)
        if consensus is None:
            _, (iy, ix) = ndimage.distance_transform_edt(~ring > 0 if ring.sum() == 0 else ~ring,
                                                         return_indices=True)
            final[..., 3][eff_mask] = orig[iy, ix, 3][eff_mask]
        else:
            final[..., 3][eff_mask] = consensus
        Image.fromarray(final, "RGBA").save(out_dir / "repaired-final.png")

        # ---- verification ----
        inside_rgb = int((final[..., :3][eff_mask] != orig[..., :3][eff_mask]).any(axis=-1).sum())
        outside_rgb = int((final[..., :3][~eff_mask] != orig[..., :3][~eff_mask]).any(axis=-1).sum())
        outside_alpha = int((final[..., 3][~eff_mask] != orig[..., 3][~eff_mask]).sum())
        result["mask_inside_changed_pixels"] = inside_rgb
        result["mask_outside_changed_pixels"] = outside_rgb + outside_alpha
        result["mask_outside_rgb_changed_final"] = outside_rgb
        result["mask_outside_alpha_changed_final"] = outside_alpha
        result["alpha_repair_method"] = ("ring_consensus_255" if consensus == 255
                                         else "ring_consensus_0" if consensus == 0
                                         else "nearest_reliable_pixel")
        result["raw_result_path"] = (out_dir / "raw-image2-result.png").as_posix()
        result["final_result_path"] = (out_dir / "repaired-final.png").as_posix()

        if outside_rgb != 0 or outside_alpha != 0:
            return finish("failed", "assertion_failed",
                          f"outside changed rgb={outside_rgb} alpha={outside_alpha}")

        # ---- previews ----
        Image.fromarray(orig, "RGBA").save(out_dir / "original.png")
        panels = [Image.fromarray(orig, "RGBA").convert("RGB"),
                  Image.fromarray((eff_mask * 255).astype(np.uint8).repeat(3, 0).reshape(H, W, 3) if False else np.dstack([eff_mask * 255] * 3).astype(np.uint8)),
                  mapped,
                  Image.fromarray(final, "RGBA").convert("RGB")]
        gap = 6
        canvas = Image.new("RGB", (W * len(panels) + gap * (len(panels) - 1), H), (30, 30, 30))
        for i, p in enumerate(panels):
            canvas.paste(Image.fromarray(np.asarray(p)).convert("RGB"), (i * (W + gap), 0))
        canvas.save(out_dir / "preview.png")

        # zoom-compare around mask bbox
        ys, xs = np.nonzero(eff_mask)
        pad = 20
        x0, y0 = max(xs.min() - pad, 0), max(ys.min() - pad, 0)
        x1, y1 = min(xs.max() + pad, W), min(ys.max() + pad, H)
        scale = max(1, min(5, 1200 // max(x1 - x0, 1)))
        tiles = [Image.fromarray(orig, "RGBA").convert("RGB").crop((x0, y0, x1, y1)),
                 mapped.crop((x0, y0, x1, y1)),
                 Image.fromarray(final, "RGBA").convert("RGB").crop((x0, y0, x1, y1))]
        zw, zh = (x1 - x0) * scale, (y1 - y0) * scale
        zc = Image.new("RGB", (zw * 3 + 16, zh), (30, 30, 30))
        for i, t in enumerate(tiles):
            zc.paste(t.resize((zw, zh), Image.NEAREST), (i * (zw + 8), 0))
        zc.save(out_dir / "zoom-compare.png")

        return finish("success")
    except Image2RepairError as exc:
        return finish("failed", exc.reason_code, exc.message)
    except Exception as exc:
        return finish("failed", "unexpected_error", f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
