#!/usr/bin/env python3
"""Stage2-C Repair Router v0.1 (PoC).

Chooses the repair backend (cv | image2) for one authoritative repair case.
The Router NEVER executes a repair: it does not call the frozen
Traditional CV Repair v0.1, does not call Image2, and never modifies the
repair input or any upstream artifact.

Decision pipeline (engineering code is the final authority):

1. Deterministic first branch — if target metadata marks the asset as a
   background (background / page_background / scene_background), route to
   image2 with BACKGROUND_REQUIRES_GENERATIVE_REPAIR. Backgrounds never
   enter CV.
2. Otherwise a VLM (temperature 0) classifies the repair region's visual
   surface from TWO images: the authoritative target asset image and a
   deterministically generated semi-transparent red repair-mask overlay
   (same canvas, no resize/crop).
3. A frozen deterministic resolver maps the classification to the backend:
       structure_crossing=true            -> image2
       semantic_content == illustration   -> image2
       surface_type in {smooth_plate,
                        simple_gradient}  -> cv
       structured_ui                      -> image2
       illustrative_texture               -> image2
       ambiguous                          -> image2 (safe fallback)

VLM reuse: subclasses the production ResponsesAPIVLMClient
(game-ui-asset-analyzer/scripts/vlm_client.py) only to (a) attach two
images to one message and (b) send temperature=0. No new HTTP client.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

REPAIRER_DIR = Path(__file__).resolve().parent
ANALYZER_SCRIPTS = Path(__file__).resolve().parents[1] / "game-ui-asset-analyzer" / "scripts"
RESULT_SCHEMA_PATH = REPAIRER_DIR / "schemas" / "repair-route.schema.json"

SCHEMA_VERSION = "repair-route-v0.1"

SURFACE_TYPES = ("smooth_plate", "simple_gradient", "structured_ui",
                 "illustrative_texture", "ambiguous")
TEXTURE_COMPLEXITIES = ("low", "medium", "high")
SEMANTIC_CONTENTS = ("none", "ui_structure", "illustration")

CV_SURFACE_TYPES = {"smooth_plate", "simple_gradient"}

REASON_FOR_SURFACE = {
    "smooth_plate": "SMOOTH_CONTINUOUS_SURFACE",
    "simple_gradient": "SIMPLE_GRADIENT_SURFACE",
    "structured_ui": "STRUCTURED_UI_RECONSTRUCTION",
    "illustrative_texture": "ILLUSTRATIVE_TEXTURE_RECONSTRUCTION",
    "ambiguous": "AMBIGUOUS_SURFACE_FALLBACK_IMAGE2",
}
BACKGROUND_REASONS = ("background", "page_background", "scene_background")

SYSTEM_PROMPT = """You are the Stage2-C Repair Router surface classifier.
Judge only what is visible in the provided images. You receive:
IMAGE 1: the authoritative target asset image.
IMAGE 2: the same target asset with a semi-transparent red overlay marking the region that must be repaired.
Classify ONLY the visual surface under and immediately around the red overlay region.
Do not assume what the region should contain; do not guess asset history.
Answer with a single JSON object matching the requested fields. No markdown fences, no extra text."""

USER_PROMPT = """Classify the visual surface of the red-overlay repair region in this game UI asset.

Definitions:
- smooth_plate: large continuous UI panel, near-solid color, very weak color variation, no specific pattern to restore, no complex edges crossing the region.
- simple_gradient: smooth linear/radial color change, no semantic pattern, locally inferable from the surrounding surface, region does not cross important structure.
- structured_ui: region involves borders, strokes, decorations, icon structure, clear shape continuation, multi-layer highlights, grooves, metal edges, complex button structure.
- illustrative_texture: region involves illustrations, characters, scenes, patterns, materials, complex textures, semantic objects — content NOT inferable from a simple smooth surface.
- ambiguous: you cannot decide reliably.

Also report:
- structure_crossing: true if the overlay clearly crosses a border, outline, highlight line, decorative line, or pattern structure.
- texture_complexity: low | medium | high for the region's texture.
- semantic_content: none | ui_structure | illustration.

