#!/usr/bin/env python3
"""Stage2-C Background Repair v0.1 PoC (single-image bbox black-out).

Pipeline (v0.1, experimental, NOT production):
  clean UI source image
  + reviewed-direct-assets.json (human-reviewed bboxes)
      -> black-out foreground terminal asset bboxes (bbox_source coords)
      -> background-repair-input.png / background-repair-mask.png / plan
      -> single-image gpt-image-2-official call (reuses image2_cli chain)
      -> background-repaired.png

Scope guards for this PoC:
  - NO second reference mask is sent to Image2 (mask is debug-only).
  - NO SAM masks, NO Asset Repair, NO router changes.
  - taxonomy-based exclusion uses ONLY real fields present in the reviewed
    JSON ("taxonomy": background / panel are structural owners, not terminal
    foreground assets). No invented taxonomy.
  - --prepare-only stops before any model call so the black-hole input can be
    human-inspected first.

The Image2 call reuses the verified chain in image2_cli.py:
  local file -> POST {base}/api/upload (multipart, explicit part MIME)
             -> POST /v1/images/generations (public HTTPS URL, never data URI)
             -> task polling -> result -> download
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXPERIMENTS = Path(__file__).resolve().parent
for _p in (str(_EXPERIMENTS),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import image2_cli  # verified upload / submit / poll / download chain

PLAN_SCHEMA_VERSION = "background-repair-plan-v0.1"

# Prompt confirmed by user 2026-09-07 (baseline-001 Image2 run, single image).
PROMPT = """Fill the black missing regions.
Continue the surrounding background naturally.
Do not restore removed foreground objects.
Do not add UI/text/icons."""

# Taxonomies that are structural/background owners in the reviewed-direct-assets
# contract (verified against the real JSON: "background", "panel", "manual",
# "button", "icon"). Only these two are NOT terminal foreground assets.
EXCLUDED_TAXONOMIES = {"background", "panel"}
# "manual" taxonomy entries are human-added keeps with no VLM taxonomy; they
# stay in the repair set (human confirmed them as foreground).
UNCLEAR_TAXONOMIES = {"manual"}


def load_reviewed_assets(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def resolve_source_image(json_path: Path, doc: dict[str, Any],
                         override: str | None) -> Path:
    """Resolve the clean UI image from the JSON contract first.

    reviewed-direct-assets-v0.1 records "source_image": "source.png" relative
    to the run root (= the JSON's parent directory). Only fall back to --image
    when the recorded file cannot be located.
    """
    if override:
        candidate = Path(override)
        if not candidate.is_file():
            raise SystemExit(f"ERROR: --image not found: {candidate}")
        return candidate
    recorded = doc.get("source_image")
    if isinstance(recorded, str) and recorded.strip():
        candidate = (json_path.parent / recorded).resolve()
        if candidate.is_file():
            return candidate
        raise SystemExit(
            f"ERROR: source_image recorded in JSON ({recorded}) not found at "
            f"{candidate}; pass --image explicitly")
    raise SystemExit("ERROR: reviewed JSON has no source_image field; pass --image")


def clamp_bbox(bbox: dict[str, Any], width: int, height: int) -> tuple[int, int, int, int]:
    """Clamp a source-coordinate bbox to image bounds. Returns x0,y0,x1,y1."""
    x = int(round(float(bbox["x"])))
    y = int(round(float(bbox["y"])))
    w = int(round(float(bbox["width"])))
    h = int(round(float(bbox["height"])))
    x0 = max(0, min(x, width))
    y0 = max(0, min(y, height))
    x1 = max(0, min(x + w, width))
    y1 = max(0, min(y + h, height))
    return x0, y0, x1, y1


def build_plan(doc: dict[str, Any], reviewed_json: Path, source_image: Path,
               width: int, height: int, exclude_ids: set[str]
               ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition reviewed assets into repair vs excluded, from real fields."""
    repair_assets: list[dict[str, Any]] = []
    excluded_assets: list[dict[str, Any]] = []
    for asset in doc.get("assets", []):
        aid = str(asset.get("id"))
        taxonomy = str(asset.get("taxonomy", ""))
        bbox = asset.get("bbox_source")
        if not isinstance(bbox, dict):
            excluded_assets.append({"asset_id": aid,
                                    "reason": "missing_bbox_source"})
            continue
        if aid in exclude_ids:
            excluded_assets.append({"asset_id": aid,
                                    "reason": "explicitly_excluded"})
            continue
        if taxonomy in EXCLUDED_TAXONOMIES:
            excluded_assets.append({"asset_id": aid,
                                    "reason": f"taxonomy_{taxonomy}"})
            continue
        x0, y0, x1, y1 = clamp_bbox(bbox, width, height)
        if x1 <= x0 or y1 <= y0:
            excluded_assets.append({"asset_id": aid,
                                    "reason": "bbox_empty_after_clamp"})
            continue
        repair_assets.append({
            "asset_id": aid,
            "label": asset.get("label"),
            "taxonomy": taxonomy,
            "bbox_source": {"x": x0, "y": y0,
                            "width": x1 - x0, "height": y1 - y0},
            "review_status": (asset.get("review") or {}).get("status", "unmodified"),
            "bbox_used": "bbox_source (human-override-aware)",
        })
    return repair_assets, excluded_assets


def prepare(reviewed_json: Path, source_image: Path, out_dir: Path,
            exclude_ids: set[str]) -> dict[str, Any]:
    doc = load_reviewed_assets(reviewed_json)
    img = Image.open(source_image)
    img.load()
    width, height = img.size
    if img.mode == "RGBA":
        rgb = np.array(img)[..., :3].copy()
    else:
        rgb = np.array(img.convert("RGB")).copy()
    mask = np.zeros((height, width), dtype=np.uint8)  # 0=preserve, 255=repair

    repair_assets, excluded_assets = build_plan(
        doc, reviewed_json, source_image, width, height, exclude_ids)

    for item in repair_assets:
        b = item["bbox_source"]
        rgb[b["y"]:b["y"] + b["height"], b["x"]:b["x"] + b["width"]] = (0, 0, 0)
        mask[b["y"]:b["y"] + b["height"], b["x"]:b["x"] + b["width"]] = 255

    union_px = int(np.count_nonzero(mask))
    total_px = width * height

    out_dir.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb, "RGB").save(out_dir / "background-repair-input.png")
    Image.fromarray(mask, "L").save(out_dir / "background-repair-mask.png")

    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "source_image": str(source_image),
        "source_size": {"width": width, "height": height},
        "reviewed_assets_json": str(reviewed_json),
        "review_schema_version": doc.get("schema_version"),
        "repair_assets": repair_assets,
        "excluded_assets": excluded_assets,
        "repair_bbox_count": len(repair_assets),
        "repair_pixel_count_bbox_union": union_px,
        "repair_ratio_of_source": round(union_px / total_px, 6),
        "requested_provider_size": choose_provider_size(width, height),
        "image2_model": image2_cli.MODEL,
        "prompt": PROMPT,
    }
    (out_dir / "background-repair-plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return plan


def choose_provider_size(width: int, height: int) -> str:
    """Pick the closest supported provider size by orientation (v0.1 rule)."""
    if width > height:
        return "1536x1024"
    if height > width:
        return "1024x1536"
    return "1024x1024"


def call_image2(out_dir: Path, plan: dict[str, Any], max_wait: float) -> dict[str, Any]:
    """Single-image Image2 call, reusing the verified image2_cli chain."""
    api_key = image2_cli.read_api_key()
    input_path = out_dir / "background-repair-input.png"
    out_dir.mkdir(parents=True, exist_ok=True)

    session = image2_cli.requests.Session()
    session.headers.update({"Authorization": f"Bearer {api_key}",
                            "User-Agent": image2_cli.BROWSER_UA})
    urls = image2_cli.upload_local_images([input_path], session, api_key)

    payload = {"model": image2_cli.MODEL, "prompt": PROMPT, "type": "image_text",
               "images": urls, "size": plan["requested_provider_size"],
               "n": 1, "response_format": "url"}
    audit = {**payload,
             "images": [{"local_path": str(input_path), "url": urls[0]}],
             "source_size": plan["source_size"],
             "repair_bbox_count": plan["repair_bbox_count"]}
    (out_dir / "request.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[image2] submitting ({len(urls)} ref image(s), "
          f"size={plan['requested_provider_size']}) ...")
    r = session.post(f"{image2_cli.BASE_URL}/images/generations",
                     json=payload, timeout=120)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not r.ok or body.get("success") is False:
        raise SystemExit("ERROR submit: " + image2_cli.redact(
            f"HTTP {r.status_code}: {r.text[:300]}", api_key))
    task_id = image2_cli.extract_task_id(body)
    print(f"[image2] task_id: {task_id}")

    print("[image2] polling ...")
    import time
    deadline = time.monotonic() + max_wait
    last = ""
    while True:
        time.sleep(5)
        sr = session.get(f"{image2_cli.BASE_URL}/tasks/{task_id}/status", timeout=60)
        sbody = sr.json() if sr.ok else {}
        status = image2_cli.task_status(sbody)
        if status != last:
            print(f"      {status or '(unknown)'}")
            last = status
        if status in {"completed", "succeeded", "success", "finished"}:
            break
        if status in {"failed", "error", "cancelled", "canceled"}:
            raise SystemExit("ERROR task failed: " + image2_cli.redact(
                json.dumps(sbody)[:300], api_key))
        if time.monotonic() > deadline:
            raise SystemExit(f"ERROR timeout after {max_wait}s (last: {status!r})")

    rr = session.get(f"{image2_cli.BASE_URL}/tasks/{task_id}/result", timeout=60)
    result_body = rr.json() if rr.ok else {}
    (out_dir / "response.json").write_text(
        json.dumps(result_body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    image_url = image2_cli.first_image_url(result_body)

    print("[image2] downloading ...")
    img = session.get(image_url, timeout=180)
    img.raise_for_status()
    repaired_path = out_dir / "background-repaired.png"
    repaired_path.write_bytes(img.content)
    out_img = Image.open(repaired_path)
    out_img.load()
    summary = {"task_id": task_id, "task_status": last,
               "requested_provider_size": plan["requested_provider_size"],
               "actual_output_size": {"width": out_img.width,
                                      "height": out_img.height},
               "output_image_url": image_url,
               "repaired_path": str(repaired_path)}
    (out_dir / "background-repair-result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage2-C Background Repair v0.1 PoC (bbox black-out, single-image Image2)")
    parser.add_argument("--reviewed-assets", required=True,
                        help="path to reviewed-direct-assets.json")
    parser.add_argument("--image", default=None,
                        help="clean UI image (only if JSON source_image cannot be resolved)")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--exclude-asset-id", action="append", default=[],
                        help="asset id to exclude from black-out; repeatable")
    parser.add_argument("--prepare-only", action="store_true",
                        help="only write input/mask/plan, never call Image2")
    parser.add_argument("--max-wait", type=float, default=600.0)
    args = parser.parse_args(argv)

    reviewed_json = Path(args.reviewed_assets)
    if not reviewed_json.is_file():
        raise SystemExit(f"ERROR: reviewed assets JSON not found: {reviewed_json}")
    out_dir = Path(args.out)
    exclude_ids = {a.strip() for a in args.exclude_asset_id if a.strip()}

    source_image = resolve_source_image(reviewed_json,
                                        load_reviewed_assets(reviewed_json),
                                        args.image)
    plan = prepare(reviewed_json, source_image, out_dir, exclude_ids)

    print(f"source: {plan['source_image']} "
          f"{plan['source_size']['width']}x{plan['source_size']['height']}")
    print(f"repair assets ({plan['repair_bbox_count']}): "
          + ", ".join(a["asset_id"] for a in plan["repair_assets"]))
    print(f"excluded ({len(plan['excluded_assets'])}): "
          + ", ".join(f"{a['asset_id']}({a['reason']})" for a in plan["excluded_assets"]))
    print(f"repair union pixels: {plan['repair_pixel_count_bbox_union']} "
          f"(ratio {plan['repair_ratio_of_source']})")
    print(f"provider size (requested): {plan['requested_provider_size']}")
    print(f"input  : {out_dir / 'background-repair-input.png'}")
    print(f"mask   : {out_dir / 'background-repair-mask.png'}")
    print(f"plan   : {out_dir / 'background-repair-plan.json'}")

    if args.prepare_only:
        print("PREPARE-ONLY: skipping Image2 call (inspect the input first).")
        return 0

    summary = call_image2(out_dir, plan, args.max_wait)
    print(f"DONE: {summary['repaired_path']}")
    print(f"      task status: {summary['task_status']}")
    print(f"      output size: {summary['actual_output_size']['width']}"
          f"x{summary['actual_output_size']['height']} "
          f"(requested {summary['requested_provider_size']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
