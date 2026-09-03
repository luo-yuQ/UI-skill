#!/usr/bin/env python3
"""Direct Asset Human Review UI (Human-in-the-loop bbox correction PoC).

Local browser UI for manually reviewing / correcting VLM-discovered asset
bboxes from an existing Direct Asset Discovery run.

Contract rules enforced by this tool:

- ``direct-assets.json`` is immutable VLM output. It is opened read-only and
  is never rewritten by this tool.
- All human edits are persisted to a separate ``review-overrides.json``.
- The editor world space is exactly the real source-image pixel size. A
  display-only zoom transform is applied on top of that world space; bbox
  data is always kept in source-image pixel
  coordinates (integers) and is never scaled, re-based, or persisted in any
  other coordinate space.
- Schema adapter: ``direct-assets.json`` ``bbox_source`` (source pixel space)
  is normalized into the editor bbox representation. The source contract is
  never modified.

Only the Python standard library is used.

Usage::

    python game-ui-asset-analyzer/experiments/direct_asset_review_ui.py `
      --image "runs/.../source.png" `
      --assets-json "runs/.../direct-assets.json" `
      --overrides-json "runs/.../review-overrides.json"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import struct
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

OVERRIDES_SCHEMA_VERSION = "direct-asset-review-overrides-v0.1"
VALID_DECISIONS = ("KEEP", "DROP")
BBOX_KEYS = ("x", "y", "width", "height")

# ---------------------------------------------------------------------------
# Image size detection (stdlib only, PNG + JPEG)
# ---------------------------------------------------------------------------


def _png_size(data: bytes):
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 24:
        return None
    return struct.unpack(">II", data[16:24])


def _jpeg_size(data: bytes):
    if data[:2] != b"\xff\xd8":
        return None
    i, n = 2, len(data)
    while i + 4 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if i + 9 <= n:
                height, width = struct.unpack(">HH", data[i + 5 : i + 9])
                return width, height
            return None
        i += 2 + seg_len
    return None


def image_size(path: Path):
    """Return (width, height) of a PNG/JPEG file, or raise ValueError."""
    data = path.read_bytes()
    size = _png_size(data) or _jpeg_size(data)
    if size is None:
        raise ValueError(
            f"cannot detect image dimensions of '{path}' "
            "(only PNG and JPEG are supported by this stdlib-only tool)"
        )
    return int(size[0]), int(size[1])


# ---------------------------------------------------------------------------
# direct-assets.json adapter (read-only)
# ---------------------------------------------------------------------------


def load_direct_assets(doc, image_w: int, image_h: int):
    """Normalize a parsed direct-assets.json document without modifying it.

    Adapter: the source contract uses ``bbox_source`` (source-image pixel
    space) and ``bbox_analysis`` (analysis-image space). The editor works in
    source-image pixel space, so ``bbox_source`` is preferred and normalized
    into {x, y, width, height} integer dicts. Neither representation in the
    source file is touched.
    """

    source_size = doc.get("source_image_size")
    if isinstance(source_size, dict):
        if (source_size.get("width"), source_size.get("height")) != (image_w, image_h):
            raise SystemExit(
                f"ERROR: --image real size {image_w}x{image_h} does not match "
                f"direct-assets.json source_image_size "
                f"{source_size.get('width')}x{source_size.get('height')}. "
                "The editor coordinate space is the source-image pixel space "
                "(bbox_source); refusing to start with a mismatched image."
            )
    else:
        print("WARNING: direct-assets.json has no source_image_size field; "
              "cannot verify image size consistency.")

    analysis_size = doc.get("analysis_image_size")
    if isinstance(analysis_size, dict) and isinstance(source_size, dict):
        if (analysis_size.get("width"), analysis_size.get("height")) != (
            source_size.get("width"),
            source_size.get("height"),
        ):
            print(
                "WARNING: analysis_image_size differs from source_image_size; "
                "bbox_analysis lives in the analysis space and is displayed "
                "for reference only. The editor space stays bbox_source."
            )

    assets = []
    problems = []
    for index, item in enumerate(doc.get("assets", [])):
        asset_id = item.get("id") or f"asset_{index + 1:03d}"
        raw_bbox = item.get("bbox_source", item.get("bbox_analysis"))
        if not isinstance(raw_bbox, dict):
            problems.append(f"{asset_id}: no bbox_source/bbox_analysis object; skipped")
            continue
        try:
            bbox = {k: int(round(float(raw_bbox[k]))) for k in BBOX_KEYS}
        except (KeyError, TypeError, ValueError):
            problems.append(f"{asset_id}: malformed bbox {raw_bbox!r}; skipped")
            continue
        if bbox["width"] <= 0 or bbox["height"] <= 0:
            problems.append(f"{asset_id}: non-positive bbox size {bbox}; skipped")
            continue
        assets.append(
            {
                "id": asset_id,
                "label": item.get("label", ""),
                "taxonomy": item.get("taxonomy", ""),
                "bbox": bbox,
                "analysis_bbox": item.get("bbox_analysis"),
            }
        )

    for problem in problems:
        print(f"WARNING: {problem}")
    return assets


# ---------------------------------------------------------------------------
# review-overrides.json validation + writer
# ---------------------------------------------------------------------------


def _bbox_error(bbox, image_w: int, image_h: int):
    if not isinstance(bbox, dict):
        return "bbox must be an object with x/y/width/height"
    for key in BBOX_KEYS:
        value = bbox.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            return f"bbox.{key} must be an integer (got {value!r})"
    if bbox["width"] <= 0 or bbox["height"] <= 0:
        return "bbox.width and bbox.height must be > 0"
    if (
        bbox["x"] < 0
        or bbox["y"] < 0
        or bbox["x"] + bbox["width"] > image_w
        or bbox["y"] + bbox["height"] > image_h
    ):
        return (
            f"bbox {bbox} is outside the image bounds "
            f"(0,0,{image_w},{image_h})"
        )
    return None


def save_overrides(
    body, overrides_path: Path, image_w: int, image_h: int, source_assets_name: str
):
    """Validate the client payload and write review-overrides.json.

    direct-assets.json is never touched by this function.
    """
    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    overrides_in = body.get("overrides")
    manual_in = body.get("manual_assets")
    if not isinstance(overrides_in, dict):
        raise ValueError("'overrides' must be an object")
    if manual_in is None:
        manual_in = []
    if not isinstance(manual_in, list):
        raise ValueError("'manual_assets' must be an array")

    overrides_out = {}
    for asset_id, entry in overrides_in.items():
        if not isinstance(asset_id, str) or not asset_id:
            raise ValueError("override keys must be non-empty strings")
        if not isinstance(entry, dict):
            raise ValueError(f"override for '{asset_id}' must be an object")
        unknown = set(entry) - {"bbox", "decision"}
        if unknown:
            raise ValueError(f"override for '{asset_id}' has unknown fields: {sorted(unknown)}")
        out = {}
        if "bbox" in entry:
            error = _bbox_error(entry["bbox"], image_w, image_h)
            if error:
                raise ValueError(f"{asset_id}: {error}")
            out["bbox"] = {k: int(entry["bbox"][k]) for k in BBOX_KEYS}
        if "decision" in entry:
            if entry["decision"] not in VALID_DECISIONS:
                raise ValueError(
                    f"{asset_id}: decision must be one of {list(VALID_DECISIONS)}"
                )
            out["decision"] = entry["decision"]
        if out:
            overrides_out[asset_id] = out

    manual_out = []
    seen_refs = set()
    for item in manual_in:
        if not isinstance(item, dict):
            raise ValueError("each manual asset must be an object")
        unknown = set(item) - {"asset_ref", "bbox", "decision"}
        if unknown:
            raise ValueError(f"manual asset has unknown fields: {sorted(unknown)}")
        asset_ref = item.get("asset_ref")
        if not isinstance(asset_ref, str) or not asset_ref:
            raise ValueError("manual asset requires a non-empty 'asset_ref'")
        if asset_ref in seen_refs:
            raise ValueError(f"duplicate manual asset_ref '{asset_ref}'")
        seen_refs.add(asset_ref)
        error = _bbox_error(item.get("bbox"), image_w, image_h)
        if error:
            raise ValueError(f"{asset_ref}: {error}")
        decision = item.get("decision", "KEEP")
        if decision not in VALID_DECISIONS:
            raise ValueError(
                f"{asset_ref}: decision must be one of {list(VALID_DECISIONS)}"
            )
        manual_out.append(
            {
                "asset_ref": asset_ref,
                "bbox": {k: int(item["bbox"][k]) for k in BBOX_KEYS},
                "decision": decision,
            }
        )

    payload = {
        "schema_version": OVERRIDES_SCHEMA_VERSION,
        "source_assets_json": source_assets_name,
        "image_size": {"width": image_w, "height": image_h},
        "overrides": overrides_out,
        "manual_assets": manual_out,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    overrides_path.parent.mkdir(parents=True, exist_ok=True)
    overrides_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return payload


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

PAGE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Direct Asset Human Review UI</title>
<style>
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0; display: flex; flex-direction: column;
    font: 13px/1.45 system-ui, sans-serif; color: #e8eaed; background: #101114;
  }
  header {
    display: flex; align-items: center; gap: 12px; padding: 8px 14px;
    background: #1c1e24; border-bottom: 1px solid #33363e; flex: 0 0 auto;
  }
  header h1 { font-size: 14px; margin: 0; font-weight: 600; }
  #meta { color: #9aa0a6; }
  .zoom-tools { display: flex; align-items: center; gap: 6px; }
  #zoom-value { min-width: 46px; text-align: center; font: 12px Consolas, monospace; }
  button {
    font: 12px system-ui, sans-serif; padding: 6px 12px; border-radius: 4px;
    border: 1px solid #5f6368; background: #2d3037; color: #e8eaed; cursor: pointer;
  }
  button:hover { background: #3c4048; }
  #b-add.active { background: #1a73e8; border-color: #1a73e8; }
  #b-save { background: #188038; border-color: #188038; font-weight: 600; }
  #b-save:hover { background: #1e9e46; }
  .badge {
    padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600;
    background: #3c4048; color: #e8eaed;
  }
  #dirty { background: #f9ab00; color: #202124; }
  #save-status { color: #81c995; }
  #save-status.error { color: #f28b82; }
  main { flex: 1 1 auto; display: flex; min-height: 0; }
  #left {
    flex: 1 1 auto; position: relative; overflow: auto; background: #17181c;
    cursor: default;
  }
  /* Coordinate contract: #stage is always the real source/world pixel size.
     #camera supplies scrollable layout space; zoom is display-only. */
  #camera {
    position: relative; min-width: 100%; min-height: 100%;
  }
  #stage {
    position: absolute; margin: 0; transform-origin: 0 0;
    box-shadow: 0 0 0 1px #3c4048; will-change: transform;
  }
  #stage > img, #stage > svg { position: absolute; left: 0; top: 0; display: block; }
  body.add-mode #stage { cursor: crosshair; }
  #stage > img { user-select: none; -webkit-user-drag: none; }
  #overlay { pointer-events: none; }
  .asset rect { pointer-events: all; }
  .bbox {
    fill: rgba(0, 229, 255, 0.08); stroke: #00e5ff; stroke-width: 2; cursor: move;
  }
  .bbox.st-modified { stroke: #ffb300; fill: rgba(255, 179, 0, 0.10); }
  .bbox.st-manual   { stroke: #81c995; fill: rgba(129, 201, 149, 0.10); }
  .bbox.selected    { stroke: #ffffff; stroke-width: 3; fill: rgba(255, 255, 255, 0.12); }
  .blabel {
    fill: #fff; font: 600 14px system-ui, sans-serif; pointer-events: none;
    paint-order: stroke; stroke: #000; stroke-width: 3px;
  }
  .handle {
    fill: #ffffff; stroke: #000; stroke-width: 1.5; pointer-events: all;
  }
  .handle[data-corner="tl"], .handle[data-corner="br"] { cursor: nwse-resize; }
  .handle[data-corner="tr"], .handle[data-corner="bl"] { cursor: nesw-resize; }
  #right {
    flex: 0 0 330px; overflow: auto; background: #1c1e24;
    border-left: 1px solid #33363e; padding: 12px 14px;
  }
  #right h2 { font-size: 13px; margin: 0 0 10px; color: #9aa0a6; text-transform: uppercase; letter-spacing: .06em; }
  .row { display: flex; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
  .row .k { color: #9aa0a6; }
  .mono { font-family: Consolas, monospace; font-size: 12px; }
  .muted { color: #9aa0a6; }
  label.num { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  label.num span { flex: 0 0 46px; color: #9aa0a6; }
  label.num input {
    flex: 1; background: #101114; color: #e8eaed; border: 1px solid #5f6368;
    border-radius: 4px; padding: 5px 8px; font: 13px Consolas, monospace;
  }
  .btns { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
  #b-drop { border-color: #c5221f; background: #3a1d1c; color: #f28b82; }
  #b-drop:hover { background: #4d2422; }
  #b-delete { border-color: #c5221f; background: #3a1d1c; color: #f28b82; }
</style>
</head>
<body>
<header>
  <h1>Direct Asset Human Review UI</h1>
  <span id="meta" class="muted"></span>
  <button id="b-add" type="button">Add Box</button>
  <span class="zoom-tools">
    <button id="b-zoom-out" type="button" title="Zoom out">-</button>
    <span id="zoom-value">100%</span>
    <button id="b-zoom-in" type="button" title="Zoom in">+</button>
    <button id="b-zoom-100" type="button">100%</button>
    <button id="b-fit" type="button">Fit</button>
  </span>
  <button id="b-save" type="button">Save Overrides</button>
  <span id="dirty" class="badge" hidden>unsaved changes</span>
  <span id="save-status"></span>
</header>
<main>
  <div id="left">
    <div id="camera">
      <div id="stage">
        <img id="img" alt="source image" draggable="false">
        <svg id="overlay" xmlns="http://www.w3.org/2000/svg"></svg>
      </div>
    </div>
  </div>
  <div id="right">
    <h2>Inspector</h2>
    <div id="insp-empty" class="muted">Click a bbox on the image to select it.</div>
    <div id="insp-body" hidden>
      <div class="row"><span class="k">ref</span><b id="i-id" class="mono"></b></div>
      <div class="row"><span class="k">label</span><span id="i-label"></span></div>
      <div class="row"><span class="k">taxonomy</span><span id="i-tax"></span></div>
      <div class="row"><span class="k">status</span><span id="i-status" class="badge"></span></div>
      <div class="row"><span class="k">original bbox</span><span id="i-orig" class="mono"></span></div>
      <div class="row"><span class="k">analysis bbox</span><span id="i-ana" class="mono"></span></div>
      <label class="num"><span>x</span><input id="i-x" type="number" step="1" min="0"></label>
      <label class="num"><span>y</span><input id="i-y" type="number" step="1" min="0"></label>
      <label class="num"><span>width</span><input id="i-w" type="number" step="1" min="1"></label>
      <label class="num"><span>height</span><input id="i-h" type="number" step="1" min="1"></label>
      <div class="btns">
        <button id="b-keep" type="button">KEEP</button>
        <button id="b-drop" type="button">DROP</button>
        <button id="b-clear" type="button">Clear review</button>
        <button id="b-delete" type="button" hidden>Delete manual box</button>
      </div>
    </div>
  </div>
</main>
<script>
"use strict";
const S = {
  imgW: 0, imgH: 0, assets: [], manual: [], selected: null,
  mode: "select", dirty: false,
  viewport: { zoom: 1.0 },
};
let drag = null;

const ZOOM_LEVELS = [0.25, 0.50, 0.75, 1.00, 1.25, 1.50, 2.00, 3.00, 4.00];
const MIN_ZOOM = ZOOM_LEVELS[0];
const MAX_ZOOM = ZOOM_LEVELS[ZOOM_LEVELS.length - 1];

const $ = (sel) => document.querySelector(sel);
const byId = (id) => S.assets.find((a) => a.id === id) || S.manual.find((a) => a.id === id);
const allAssets = () => S.assets.concat(S.manual);
function getVisibleAssets() {
  return allAssets().filter((a) => a.decision !== "DROP");
}
const bboxChanged = (a) =>
  !a.manual && ["x", "y", "width", "height"].some((k) => a.bbox[k] !== a.original[k]);
const STATUS_COLORS = {
  ORIGINAL: "#00e5ff", MODIFIED: "#ffb300", DROPPED: "#f28b82", MANUAL: "#81c995",
};

function status(a) {
  if (a.manual) return a.decision === "DROP" ? "DROPPED" : "MANUAL";
  if (a.decision === "DROP") return "DROPPED";
  if (a.decision === "KEEP" || bboxChanged(a)) return "MODIFIED";
  return "ORIGINAL";
}

function clamp(v, lo, hi) { return Math.min(Math.max(v, lo), hi); }

function clampBox(b) {
  let w = Math.max(1, Math.round(b.width)), h = Math.max(1, Math.round(b.height));
  w = Math.min(w, S.imgW); h = Math.min(h, S.imgH);
  const x = clamp(Math.round(b.x), 0, S.imgW - w);
  const y = clamp(Math.round(b.y), 0, S.imgH - h);
  return { x, y, width: w, height: h };
}

function resizeBox(o, corner, dx, dy) {
  let x1 = o.x, y1 = o.y, x2 = o.x + o.width, y2 = o.y + o.height;
  if (corner === "tl" || corner === "bl") x1 = o.x + dx; else x2 = o.x + o.width + dx;
  if (corner === "tl" || corner === "tr") y1 = o.y + dy; else y2 = o.y + o.height + dy;
  x2 = clamp(x2, 1, S.imgW); x1 = clamp(x1, 0, x2 - 1);
  y2 = clamp(y2, 1, S.imgH); y1 = clamp(y1, 0, y2 - 1);
  return { x: x1, y: y1, width: x2 - x1, height: y2 - y1 };
}

function cornerPoint(b, c) {
  return [c === "tl" || c === "bl" ? b.x : b.x + b.width,
          c === "tl" || c === "tr" ? b.y : b.y + b.height];
}

function nextManualId() {
  const used = new Set(allAssets().map((m) => m.id));
  let i = 1;
  while (used.has("manual_" + String(i).padStart(3, "0"))) i++;
  return "manual_" + String(i).padStart(3, "0");
}

function setMode(mode) {
  S.mode = mode;
  document.body.classList.toggle("add-mode", mode === "add");
  $("#b-add").classList.toggle("active", mode === "add");
}


// ----------------------------------------------------------- viewport ---

function screenToWorld(clientX, clientY) {
  const r = $("#stage").getBoundingClientRect();
  return {
    x: (clientX - r.left) / S.viewport.zoom,
    y: (clientY - r.top) / S.viewport.zoom,
  };
}

function applyViewport() {
  const workspace = $("#left");
  const camera = $("#camera");
  const stage = $("#stage");
  const z = S.viewport.zoom;
  const scaledW = S.imgW * z;
  const scaledH = S.imgH * z;
  const contentW = Math.max(workspace.clientWidth, scaledW + 32);
  const contentH = Math.max(workspace.clientHeight, scaledH + 32);
  camera.style.width = contentW + "px";
  camera.style.height = contentH + "px";
  stage.style.left = Math.max(16, (contentW - scaledW) / 2) + "px";
  stage.style.top = Math.max(16, (contentH - scaledH) / 2) + "px";
  stage.style.transform = "scale(" + z + ")";
  $("#zoom-value").textContent = Math.round(S.viewport.zoom * 100) + "%";
}

function setZoom(newZoom, clientX, clientY) {
  const workspace = $("#left");
  const r = workspace.getBoundingClientRect();
  if (clientX == null) clientX = r.left + r.width / 2;
  if (clientY == null) clientY = r.top + r.height / 2;

  const world = screenToWorld(clientX, clientY);
  const z = clamp(newZoom, MIN_ZOOM, MAX_ZOOM);
  S.viewport.zoom = z;
  applyViewport();
  const stageRect = $("#stage").getBoundingClientRect();
  workspace.scrollLeft += stageRect.left + world.x * z - clientX;
  workspace.scrollTop += stageRect.top + world.y * z - clientY;
}

function centerAtZoom(zoom) {
  const workspace = $("#left");
  const z = clamp(zoom, MIN_ZOOM, MAX_ZOOM);
  S.viewport.zoom = z;
  applyViewport();
  const camera = $("#camera");
  workspace.scrollLeft = Math.max(0, (camera.offsetWidth - workspace.clientWidth) / 2);
  workspace.scrollTop = Math.max(0, (camera.offsetHeight - workspace.clientHeight) / 2);
}

function fitView() {
  const workspace = $("#left");
  const margin = 32;
  const availW = Math.max(1, workspace.clientWidth - margin * 2);
  const availH = Math.max(1, workspace.clientHeight - margin * 2);
  centerAtZoom(Math.min(availW / S.imgW, availH / S.imgH));
}

function stepZoom(direction) {
  const z = S.viewport.zoom;
  if (direction > 0) {
    const next = ZOOM_LEVELS.find((v) => v > z + 1e-9);
    setZoom(next == null ? MAX_ZOOM : next);
  } else {
    const lower = ZOOM_LEVELS.filter((v) => v < z - 1e-9);
    setZoom(lower.length ? lower[lower.length - 1] : MIN_ZOOM);
  }
}

function markDirty() { S.dirty = true; $("#dirty").hidden = false; }
function markClean() { S.dirty = false; $("#dirty").hidden = true; }

let flashTimer = null;
function flash(msg, isError) {
  const el = $("#save-status");
  el.textContent = msg;
  el.classList.toggle("error", !!isError);
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { el.textContent = ""; }, 6000);
}

// ---------------------------------------------------------------- render ---

function render() {
  const svg = $("#overlay");
  let out = "";
  // DROP stays in editor state/output but never enters SVG rendering.
  // The id guard makes the one-bbox-per-id render contract explicit.
  const renderedIds = new Set();
  const visible = getVisibleAssets();
  for (const a of visible) {
    if (renderedIds.has(a.id)) {
      console.warn("duplicate asset id skipped in render:", a.id);
      continue;
    }
    renderedIds.add(a.id);
    const st = status(a);
    const sel = a.id === S.selected;
    out += '<g class="asset" data-id="' + a.id + '">' +
      '<rect class="bbox st-' + st.toLowerCase() + (sel ? " selected" : "") +
      '" x="' + a.bbox.x + '" y="' + a.bbox.y +
      '" width="' + a.bbox.width + '" height="' + a.bbox.height + '"/>' +
      '<text class="blabel" x="' + (a.bbox.x + 4) +
      '" y="' + Math.max(15, a.bbox.y - 6) + '">' + a.id + "</text></g>";
  }
  const a = visible.find((x) => x.id === S.selected) || null;
  if (a && (!drag || drag.type !== "new")) {
    for (const c of ["tl", "tr", "bl", "br"]) {
      const [hx, hy] = cornerPoint(a.bbox, c);
      out += '<rect class="handle" data-corner="' + c +
        '" x="' + (hx - 6) + '" y="' + (hy - 6) + '" width="12" height="12"/>';
    }
  }
  if (drag && drag.type === "new") {
    const b = clampBox(drag.cur);
    out += '<rect class="bbox st-manual" x="' + b.x + '" y="' + b.y +
      '" width="' + b.width + '" height="' + b.height + '"/>';
  }
  svg.innerHTML = out;
  renderInspector();
  renderHeader();
}

function renderHeader() {
  const counts = { modified: 0, dropped: 0, manual: S.manual.length };
  for (const a of S.assets) {
    const st = status(a);
    if (st === "MODIFIED") counts.modified++;
    if (st === "DROPPED") counts.dropped++;
  }
  $("#meta").textContent =
    "image " + S.imgW + "x" + S.imgH +
    " | vlm assets " + S.assets.length +
    " | modified " + counts.modified +
    " | dropped " + counts.dropped +
    " | manual " + counts.manual +
    " | visible " + getVisibleAssets().length;
}

const NUM_FIELDS = [["i-x", "x"], ["i-y", "y"], ["i-w", "width"], ["i-h", "height"]];

function renderInspector() {
  const a = byId(S.selected);
  $("#insp-empty").hidden = !!a;
  $("#insp-body").hidden = !a;
  if (!a) return;
  $("#i-id").textContent = a.id;
  $("#i-label").textContent = a.manual ? "(manual)" : a.label;
  $("#i-tax").textContent = a.manual ? "manual" : a.taxonomy;
  const st = status(a);
  $("#i-status").textContent = st;
  $("#i-status").style.background = STATUS_COLORS[st];
  $("#i-status").style.color = "#101114";
  $("#i-orig").textContent = a.manual
    ? "-" : fmt(a.original);
  $("#i-ana").textContent = a.manual || !a.analysis_bbox ? "-" : fmt(a.analysis_bbox);
  const vals = { "i-x": a.bbox.x, "i-y": a.bbox.y, "i-w": a.bbox.width, "i-h": a.bbox.height };
  for (const [id, v] of Object.entries(vals)) {
    const el = $("#" + id);
    if (document.activeElement !== el) el.value = v;
  }
  $("#b-keep").hidden = false;
  $("#b-drop").hidden = false;
  $("#b-clear").textContent = a.manual ? "Reset decision" : "Clear review";
  $("#b-delete").hidden = !a.manual;
}

const fmt = (b) => "x=" + b.x + " y=" + b.y + " w=" + b.width + " h=" + b.height;

// ---------------------------------------------------------------- events ---

$("#overlay").addEventListener("mousedown", (e) => {
  if (S.mode === "add") return;
  const t = e.target;
  if (t.classList && t.classList.contains("handle")) {
    const a = byId(S.selected);
    if (a) startDrag("resize", a, e, t.dataset.corner);
    e.preventDefault();
    return;
  }
  const g = t.closest ? t.closest("g.asset") : null;
  if (g) {
    const a = byId(g.dataset.id);
    S.selected = a.id;
    render();
    startDrag("move", a, e);
    e.preventDefault();
  }
});

function startDrag(type, a, e, corner) {
  const p = screenToWorld(e.clientX, e.clientY);
  drag = {
    type, a, corner,
    startWorldX: p.x, startWorldY: p.y,
    orig: { ...a.bbox }, changed: false,
  };
}

$("#stage").addEventListener("mousedown", (e) => {
  if (S.mode !== "add") return;
  const p = screenToWorld(e.clientX, e.clientY);
  const x = Math.round(p.x), y = Math.round(p.y);
  drag = {
    type: "new", startWorldX: p.x, startWorldY: p.y,
    anchorX: x, anchorY: y,
    cur: { x, y, width: 1, height: 1 },
  };
  e.preventDefault();
});

document.addEventListener("mousemove", (e) => {
  if (!drag) return;
  const p = screenToWorld(e.clientX, e.clientY);
  const dx = p.x - drag.startWorldX, dy = p.y - drag.startWorldY;
  if (drag.type === "new") {
    const cx = Math.round(p.x), cy = Math.round(p.y);
    const x1 = Math.min(drag.anchorX, cx), y1 = Math.min(drag.anchorY, cy);
    const x2 = Math.max(drag.anchorX, cx), y2 = Math.max(drag.anchorY, cy);
    drag.cur = clampBox({ x: x1, y: y1, width: x2 - x1, height: y2 - y1 });
    render();
    return;
  }
  const o = drag.orig;
  let b;
  if (drag.type === "move") {
    b = { x: o.x + dx, y: o.y + dy, width: o.width, height: o.height };
  } else {
    b = resizeBox(o, drag.corner, dx, dy);
  }
  const nb = clampBox(b);
  if (["x", "y", "width", "height"].some((k) => nb[k] !== drag.a.bbox[k])) drag.changed = true;
  drag.a.bbox = nb;
  render();
});

document.addEventListener("mouseup", () => {
  if (!drag) return;
  const d = drag;
  drag = null;
  if (d.type === "new") {
    const b = clampBox(d.cur);
    if (b.width >= 2 && b.height >= 2) {
      const id = nextManualId();
      S.manual.push({ id, bbox: b, decision: "KEEP", manual: true });
      S.selected = id;
      markDirty();
      setMode("select");
    }
  } else {
    d.a.bbox = clampBox(d.a.bbox);
    if (d.changed) markDirty();
  }
  render();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (drag) { // cancel in-flight drag: restore original bbox
      if (drag.type !== "new" && drag.a) drag.a.bbox = { ...drag.orig };
      drag = null;
      render();
    } else if (S.mode === "add") {
      setMode("select");
    }
  }
});

// Work-area zoom only. preventDefault also blocks browser Ctrl+wheel page zoom
// while the pointer is inside the workspace. The pointer is the zoom anchor.
$("#left").addEventListener("wheel", (e) => {
  if (!e.ctrlKey) return;
  e.preventDefault();
  const factor = Math.exp(-e.deltaY * 0.0015);
  setZoom(S.viewport.zoom * factor, e.clientX, e.clientY);
}, { passive: false });

for (const [id, key] of NUM_FIELDS) {
  $("#" + id).addEventListener("input", (e) => {
    const a = byId(S.selected);
    if (!a) return;
    const v = parseInt(e.target.value, 10);
    if (Number.isNaN(v)) return;
    const b = { ...a.bbox };
    b[key] = v;
    const nb = clampBox(b);
    if (["x", "y", "width", "height"].some((k) => nb[k] !== a.bbox[k])) markDirty();
    a.bbox = nb;
    render();
  });
}

$("#b-keep").addEventListener("click", () => {
  const a = byId(S.selected);
  if (!a) return;
  a.decision = "KEEP";
  markDirty();
  render();
});

$("#b-drop").addEventListener("click", () => {
  const a = byId(S.selected);
  if (!a) return;
  a.decision = "DROP";
  S.selected = null;
  markDirty();
  render();
});

$("#b-clear").addEventListener("click", () => {
  const a = byId(S.selected);
  if (!a) return;
  a.decision = null;
  if (a.manual) a.bbox = clampBox(a.bbox);
  markDirty();
  render();
});

$("#b-delete").addEventListener("click", () => {
  const idx = S.manual.findIndex((m) => m.id === S.selected);
  if (idx >= 0) {
    S.manual.splice(idx, 1);
    S.selected = null;
    markDirty();
    render();
  }
});

$("#b-add").addEventListener("click", () => {
  setMode(S.mode === "add" ? "select" : "add");
});

$("#b-zoom-out").addEventListener("click", () => stepZoom(-1));
$("#b-zoom-in").addEventListener("click", () => stepZoom(1));
$("#b-zoom-100").addEventListener("click", () => centerAtZoom(1.0));
$("#b-fit").addEventListener("click", fitView);

$("#b-save").addEventListener("click", save);

window.addEventListener("beforeunload", (e) => {
  if (S.dirty) { e.preventDefault(); e.returnValue = ""; }
});

// ------------------------------------------------------------------ save ---

function buildOverrides() {
  const overrides = {};
  for (const a of S.assets) {
    if (a.decision === "DROP") {
      // Dropped source assets persist only the logical decision.
      overrides[a.id] = { decision: "DROP" };
      continue;
    }
    const o = {};
    if (bboxChanged(a)) {
      o.bbox = { x: a.bbox.x, y: a.bbox.y, width: a.bbox.width, height: a.bbox.height };
    }
    if (a.decision) o.decision = a.decision;
    if (o.bbox || o.decision) overrides[a.id] = o;
  }
  const manual_assets = S.manual.map((m) => ({
    asset_ref: m.id,
    bbox: { x: m.bbox.x, y: m.bbox.y, width: m.bbox.width, height: m.bbox.height },
    decision: m.decision || "KEEP",
  }));
  return { overrides, manual_assets };
}

async function save() {
  try {
    const res = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildOverrides()),
    });
    const j = await res.json();
    if (res.ok) {
      markClean();
      flash("Saved to " + j.path + " (" + j.override_count + " overrides, " +
            j.manual_count + " manual)");
    } else {
      flash("Save failed: " + j.error, true);
    }
  } catch (err) {
    flash("Save failed: " + err, true);
  }
}

// ------------------------------------------------------------------ init ---

async function init() {
  const st = await (await fetch("/api/state")).json();
  S.imgW = st.image_size.width;
  S.imgH = st.image_size.height;
  S.assets = st.assets.map((a) => ({
    id: a.id, label: a.label || "", taxonomy: a.taxonomy || "",
    original: { ...a.bbox }, bbox: { ...a.bbox }, analysis_bbox: a.analysis_bbox || null,
    decision: null, manual: false,
  }));
  // Apply existing review-overrides.json so a previous session can be resumed.
  const ov = st.overrides;
  if (ov && typeof ov === "object") {
    for (const [id, o] of Object.entries(ov.overrides || {})) {
      const a = S.assets.find((x) => x.id === id);
      if (!a) continue;
      if (o.bbox) a.bbox = { x: +o.bbox.x, y: +o.bbox.y, width: +o.bbox.width, height: +o.bbox.height };
      if (o.decision) a.decision = o.decision;
    }
    S.manual = (ov.manual_assets || []).map((m) => ({
      id: m.asset_ref, bbox: { x: +m.bbox.x, y: +m.bbox.y, width: +m.bbox.width, height: +m.bbox.height },
      decision: m.decision || "KEEP", manual: true,
    }));
  }

  const stage = $("#stage");
  // Real source/world pixel size. Zoom/pan never changes these dimensions.
  stage.style.width = S.imgW + "px";
  stage.style.height = S.imgH + "px";
  const img = $("#img");
  img.width = S.imgW;
  img.height = S.imgH;
  img.src = "/image";
  const svg = $("#overlay");
  svg.setAttribute("width", S.imgW);
  svg.setAttribute("height", S.imgH);
  svg.setAttribute("viewBox", "0 0 " + S.imgW + " " + S.imgH);
  applyViewport();
  render();
}

window.addEventListener("resize", applyViewport);

init();
</script>
</body>
</html>
"""