Return exactly this JSON object with no extra keys:
{"surface_type": "...", "structure_crossing": false, "texture_complexity": "low", "semantic_content": "none", "reason_summary": "..."}"""


class RepairRouteError(RuntimeError):
    def __init__(self, reason_code: str, message: str):
        super().__init__(f"{reason_code}: {message}")
        self.reason_code = reason_code
        self.message = message


# ---------------------------------------------------------------------------
# overlay generation (deterministic)
# ---------------------------------------------------------------------------

def build_repair_overlay(target: np.ndarray, repair_mask: np.ndarray) -> np.ndarray:
    """Target RGBA + semi-transparent red overlay on the repair mask.

    Same canvas, no resize, no crop, coordinates untouched."""
    if repair_mask.shape != target.shape[:2]:
        raise RepairRouteError("mask_size_mismatch",
                               f"repair mask {repair_mask.shape} != target {target.shape[:2]}")
    overlay = target.copy()
    region = repair_mask.astype(bool)
    # blend 50% red over RGB, keep alpha channel semantics
    overlay_rgb = overlay[..., :3].astype(np.float64)
    overlay_rgb[region] = 0.5 * overlay_rgb[region] + 0.5 * np.array([255.0, 0.0, 0.0])
    overlay[..., :3] = np.clip(overlay_rgb, 0, 255).astype(np.uint8)
    # make the overlaid area fully visible even if target alpha was partial
    overlay[region, 3] = np.maximum(overlay[region, 3], 200)
    return overlay


def load_target_role(repair_input: dict[str, Any]) -> str:
    """Read the target's role metadata. Never guesses: only explicit labels
    count as background. Returns 'normal_asset' otherwise."""
    role = repair_input.get("target_role")
    if isinstance(role, str) and role.strip().lower() in BACKGROUND_REASONS:
        return "background"
    taxonomy = (repair_input.get("target_taxonomy")
                or repair_input.get("target_label") or "")
    if isinstance(taxonomy, str) and taxonomy.strip().lower() in BACKGROUND_REASONS:
        return "background"
    return "normal_asset"


# ---------------------------------------------------------------------------
# deterministic resolver (the frozen routing rules)
# ---------------------------------------------------------------------------

def resolve_backend(surface_type: str, structure_crossing: bool,
                    semantic_content: str) -> tuple[str, str]:
    """Frozen mapping. Returns (repair_backend, reason_code)."""
    if structure_crossing:
        return "image2", "STRUCTURE_CROSSES_REPAIR_REGION"
    if semantic_content == "illustration":
        return "image2", "ILLUSTRATIVE_TEXTURE_RECONSTRUCTION"
    if surface_type in CV_SURFACE_TYPES:
        return "cv", REASON_FOR_SURFACE[surface_type]
    if surface_type == "structured_ui":
        return "image2", REASON_FOR_SURFACE[surface_type]
    if surface_type == "illustrative_texture":
        return "image2", REASON_FOR_SURFACE[surface_type]
    return "image2", REASON_FOR_SURFACE["ambiguous"]


# ---------------------------------------------------------------------------
# VLM boundary
# ---------------------------------------------------------------------------

def _get_vlm_cls():
    """Import the production VLM client lazily; no network at import time."""
    if str(ANALYZER_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(ANALYZER_SCRIPTS))
    from vlm_client import (VLMClientConfig, VLMConfigurationError, VLMError,  # noqa: E402
                            ResponsesAPIVLMClient)

    class TwoImageResponsesVLMClient(ResponsesAPIVLMClient):
        """Thin subclass: two images + temperature 0. Same endpoint, auth,
        parsing. Exists only because the frozen production client sends
        exactly one image and no temperature."""

        def infer_two_images_json(self, image_path_a: Path, image_path_b: Path,
                                  system_prompt: str, user_prompt: str) -> dict[str, Any]:
            from vlm_client import extract_output_text, parse_json_object  # noqa: E402
            payload = {
                "model": self.config.model,
                "instructions": system_prompt,
                # temperature 0: stability requirement of the Router PoC
                "temperature": 0,
                "input": [{
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": user_prompt},
                        {"type": "input_image",
                         "image_url": self._encode(image_path_a)},
                        {"type": "input_image",
                         "image_url": self._encode(image_path_b)},
                    ],
                }],
                "max_output_tokens": self.max_output_tokens,
            }
            headers = {
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Stage2C-RepairRouter/0.1",
                "Accept-Encoding": "identity",
            }
            response = self.session.post(self.endpoint, headers=headers,
                                         json=payload, timeout=self.config.timeout)
            status = getattr(response, "status_code", None)
            if status is None or not (200 <= status < 300):
                from vlm_client import VLMTransportError, _safe_provider_body
                body = _safe_provider_body(getattr(response, "text", ""), self.config.api_key)
                raise VLMTransportError(f"HTTP {status}: {body}")
            from vlm_client import parse_json_object as _p
            provider_response = json.loads(response.text)
            output_text = extract_output_text(provider_response).strip()
            return parse_json_object(output_text)

        @staticmethod
        def _encode(image_path: Path) -> str:
            from vlm_client import encode_image_as_data_url
            return encode_image_as_data_url(image_path)

    return TwoImageResponsesVLMClient, VLMClientConfig, VLMError, VLMConfigurationError


def read_provider_credentials() -> tuple[str, str, str]:
    """Resolve STAGE2A_VLM_* env, falling back to the user-level
    YOUCHU_API_KEY registry value for base URL / key defaults used by this
    project. Values are never logged."""
    import os
    base_url = os.environ.get("STAGE2A_VLM_BASE_URL", "").strip()
    api_key = os.environ.get("STAGE2A_VLM_API_KEY", "").strip()
    model = os.environ.get("STAGE2A_VLM_MODEL", "").strip()
    if not api_key:
        api_key = _read_user_env_registry("YOUCHU_API_KEY")
    if not base_url:
        base_url = "https://ai-api.youchu.work"
    if not model:
        model = "glm-5.3-flash"
    if not api_key:
        raise RepairRouteError("vlm_config_missing",
                               "no API key: set STAGE2A_VLM_API_KEY or YOUCHU_API_KEY")
    return base_url, api_key, model


def _read_user_env_registry(name: str) -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value).strip()
    except (OSError, ImportError):
        return ""


def classify_surface(vlm_client, target_png: Path, overlay_png: Path) -> dict[str, Any]:
    """One VLM call; validates enums strictly. Raises RepairRouteError on
    invalid output."""
    cls, *_ = _get_vlm_cls()
    try:
        raw = vlm_client.infer_two_images_json(target_png, overlay_png,
                                               SYSTEM_PROMPT, USER_PROMPT)
    except Exception as exc:  # VLMError subclasses
        code = getattr(exc, "code", "vlm_error")
        raise RepairRouteError(code if code != "vlm_error" else "vlm_error", str(exc)) from exc
    if not isinstance(raw, dict):
        raise RepairRouteError("vlm_invalid_output", "VLM output is not a JSON object")

    surface = raw.get("surface_type")
    crossing = raw.get("structure_crossing")
    texture = raw.get("texture_complexity")
    semantic = raw.get("semantic_content")

    if surface not in SURFACE_TYPES:
        raise RepairRouteError("vlm_invalid_output", f"surface_type invalid: {surface!r}")
    if not isinstance(crossing, bool):
        raise RepairRouteError("vlm_invalid_output",
                               f"structure_crossing must be bool: {crossing!r}")
    if texture not in TEXTURE_COMPLEXITIES:
        raise RepairRouteError("vlm_invalid_output",
                               f"texture_complexity invalid: {texture!r}")
    if semantic not in SEMANTIC_CONTENTS:
        raise RepairRouteError("vlm_invalid_output",
                               f"semantic_content invalid: {semantic!r}")
    return {
        "surface_type": surface,
        "structure_crossing": crossing,
        "texture_complexity": texture,
        "semantic_content": semantic,
        "reason_summary": str(raw.get("reason_summary", ""))[:500],
    }


# ---------------------------------------------------------------------------
# main routing entry
# ---------------------------------------------------------------------------

def route_case(repair_input_path: Path, output_dir: Path,
               vlm_client=None, run_index: int = 0) -> dict[str, Any]:
    """Route one case. Writes overlay + route JSON into output_dir.
    Never executes a repair backend, never modifies the repair input."""
    output_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "failed",
        "reason_code": None,
        "message": None,
    }

    def finish(status: str, doc: dict[str, Any]) -> dict[str, Any]:
        doc["status"] = status
        out = output_dir / f"route-run-{run_index:03d}.json"
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return doc

    try:
        inp = json.loads(repair_input_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        result["reason_code"] = "invalid_repair_input"
        result["message"] = str(exc)
        return finish("failed", result)
    if inp.get("status") != "success":
        result["reason_code"] = "repair_input_not_success"
        result["message"] = f"upstream status: {inp.get('status')}"
        return finish("failed", result)

    result["target_asset_id"] = inp["target_asset_id"]
    result["occluder_asset_ids"] = list(inp["occluder_asset_ids"])
    result["input_target_path"] = inp["target_asset_path"]
    result["input_repair_mask_path"] = inp["repair_mask_path"]

    # ---------- deterministic background branch ----------
    target_role = load_target_role(inp)
    result["target_role"] = target_role
    if target_role == "background":
        result.update({
            "repair_backend": "image2",
            "reason_code": "BACKGROUND_REQUIRES_GENERATIVE_REPAIR",
            "surface_type": None,
            "structure_crossing": None,
            "texture_complexity": None,
            "semantic_content": None,
        })
        return finish("success", result)

    # ---------- load target + mask (read-only) ----------
    target = np.array(Image.open(inp["target_asset_path"]).convert("RGBA"))
    mask_img = np.array(Image.open(inp["repair_mask_path"]).convert("L"))
    if mask_img.shape != target.shape[:2]:
        result["reason_code"] = "mask_size_mismatch"
        result["message"] = f"repair mask {mask_img.shape} != target {target.shape[:2]}"
        return finish("failed", result)

    # ---------- deterministic overlay ----------
    overlay = build_repair_overlay(target, mask_img > 0)
    target_png = output_dir / "target.png"
    overlay_png = output_dir / "repair-overlay.png"
    if not target_png.exists():
        Image.fromarray(target, "RGBA").save(target_png)
    Image.fromarray(overlay, "RGBA").save(overlay_png)
    result["overlay_path"] = overlay_png.as_posix()

    # ---------- VLM classification ----------
    if vlm_client is None:
        cls, cfg_cls, vlm_error, cfg_error = _get_vlm_cls()
        try:
            base_url, api_key, model = read_provider_credentials()
            config = cfg_cls(base_url=base_url, api_key=api_key, model=model, timeout=90.0)
            vlm_client = cls(config)
        except cfg_error as exc:
            result["reason_code"] = "vlm_config_missing"
            result["message"] = str(exc)
            return finish("failed", result)
        result.setdefault("vlm_metadata", {})["model"] = model

    try:
        classification = classify_surface(vlm_client, target_png, overlay_png)
    except RepairRouteError as exc:
        result["reason_code"] = exc.reason_code
        result["message"] = exc.message
        return finish("failed", result)

    # ---------- deterministic resolver (engineering code decides) ----------
    backend, reason_code = resolve_backend(
        classification["surface_type"],
        classification["structure_crossing"],
        classification["semantic_content"])

    result.update({
        "surface_type": classification["surface_type"],
        "structure_crossing": classification["structure_crossing"],
        "texture_complexity": classification["texture_complexity"],
        "semantic_content": classification["semantic_content"],
        "reason_summary": classification["reason_summary"],
        "repair_backend": backend,
        "reason_code": reason_code,
    })
    return finish("success", result)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage2-C Repair Router v0.1 PoC")
    parser.add_argument("--repair-input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-index", type=int, default=0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    doc = route_case(Path(args.repair_input), Path(args.output_dir),
                     run_index=args.run_index)
    print(json.dumps({k: doc[k] for k in
                      ("status", "target_asset_id", "repair_backend", "reason_code")
                      if k in doc}, ensure_ascii=False))
    return 0 if doc["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
