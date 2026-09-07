#!/usr/bin/env python3
"""Stage2-F Web Layout Editor v0.1 PoC.

Browser editor for a successfully generated Stage2-E recompose result:

- ``ui-compose.json``  (READ ONLY BASELINE, never written by this tool)
- clean background PNG
- transparent component PNGs

The editor re-renders the full UI in the browser and lets the user:

- click-select a component
- drag it with the mouse (Pointer Events + setPointerCapture)
- edit x / y numerically in the Inspector
- arrow-key nudge (1 px, Shift+Arrow = 10 px)
- Save -> ``layout-overrides.json`` (+ ``resolved-layout.json``)
- Reset Selected -> revert one component to its base x/y (disk on Save)

Coordinate contract (hard rule)::

    WORLD SPACE  = source image pixel space (ui-compose.json x/y live here)
    DISPLAY SPACE= browser rendered pixels (world * zoom)
    ZOOM         = display-only transform; never persisted, never mixed
                   into coordinates

Drag conversion is always recomputed from the drag-start anchor (no
incremental accumulation)::

    world_dx = screen_dx / zoom
    x = drag_start_world_x + world_dx   (rounded to integer source pixels)

Historical review-UI bugs explicitly avoided in v0.1:

- plain mouse wheel does NOT zoom (only Ctrl+wheel)
- the canvas cannot be dragged away (no free pan in v0.1)
- one single display transform (CSS scale on the stage), never a mix of
  CSS width scaling + transform scale + extra coordinate scaling
- UI state vs disk JSON: explicit baseState/currentState/savedState with
  a dirty flag; Save failure keeps dirty=true

Only the Python standard library is used.

Usage::

    python game-ui-asset-repairer/experiments/layout_editor.py ^
      --compose "runs/20260902_direct-asset-discovery-007-production-client/stage2e/recompose/baseline-001/ui-compose.json"
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import struct
import tempfile
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

OVERRIDES_SCHEMA_VERSION = "layout-overrides-v0.1"
RESOLVED_SCHEMA_VERSION = "resolved-layout-v0.1"
EDITABLE_FIELDS = ("x", "y")  # v0.1: only x/y are editable


# ---------------------------------------------------------------------------
# Image size detection (stdlib only, PNG)
# ---------------------------------------------------------------------------


def _png_size(data: bytes):
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 24:
        return None
    return struct.unpack(">II", data[16:24])


def image_size(path: Path):
    """Return (width, height) of a PNG file, or raise ValueError."""
    data = path.read_bytes()
    size = _png_size(data)
    if size is None:
        raise ValueError(f"cannot detect PNG dimensions of '{path}'")
    return int(size[0]), int(size[1])


# ---------------------------------------------------------------------------
# atomic JSON writer
# ---------------------------------------------------------------------------


def atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON atomically: temp file -> flush+fsync -> os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# ui-compose.json adapter (READ ONLY)
# ---------------------------------------------------------------------------


def load_compose(compose_path: Path):
    """Parse ui-compose.json without modifying it.

    Returns (doc, source_w, source_h, components, warnings) where components
    is a list of normalized dicts with existing PNG paths only.
    """
    doc = json.loads(compose_path.read_text(encoding="utf-8"))
    warnings = []

    source_size = doc.get("source_size") or {}
    source_w = int(source_size.get("width", 0))
    source_h = int(source_size.get("height", 0))
    if source_w <= 0 or source_h <= 0:
        raise SystemExit(
            f"ERROR: {compose_path} has no usable source_size "
            f"(got {source_size!r}); editor world space cannot be derived"
        )

    background = doc.get("background") or {}
    bg_path = Path(background.get("path", ""))
    if not bg_path.is_file():
        raise SystemExit(f"ERROR: background PNG not found: {bg_path}")

    components = []
    for index, item in enumerate(doc.get("components", [])):
        asset_id = item.get("asset_id") or f"component_{index:03d}"
        png_path = Path(item.get("path", ""))
        if not png_path.is_file():
            warnings.append(f"missing_component_png: {asset_id} ({png_path}); skipped")
            continue
        try:
            x = int(round(float(item["x"])))
            y = int(round(float(item["y"])))
            w = int(round(float(item["width"])))
            h = int(round(float(item["height"])))
        except (KeyError, TypeError, ValueError):
            warnings.append(f"malformed geometry for {asset_id}; skipped")
            continue
        if item.get("visible") is False:
            warnings.append(f"invisible component skipped: {asset_id}")
            continue
        components.append(
            {
                "asset_id": asset_id,
                "label": item.get("label", ""),
                "taxonomy": item.get("taxonomy", ""),
                "path": str(png_path),
                "x": x,
                "y": y,
                "width": w,
                "height": h,
                "z_index": int(item.get("z_index", index)),
            }
        )

    components.sort(key=lambda c: c["z_index"])
    return doc, source_w, source_h, components, warnings


# ---------------------------------------------------------------------------
# layout-overrides.json validation + save (+ resolved-layout.json)
# ---------------------------------------------------------------------------


def validate_overrides(body, components, source_w: int, source_h: int) -> dict:
    """Validate a /api/save payload; return the clean overrides dict.

    v0.1 rules:
    - overrides is an object keyed by asset_id
    - each entry may only contain x and/or y (integers, not bools)
    - asset_id must exist in the compose components
    - resolved position must stay inside the canvas (clamp policy)
    """
    if not isinstance(body, dict) or not isinstance(body.get("overrides"), dict):
        raise ValueError("request body must contain an 'overrides' object")

    by_id = {c["asset_id"]: c for c in components}
    clean = {}
    for asset_id, entry in body["overrides"].items():
        if not isinstance(asset_id, str) or not asset_id:
            raise ValueError("override keys must be non-empty strings")
        comp = by_id.get(asset_id)
        if comp is None:
            raise ValueError(f"unknown_override_asset: {asset_id}")
        if not isinstance(entry, dict):
            raise ValueError(f"override for '{asset_id}' must be an object")
        unknown = set(entry) - set(EDITABLE_FIELDS)
        if unknown:
            raise ValueError(
                f"override for '{asset_id}' has non-editable fields: {sorted(unknown)} "
                f"(v0.1 allows only {list(EDITABLE_FIELDS)})"
            )
        out = {}
        for key in EDITABLE_FIELDS:
            if key not in entry:
                continue
            value = entry[key]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{asset_id}.{key} must be an integer (got {value!r})")
            if key == "x":
                lo, hi = 0, source_w - comp["width"]
            else:
                lo, hi = 0, source_h - comp["height"]
            if not lo <= value <= hi:
                raise ValueError(
                    f"{asset_id}.{key}={value} outside canvas bounds [{lo},{hi}]"
                )
            out[key] = value
        if out:
            clean[asset_id] = out
    return clean


def build_resolved_layout(
    compose_path: Path,
    compose_doc: dict,
    components,
    overrides: dict,
    source_w: int,
    source_h: int,
) -> dict:
    """Merge ui-compose.json + overrides into resolved-layout.json."""
    override_by_id = {c["asset_id"]: c for c in components}, {
        c["asset_id"]: c for c in components
    }
    out_components = []
    for comp in components:
        entry = dict(comp)
        ov = overrides.get(comp["asset_id"], {})
        entry["x"] = int(ov.get("x", comp["x"]))
        entry["y"] = int(ov.get("y", comp["y"]))
        out_components.append(entry)

    return {
        "schema_version": RESOLVED_SCHEMA_VERSION,
        "source_compose": str(compose_path),
        "source_size": {"width": source_w, "height": source_h},
        "canvas": {"width": source_w, "height": source_h},
        "background": compose_doc.get("background", {}),
        "z_order_source": compose_doc.get("z_order_source"),
        "components": out_components,
    }


# ---------------------------------------------------------------------------
# editor page (single-file HTML + CSS + vanilla JS)
# ---------------------------------------------------------------------------

PAGE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Stage2-F Layout Editor v0.1</title>
<style>
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0; display: flex; flex-direction: column;
    font: 13px/1.45 system-ui, sans-serif; color: #e8eaed; background: #101114;
  }
  header {
    display: flex; align-items: center; gap: 10px; padding: 8px 14px;
    background: #1c1e24; border-bottom: 1px solid #33363e; flex: 0 0 auto;
  }
  header h1 { font-size: 14px; margin: 0 8px 0 0; font-weight: 600; }
  button {
    font: 12px system-ui, sans-serif; padding: 6px 12px; border-radius: 4px;
    border: 1px solid #5f6368; background: #2d3037; color: #e8eaed; cursor: pointer;
  }
  button:hover { background: #3c4048; }
  #zoom-value { min-width: 48px; text-align: center; font: 12px Consolas, monospace; }
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

  /* ---- viewport -------------------------------------------------------
     Coordinate contract: #stage is always the real WORLD size
     (source_w x source_h, in source pixels). #holder reserves the
     DISPLAY size (world * zoom) so native scrolling works. The ONLY
     display transform is `transform: scale(zoom)` with origin 0 0 on
     #stage. No CSS width scaling, no extra coordinate scaling. */
  #viewport {
    flex: 1 1 auto; position: relative; overflow: auto; background: #17181c;
  }
  #holder { position: relative; }
  #stage {
    position: absolute; left: 0; top: 0; transform-origin: 0 0;
    box-shadow: 0 0 0 1px #3c4048; will-change: transform;
  }
  #stage img { position: absolute; display: block; user-select: none; -webkit-user-drag: none; }
  #background { pointer-events: none; left: 0; top: 0; z-index: 0; }
  .component { touch-action: none; cursor: move; }
  .component.selected { outline: 3px solid #1a73e8; outline-offset: 1px; }
  .component:hover { outline: 2px dashed rgba(26,115,232,.7); outline-offset: 1px; }
  .component.selected:hover { outline: 3px solid #1a73e8; outline-offset: 1px; }

  /* ---- inspector ------------------------------------------------------ */
  #right {
    flex: 0 0 340px; overflow: auto; background: #1c1e24;
    border-left: 1px solid #33363e; padding: 12px 14px;
  }
  #right h2 { font-size: 12px; margin: 0 0 10px; color: #9aa0a6; text-transform: uppercase; letter-spacing: .06em; }
  .row { display: flex; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
  .row .k { color: #9aa0a6; }
  .mono { font-family: Consolas, monospace; font-size: 12px; }
  .muted { color: #9aa0a6; }
  label.num { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  label.num span { flex: 0 0 46px; color: #9aa0a6; }
  label.num input {
    flex: 1; background: #101114; color: #e8eaed; border: 1px solid #5f6368;
    border-radius: 4px; padding: 5px 8px; font: 13px Consolas, monospace;
  }
  label.num input[readonly] { color: #9aa0a6; background: #17181c; border-style: dashed; }
  #path { word-break: break-all; }
  #debug {
    border-top: 1px solid #33363e; margin-top: 12px; padding-top: 10px;
    font: 11px Consolas, monospace; color: #9aa0a6; white-space: pre-line;
  }
  #warnings { color: #f9ab00; margin-top: 8px; font-size: 11px; white-space: pre-line; }

  /* ---- status bar ------------------------------------------------------ */
  footer {
    flex: 0 0 auto; display: flex; gap: 18px; padding: 6px 14px;
    background: #1c1e24; border-top: 1px solid #33363e;
    font: 12px Consolas, monospace; color: #9aa0a6;
  }
  footer b { color: #e8eaed; font-weight: 600; }
</style>
</head>
<body>
<header>
  <h1>Stage2-F Layout Editor v0.1</h1>
  <button id="b-fit" type="button">Fit</button>
  <button id="b-zoom-out" type="button" title="Zoom out">Zoom -</button>
  <span id="zoom-value">100%</span>
  <button id="b-zoom-in" type="button" title="Zoom in">Zoom +</button>
  <span style="flex:1"></span>
  <span id="dirty" class="badge" hidden>Unsaved Changes</span>
  <button id="b-reset" type="button" title="Reset selected component to base x/y">Reset Selected</button>
  <button id="b-save" type="button">Save</button>
  <span id="save-status"></span>
</header>
<main>
  <div id="viewport">
    <div id="holder"><div id="stage"></div></div>
  </div>
  <div id="right">
    <h2>Inspector</h2>
    <div id="insp-empty" class="muted">Click a component to select it.<br>Drag to move. Ctrl+wheel to zoom.</div>
    <div id="insp-body" hidden>
      <div class="row"><span class="k">asset_id</span><b id="i-id" class="mono"></b></div>
      <div class="row"><span class="k">label</span><span id="i-label"></span></div>
      <div class="row"><span class="k">taxonomy</span><span id="i-tax"></span></div>
      <div class="row"><span class="k">z_index</span><span id="i-z" class="mono"></span></div>
      <label class="num"><span>x</span><input id="i-x" type="number" step="1"></label>
      <label class="num"><span>y</span><input id="i-y" type="number" step="1"></label>
      <label class="num"><span>width</span><input id="i-w" type="number" readonly></label>
      <label class="num"><span>height</span><input id="i-h" type="number" readonly></label>
      <div class="row"><span class="k">image path</span></div>
      <div id="path" class="mono muted"></div>
      <div id="debug"></div>
    </div>
    <div id="warnings"></div>
  </div>
</main>
<footer>
  <span>Canvas: <b id="s-canvas"></b></span>
  <span>Zoom: <b id="s-zoom"></b></span>
  <span>Components: <b id="s-count"></b></span>
  <span>Selected: <b id="s-selected">-</b></span>
  <span>Position: <b id="s-pos">-</b></span>
  <span>Dirty: <b id="s-dirty">no</b></span>
</footer>
<script>
"use strict";
const CLAMP_TO_CANVAS = true;
const ZOOM_MIN = 0.25, ZOOM_MAX = 2.0;
const ZOOM_LEVELS = [0.25, 0.50, 0.67, 0.75, 1.00, 1.25, 1.50, 2.00];

const S = {
  worldW: 0, worldH: 0,          // WORLD SPACE = source pixels
  comps: [],                     // [{asset_id,label,taxonomy,url,x,y,width,height,z_index}]
  base: {},                      // asset_id -> {x,y} from ui-compose.json
  current: {},                   // asset_id -> {x,y} live editor state
  saved: {},                     // asset_id -> {x,y} state on disk
  selected: null,
  zoom: 1.0,
  dirty: false,
};
let drag = null;

const $ = (sel) => document.querySelector(sel);
const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
const comp = (id) => S.comps.find((c) => c.asset_id === id) || null;

function markDirtyIfChanged() {
  S.dirty = Object.keys(S.current).some(
    (id) => S.current[id].x !== S.saved[id].x || S.current[id].y !== S.saved[id].y
  );
}
function markClean() { S.dirty = false; }

let flashTimer = null;
function flash(msg, isError) {
  const el = $("#save-status");
  el.textContent = msg;
  el.classList.toggle("error", !!isError);
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { el.textContent = ""; }, 6000);
}

// ------------------------------------------------------ viewport / zoom ---

function applyViewport() {
  const vp = $("#viewport"), holder = $("#holder"), stage = $("#stage");
  const z = S.zoom;
  holder.style.width = (S.worldW * z) + "px";
  holder.style.height = (S.worldH * z) + "px";
  stage.style.transform = "scale(" + z + ")";
  $("#zoom-value").textContent = Math.round(z * 100) + "%";
  $("#s-zoom").textContent = Math.round(z * 100) + "%";
}

function setZoom(z, anchorX, anchorY) {
  const vp = $("#viewport");
  z = clamp(z, ZOOM_MIN, ZOOM_MAX);
  if (anchorX == null) { const r = vp.getBoundingClientRect(); anchorX = r.left + r.width / 2; anchorY = r.top + r.height / 2; }
  const stage = $("#stage");
  const rect = stage.getBoundingClientRect();
  const worldX = (anchorX - rect.left) / S.zoom;   // world point under anchor
  const worldY = (anchorY - rect.top) / S.zoom;
  S.zoom = z;
  applyViewport();
  const r2 = stage.getBoundingClientRect();
  vp.scrollLeft += (r2.left + worldX * z) - anchorX;
  vp.scrollTop += (r2.top + worldY * z) - anchorY;
}

function fitView() {
  const vp = $("#viewport");
  const margin = 24;
  const availW = Math.max(1, vp.clientWidth - margin * 2);
  const availH = Math.max(1, vp.clientHeight - margin * 2);
  const z = clamp(Math.min(availW / S.worldW, availH / S.worldH), 0.1, ZOOM_MAX);
  S.zoom = z;
  applyViewport();
  vp.scrollLeft = Math.max(0, ($("#holder").offsetWidth - vp.clientWidth) / 2);
  vp.scrollTop = Math.max(0, ($("#holder").offsetHeight - vp.clientHeight) / 2);
}

function stepZoom(direction) {
  const z = S.zoom;
  if (direction > 0) {
    const next = ZOOM_LEVELS.find((v) => v > z + 1e-9);
    setZoom(next == null ? ZOOM_MAX : next);
  } else {
    const lower = ZOOM_LEVELS.filter((v) => v < z - 1e-9);
    setZoom(lower.length ? lower[lower.length - 1] : ZOOM_MIN);
  }
}

// Plain wheel must NEVER zoom; keep native scrolling of #viewport.
// Only Ctrl+wheel zooms (pointer is the zoom anchor).
$("#viewport").addEventListener("wheel", (e) => {
  if (!e.ctrlKey) return;
  e.preventDefault();
  setZoom(S.zoom * Math.exp(-e.deltaY * 0.0015), e.clientX, e.clientY);
}, { passive: false });

// ------------------------------------------------------------- render ---

function render() {
  for (const c of S.comps) {
    const el = document.getElementById("comp-" + c.asset_id);
    if (!el) continue;
    el.style.left = S.current[c.asset_id].x + "px";
    el.style.top = S.current[c.asset_id].y + "px";
    el.classList.toggle("selected", c.asset_id === S.selected);
  }
  renderInspector();
  renderStatus();
}

function renderStatus() {
  const c = comp(S.selected);
  $("#s-canvas").textContent = S.worldW + " x " + S.worldH;
  $("#s-count").textContent = S.comps.length;
  $("#s-selected").textContent = S.selected || "-";
  $("#s-pos").textContent = c ? S.current[c.asset_id].x + ", " + S.current[c.asset_id].y : "-";
  $("#s-dirty").textContent = S.dirty ? "yes" : "no";
  $("#dirty").hidden = !S.dirty;
}

function renderInspector() {
  const c = comp(S.selected);
  $("#insp-empty").hidden = !!c;
  $("#insp-body").hidden = !c;
  if (!c) return;
  const cur = S.current[c.asset_id], base = S.base[c.asset_id];
  $("#i-id").textContent = c.asset_id;
  $("#i-label").textContent = c.label;
  $("#i-tax").textContent = c.taxonomy;
  $("#i-z").textContent = c.z_index;
  $("#i-w").value = c.width;
  $("#i-h").value = c.height;
  $("#path").textContent = c.path;
  for (const [id, v] of [["i-x", cur.x], ["i-y", cur.y]]) {
    const el = $("#" + id);
    if (document.activeElement !== el) el.value = v;
  }
  // Coordinate self-check: world vs display vs zoom.
  const z = S.zoom;
  $("#debug").textContent =
    "BASE:    x=" + base.x + ", y=" + base.y + "\n" +
    "CURRENT: x=" + cur.x + ", y=" + cur.y + "\n" +
    "DISPLAY: left=" + Math.round(cur.x * z) + ", top=" + Math.round(cur.y * z) + "\n" +
    "ZOOM:    " + Math.round(z * 100) + "%";
}

// -------------------------------------------------------------- select ---

function select(id) {
  S.selected = id;
  render();
}

// --------------------------------------------------------------- drag ---

function onPointerDown(e) {
  if (e.button !== 0) return;
  const id = this.dataset.assetId;
  const c = comp(id);
  if (!c) return;
  select(id);
  // Anchor: drag-start client position + drag-start WORLD position + zoom.
  drag = {
    id,
    startClientX: e.clientX,
    startClientY: e.clientY,
    startWorldX: S.current[id].x,
    startWorldY: S.current[id].y,
    moved: false,
  };
  this.setPointerCapture(e.pointerId);
  e.preventDefault();
}

function onPointerMove(e) {
  if (!drag || drag.id !== this.dataset.assetId) return;
  const c = comp(drag.id);
  // Absolute recompute from drag-start anchor (no incremental += drift):
  //   world_dx = screen_dx / zoom
  //   x = start_world_x + world_dx
  const worldDx = (e.clientX - drag.startClientX) / S.zoom;
  const worldDy = (e.clientY - drag.startClientY) / S.zoom;
  let x = Math.round(drag.startWorldX + worldDx);
  let y = Math.round(drag.startWorldY + worldDy);
  if (CLAMP_TO_CANVAS) {
    x = clamp(x, 0, S.worldW - c.width);
    y = clamp(y, 0, S.worldH - c.height);
  }
  S.current[drag.id] = { x, y };
  drag.moved = true;
  render();
}

function onPointerUp(e) {
  if (!drag || drag.id !== this.dataset.assetId) return;
  if (drag.moved) markDirtyIfChanged();
  drag = null;
  render();
}

// ------------------------------------------------- inspector x/y edits ---

function commitInspector(field) {
  const c = comp(S.selected);
  if (!c) return;
  const el = $("#" + (field === "x" ? "i-x" : "i-y"));
  let v = parseInt(el.value, 10);
  if (Number.isNaN(v)) { render(); return; }
  const w = field === "x" ? S.worldW - c.width : S.worldH - c.height;
  v = clamp(v, 0, w);
  S.current[c.asset_id][field] = v;
  markDirtyIfChanged();
  render();
}

for (const [id, field] of [["i-x", "x"], ["i-y", "y"]]) {
  $("#" + id).addEventListener("change", () => commitInspector(field));
  $("#" + id).addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.target.blur(); }
    e.stopPropagation();
  });
}

// ---------------------------------------------------- keyboard nudge ---

document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT") return;
  const c = comp(S.selected);
  if (!c) return;
  const step = e.shiftKey ? 10 : 1;
  let dx = 0, dy = 0;
  if (e.key === "ArrowLeft") dx = -step;
  else if (e.key === "ArrowRight") dx = step;
  else if (e.key === "ArrowUp") dy = -step;
  else if (e.key === "ArrowDown") dy = step;
  else return;
  e.preventDefault();
  const cur = S.current[c.asset_id];
  let x = cur.x + dx, y = cur.y + dy;
  if (CLAMP_TO_CANVAS) {
    x = clamp(x, 0, S.worldW - c.width);
    y = clamp(y, 0, S.worldH - c.height);
  }
  S.current[c.asset_id] = { x, y };
  markDirtyIfChanged();
  render();
});

// -------------------------------------------------------------- reset ---

$("#b-reset").addEventListener("click", () => {
  if (!S.selected) return;
  // In-memory only; disk is written on Save.
  S.current[S.selected] = { ...S.base[S.selected] };
  markDirtyIfChanged();
  render();
});

// --------------------------------------------------------------- save ---

function buildOverrides() {
  const overrides = {};
  for (const c of S.comps) {
    const cur = S.current[c.asset_id], base = S.base[c.asset_id];
    const entry = {};
    if (cur.x !== base.x) entry.x = cur.x;   // only changed fields
    if (cur.y !== base.y) entry.y = cur.y;
    if (Object.keys(entry).length) overrides[c.asset_id] = entry;
  }
  return { overrides };
}

async function save() {
  try {
    const res = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildOverrides()),
    });
    const j = await res.json();
    if (!res.ok || !j.ok) {
      flash("Save failed: " + (j.error || res.status), true);
      return; // dirty stays true
    }
    for (const id of Object.keys(S.current)) S.saved[id] = { ...S.current[id] };
    markClean();
    render();
    flash("Saved: " + j.overrides_path + " (" + j.override_count + " overrides)");
  } catch (err) {
    flash("Save failed: " + err, true);
  }
}
$("#b-save").addEventListener("click", save);

window.addEventListener("beforeunload", (e) => {
  if (S.dirty) { e.preventDefault(); e.returnValue = ""; }
});

// -------------------------------------------------------------- tools ---

$("#b-fit").addEventListener("click", fitView);
$("#b-zoom-in").addEventListener("click", () => stepZoom(1));
$("#b-zoom-out").addEventListener("click", () => stepZoom(-1));
window.addEventListener("resize", fitView);

// --------------------------------------------------------------- init ---

async function init() {
  const st = await (await fetch("/api/state")).json();
  S.worldW = st.source_size.width;
  S.worldH = st.source_size.height;

  const stage = $("#stage");
  stage.style.width = S.worldW + "px";
  stage.style.height = S.worldH + "px";

  const bg = document.createElement("img");
  bg.id = "background";
  bg.width = S.worldW;
  bg.height = S.worldH;
  bg.src = st.files.background;
  bg.alt = "clean background";
  stage.appendChild(bg);   // fixed bottom layer, pointer-events: none

  for (const c of st.components) {
    S.comps.push(c);
    S.base[c.asset_id] = { x: c.x, y: c.y };
    S.current[c.asset_id] = { x: c.x, y: c.y };
    const el = document.createElement("img");
    el.id = "comp-" + c.asset_id;
    el.className = "component";
    el.dataset.assetId = c.asset_id;
    el.width = c.width;
    el.height = c.height;
    el.src = c.url;
    el.alt = c.asset_id;
    el.draggable = false;
    el.style.left = c.x + "px";
    el.style.top = c.y + "px";
    el.style.zIndex = String(c.z_index + 1);
    el.title = c.asset_id + " (" + c.label + ")";
    el.addEventListener("pointerdown", onPointerDown);
    el.addEventListener("pointermove", onPointerMove);
    el.addEventListener("pointerup", onPointerUp);
    el.addEventListener("pointercancel", onPointerUp);
    stage.appendChild(el);
  }

  // Merge saved layout-overrides.json (stale asset ids are ignored server-side
  // and reported as warnings).
  const ov = st.overrides && st.overrides.overrides ? st.overrides.overrides : {};
  for (const [id, entry] of Object.entries(ov)) {
    if (!S.current[id]) continue;
    S.current[id] = {
      x: entry.x != null ? entry.x : S.base[id].x,
      y: entry.y != null ? entry.y : S.base[id].y,
    };
  }
  for (const id of Object.keys(S.current)) S.saved[id] = { ...S.current[id] };
  markDirtyIfChanged(); // normally false right after load

  if (st.warnings && st.warnings.length) {
    $("#warnings").textContent = "Warnings:\n" + st.warnings.join("\n");
    console.warn("layout editor warnings:", st.warnings);
  }

  fitView();
  render();
}
init();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------


class LayoutEditorHandler(BaseHTTPRequestHandler):
    server_version = "Stage2FLayoutEditor/0.1"

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
        elif path == "/api/health":
            self._send_json({"ok": True, "server": self.server_version})
        elif path.startswith("/files/"):
            safe_id = path[len("/files/"):]
            file_path = self.server.file_whitelist.get(safe_id)
            if file_path is None:
                self._send_json({"error": "unknown file id"}, 404)
                return
            try:
                data = file_path.read_bytes()
            except OSError as exc:
                self._send_json({"error": f"cannot read file: {exc}"}, 500)
                return
            mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
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
                overrides = validate_overrides(
                    body, self.server.components,
                    self.server.source_w, self.server.source_h,
                )
                atomic_write_json(self.server.overrides_path, {
                    "schema_version": OVERRIDES_SCHEMA_VERSION,
                    "source_compose": str(self.server.compose_path),
                    "canvas": {
                        "width": self.server.source_w,
                        "height": self.server.source_h,
                    },
                    "overrides": overrides,
                    "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                })
                resolved = build_resolved_layout(
                    self.server.compose_path, self.server.compose_doc,
                    self.server.components, overrides,
                    self.server.source_w, self.server.source_h,
                )
                atomic_write_json(self.server.resolved_path, resolved)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, 400)
            return
        print(
            f"[layout-editor] saved {self.server.overrides_path} "
            f"({len(overrides)} overrides) + {self.server.resolved_path}"
        )
        self._send_json({
            "ok": True,
            "overrides_path": str(self.server.overrides_path),
            "resolved_path": str(self.server.resolved_path),
            "override_count": len(overrides),
        })

    def log_message(self, fmt, *args):  # quieter default logging
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Stage2-F Web Layout Editor v0.1 PoC")
    parser.add_argument(
        "--compose", required=True,
        help="READ ONLY ui-compose.json from a Stage2-E recompose run",
    )
    parser.add_argument(
        "--overrides-json", default=None,
        help="layout-overrides.json output path (default: next to ui-compose.json)",
    )
    parser.add_argument(
        "--resolved-json", default=None,
        help="resolved-layout.json output path (default: next to ui-compose.json)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--no-browser", action="store_true", help="do not auto-open browser")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    compose_path = Path(args.compose).resolve()
    if not compose_path.is_file():
        raise SystemExit(f"ERROR: --compose not found: {compose_path}")
    overrides_path = (
        Path(args.overrides_json).resolve()
        if args.overrides_json
        else compose_path.with_name("layout-overrides.json")
    )
    resolved_path = (
        Path(args.resolved_json).resolve()
        if args.resolved_json
        else compose_path.with_name("resolved-layout.json")
    )

    compose_doc, source_w, source_h, components, warnings = load_compose(compose_path)
    if not components:
        raise SystemExit("ERROR: no renderable components found in ui-compose.json")

    # ---- file whitelist: the ONLY files the browser may fetch -------------
    file_whitelist = {
        "background": Path(compose_doc["background"]["path"]).resolve(),
    }
    for comp_item in components:
        file_whitelist[f"component_{comp_item['asset_id']}"] = Path(comp_item["path"]).resolve()

    # verify background PNG size matches the world space
    bg_w, bg_h = image_size(file_whitelist["background"])
    if (bg_w, bg_h) != (source_w, source_h):
        warnings.append(
            f"background size {bg_w}x{bg_h} != source_size "
            f"{source_w}x{source_h}; display may misalign"
        )

    # apply existing overrides (for display only; stale ids -> warning)
    overrides_doc = None
    if overrides_path.is_file():
        try:
            overrides_doc = json.loads(overrides_path.read_text(encoding="utf-8"))
            known = {c["asset_id"] for c in components}
            for asset_id in (overrides_doc.get("overrides") or {}):
                if asset_id not in known:
                    warnings.append(f"unknown_override_asset: {asset_id} (ignored)")
        except (json.JSONDecodeError, OSError) as exc:
            warnings.append(f"cannot read {overrides_path.name}: {exc}; using base compose")
            overrides_doc = None

    server = ThreadingHTTPServer((args.host, args.port), LayoutEditorHandler)
    server.daemon_threads = True
    server.compose_path = compose_path
    server.compose_doc = compose_doc
    server.components = components
    server.source_w = source_w
    server.source_h = source_h
    server.overrides_path = overrides_path
    server.resolved_path = resolved_path
    server.file_whitelist = file_whitelist
    server.io_lock = threading.Lock()

    def build_state():
        overrides_now = None
        if overrides_path.is_file():
            try:
                overrides_now = json.loads(overrides_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                overrides_now = None
        return {
            "schema_version": compose_doc.get("schema_version"),
            "source_size": {"width": source_w, "height": source_h},
            "background": {
                "width": bg_w,
                "height": bg_h,
            },
            "files": {
                "background": "/files/background",
            },
            "components": [
                {
                    **comp_item,
                    "url": f"/files/component_{comp_item['asset_id']}",
                }
                for comp_item in components
            ],
            "overrides": overrides_now,
            "overrides_path": str(overrides_path),
            "resolved_path": str(resolved_path),
            "warnings": warnings,
        }

    server.build_state = build_state

    url = f"http://{args.host}:{args.port}/"
    print("Layout Editor:")
    print(f"  {url}")
    print(f"[layout-editor] compose (read only) : {compose_path}")
    print(f"[layout-editor] canvas              : {source_w} x {source_h}")
    print(f"[layout-editor] components          : {len(components)}")
    print(f"[layout-editor] overrides output    : {overrides_path}")
    print(f"[layout-editor] resolved output     : {resolved_path}")
    for warning in warnings:
        print(f"[layout-editor] WARNING: {warning}")
    print("[layout-editor] serving on 127.0.0.1 only (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=[url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[layout-editor] stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