class ReviewRequestHandler(BaseHTTPRequestHandler):
    server_version = "DirectAssetReviewUI/0.1"

    # ------------------------------------------------------------- helpers --
    def _send_bytes(self, data: bytes, content_type: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, obj, status: int = 200):
        data = (json.dumps(obj) + "\n").encode("utf-8")
        self._send_bytes(data, "application/json; charset=utf-8", status)

    # -------------------------------------------------------------- routes --
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._send_bytes(PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/state":
            self._send_json(self.server.build_state())
        elif path == "/image":
            try:
                data = self.server.image_path.read_bytes()
            except OSError as exc:
                self._send_json({"error": f"cannot read image: {exc}"}, 500)
                return
            mime = mimetypes.guess_type(str(self.server.image_path))[0] or "application/octet-stream"
            self._send_bytes(data, mime)
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        if urlparse(self.path).path != "/api/save":
            self._send_json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            with self.server.io_lock:
                payload = save_overrides(
                    body, self.server.overrides_path,
                    self.server.image_w, self.server.image_h,
                    self.server.source_assets_name,
                )
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, 400)
            return
        print(
            f"[review-ui] saved {self.server.overrides_path} "
            f"({len(payload['overrides'])} overrides, "
            f"{len(payload['manual_assets'])} manual assets)"
        )
        self._send_json(
            {
                "ok": True,
                "path": str(self.server.overrides_path),
                "override_count": len(payload["overrides"]),
                "manual_count": len(payload["manual_assets"]),
            }
        )

    def log_message(self, fmt, *args):  # quieter default logging
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Direct Asset Human Review UI (bbox correction PoC)"
    )
    parser.add_argument("--image", required=True, help="source image matching the direct-assets.json bbox space")
    parser.add_argument("--assets-json", required=True, help="immutable direct-assets.json from Direct Asset Discovery")
    parser.add_argument(
        "--overrides-json",
        default=None,
        help="review-overrides.json output path (default: review-overrides.json next to --assets-json)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="do not auto-open the browser")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    image_path = Path(args.image).resolve()
    assets_path = Path(args.assets_json).resolve()
    if args.overrides_json:
        overrides_path = Path(args.overrides_json).resolve()
    else:
        overrides_path = assets_path.with_name("review-overrides.json")

    if not image_path.is_file():
        raise SystemExit(f"ERROR: --image not found: {image_path}")
    if not assets_path.is_file():
        raise SystemExit(f"ERROR: --assets-json not found: {assets_path}")

    image_w, image_h = image_size(image_path)

    assets_doc = json.loads(assets_path.read_text(encoding="utf-8"))
    assets = load_direct_assets(assets_doc, image_w, image_h)

    overrides_sha = (
        hashlib.sha256(assets_path.read_bytes()).hexdigest()
    )
    print("[review-ui] direct-assets.json sha256 =", overrides_sha, "(immutable, read-only)")
    print(f"[review-ui] image            : {image_path} ({image_w}x{image_h})")
    print(f"[review-ui] assets           : {assets_path} ({len(assets)} assets)")
    print(f"[review-ui] overrides output : {overrides_path}")
    if overrides_path.is_file():
        print("[review-ui] existing overrides found; they will be applied to the editor state")

    server = ThreadingHTTPServer((args.host, args.port), ReviewRequestHandler)
    server.daemon_threads = True
    server.image_path = image_path
    server.overrides_path = overrides_path
    server.image_w = image_w
    server.image_h = image_h
    server.io_lock = threading.Lock()
    server.source_assets_name = assets_path.name
    server.build_state = lambda: {
        "image_size": {"width": image_w, "height": image_h},
        "assets": assets,
        "overrides": (
            json.loads(overrides_path.read_text(encoding="utf-8"))
            if overrides_path.is_file()
            else None
        ),
    }

    url = f"http://{args.host}:{args.port}/"
    print(f"[review-ui] serving on {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=[url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[review-ui] stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
