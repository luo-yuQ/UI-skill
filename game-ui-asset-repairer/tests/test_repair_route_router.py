"""Deterministic tests for Stage2-C Repair Router v0.1 PoC.

All VLM interactions are mocked (no network). Verifies the frozen routing
rules, overlay generation, failure semantics, and read-only guarantees.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

REPAIRER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPAIRER_DIR))

repair_route_router = importlib.import_module("repair_route_router")

W, H = 200, 120


def _make_target() -> np.ndarray:
    img = np.zeros((H, W, 4), np.uint8)
    img[..., 0] = 236
    img[..., 1] = 100
    img[..., 2] = 102
    img[..., 3] = 255
    return img


def _square_mask(size: int = 30) -> np.ndarray:
    m = np.zeros((H, W), bool)
    y0, x0 = (H - size) // 2, (W - size) // 2
    m[y0:y0 + size, x0:x0 + size] = True
    return m


def _write_repair_input(tmp: Path, target_role=None, mask=None,
                        status="success") -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    target = _make_target()
    mask = mask if mask is not None else _square_mask()
    Image.fromarray(target, "RGBA").save(tmp / "target-rgba.png")
    Image.fromarray(mask.astype(np.uint8) * 255, "L").save(tmp / "repair-mask.png")
    doc = {
        "schema_version": "repair-input-v0.2",
        "status": status,
        "reason_code": "ok" if status == "success" else "broken",
        "target_asset_id": "target_a",
        "occluder_asset_ids": ["occl"],
        "target_asset_path": (tmp / "target-rgba.png").as_posix(),
        "target_mask_path": None,
        "repair_mask_path": (tmp / "repair-mask.png").as_posix(),
        "occluder_masks": [{"asset_id": "occl",
                            "bbox_source": {"x": 0, "y": 0, "width": 10, "height": 10},
                            "mask_path": "x.png"}],
        "repair_pixel_count": int(mask.sum()),
    }
    if target_role is not None:
        doc["target_role"] = target_role
    path = tmp / "repair-input.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


class MockVLM:
    """Records calls; returns canned classification."""

    def __init__(self, response=None, exc=None):
        self.response = response or {
            "surface_type": "smooth_plate", "structure_crossing": False,
            "texture_complexity": "low", "semantic_content": "none",
            "reason_summary": "test"}
        self.exc = exc
        self.calls = []

    def infer_two_images_json(self, a, b, system, user):
        self.calls.append((str(a), str(b)))
        if self.exc is not None:
            raise self.exc
        return self.response


def _route(tmp_path, vlm, target_role=None, mask=None, status="success"):
    rdir, odir = tmp_path / "in", tmp_path / "out"
    jp = _write_repair_input(rdir, target_role=target_role, mask=mask, status=status)
    return repair_route_router.route_case(jp, odir, vlm_client=vlm)


# ---------------------------------------------------------------------------
# resolver (frozen rules)
# ---------------------------------------------------------------------------

class TestResolver:
    def test_2_smooth_plate_to_cv(self):
        assert repair_route_router.resolve_backend("smooth_plate", False, "none") == \
            ("cv", "SMOOTH_CONTINUOUS_SURFACE")

    def test_3_simple_gradient_to_cv(self):
        assert repair_route_router.resolve_backend("simple_gradient", False, "none") == \
            ("cv", "SIMPLE_GRADIENT_SURFACE")

    def test_4_structured_ui_to_image2(self):
        assert repair_route_router.resolve_backend("structured_ui", False, "ui_structure") == \
            ("image2", "STRUCTURED_UI_RECONSTRUCTION")

    def test_5_illustrative_texture_to_image2(self):
        assert repair_route_router.resolve_backend("illustrative_texture", False, "illustration") == \
            ("image2", "ILLUSTRATIVE_TEXTURE_RECONSTRUCTION")

    def test_6_ambiguous_to_image2(self):
        assert repair_route_router.resolve_backend("ambiguous", False, "none") == \
            ("image2", "AMBIGUOUS_SURFACE_FALLBACK_IMAGE2")

    def test_7_structure_crossing_forces_image2(self):
        # even when surface_type says gradient
        assert repair_route_router.resolve_backend("simple_gradient", True, "none")[0] == "image2"
        assert repair_route_router.resolve_backend("simple_gradient", True, "none")[1] == \
            "STRUCTURE_CROSSES_REPAIR_REGION"

    def test_8_semantic_illustration_forces_image2(self):
        assert repair_route_router.resolve_backend("simple_gradient", False, "illustration") == \
            ("image2", "ILLUSTRATIVE_TEXTURE_RECONSTRUCTION")

    def test_1_background_branch_skips_vlm(self, tmp_path):
        # VLM must never be called for background targets
        vlm = MockVLM()
        result = _route(tmp_path, vlm, target_role="background")
        assert result["status"] == "success"
        assert result["repair_backend"] == "image2"
        assert result["reason_code"] == "BACKGROUND_REQUIRES_GENERATIVE_REPAIR"
        assert vlm.calls == []   # VLM not consulted
        assert result["surface_type"] is None

    def test_background_via_taxonomy_label(self, tmp_path):
        vlm = MockVLM()
        result = _route(tmp_path, vlm, target_role="page_background")
        assert result["target_role"] == "background"
        assert result["repair_backend"] == "image2"
        assert vlm.calls == []


# ---------------------------------------------------------------------------
# routing end-to-end with mock VLM
# ---------------------------------------------------------------------------

class TestRouting:
    def test_cv_route_success(self, tmp_path):
        vlm = MockVLM({"surface_type": "smooth_plate", "structure_crossing": False,
                       "texture_complexity": "low", "semantic_content": "none",
                       "reason_summary": "red plate"})
        result = _route(tmp_path, vlm)
        assert result["status"] == "success"
        assert result["repair_backend"] == "cv"
        assert result["reason_code"] == "SMOOTH_CONTINUOUS_SURFACE"
        assert len(vlm.calls) == 1

    def test_image2_route_from_illustrative(self, tmp_path):
        vlm = MockVLM({"surface_type": "illustrative_texture", "structure_crossing": False,
                       "texture_complexity": "high", "semantic_content": "illustration",
                       "reason_summary": ""})
        result = _route(tmp_path, vlm)
        assert result["repair_backend"] == "image2"
        assert result["reason_code"] == "ILLUSTRATIVE_TEXTURE_RECONSTRUCTION"


# ---------------------------------------------------------------------------
# failure semantics & guarantees
# ---------------------------------------------------------------------------

class TestFailures:
    def test_9_invalid_enum_fails(self, tmp_path):
        vlm = MockVLM({"surface_type": "banana", "structure_crossing": False,
                       "texture_complexity": "low", "semantic_content": "none"})
        result = _route(tmp_path, vlm)
        assert result["status"] == "failed"
        assert result["reason_code"] == "vlm_invalid_output"

    def test_9_invalid_structure_crossing_fails(self, tmp_path):
        vlm = MockVLM({"surface_type": "smooth_plate", "structure_crossing": "no",
                       "texture_complexity": "low", "semantic_content": "none"})
        result = _route(tmp_path, vlm)
        assert result["reason_code"] == "vlm_invalid_output"

    def test_11_mask_size_mismatch_fails(self, tmp_path):
        vlm = MockVLM()
        bad = np.zeros((H - 3, W + 5), bool)
        bad[5:15, 5:15] = True
        result = _route(tmp_path, vlm, mask=bad)
        assert result["status"] == "failed"
        assert result["reason_code"] == "mask_size_mismatch"
        assert vlm.calls == []   # never reached VLM

    def test_upstream_not_success_skips(self, tmp_path):
        vlm = MockVLM()
        result = _route(tmp_path, vlm, status="failed")
        assert result["status"] == "failed"
        assert result["reason_code"] == "repair_input_not_success"
        assert vlm.calls == []

    def test_10_overlay_size_equals_target(self, tmp_path):
        vlm = MockVLM()
        _route(tmp_path, vlm)
        odir = tmp_path / "out"
        target = np.array(Image.open(odir / "target.png").convert("RGBA"))
        overlay = np.array(Image.open(odir / "repair-overlay.png").convert("RGBA"))
        assert overlay.shape == target.shape

    def test_overlay_is_semitransparent_red_inside_mask(self, tmp_path):
        vlm = MockVLM()
        _route(tmp_path, vlm)
        overlay = np.array(Image.open(tmp_path / "out" / "repair-overlay.png").convert("RGBA"))
        mask = _square_mask()
        px = overlay[mask]
        assert (px[:, 0] > 200).all()          # red dominant
        assert (px[:, 1] < 60).all()           # original G=100 blended down
        assert overlay[~mask].shape[0] > 0

    def test_overlay_preserves_outside_pixels(self, tmp_path):
        vlm = MockVLM()
        _route(tmp_path, vlm)
        odir = tmp_path / "out"
        target = np.array(Image.open(odir / "target.png").convert("RGBA"))
        overlay = np.array(Image.open(odir / "repair-overlay.png").convert("RGBA"))
        mask = _square_mask()
        assert np.array_equal(overlay[~mask], target[~mask])

    def test_12_router_does_not_modify_repair_input(self, tmp_path):
        vlm = MockVLM()
        rdir = tmp_path / "in"
        jp = _write_repair_input(rdir)
        before = jp.read_bytes()
        before_target = (rdir / "target-rgba.png").read_bytes()
        before_mask = (rdir / "repair-mask.png").read_bytes()
        repair_route_router.route_case(jp, tmp_path / "out", vlm_client=vlm)
        assert jp.read_bytes() == before
        assert (rdir / "target-rgba.png").read_bytes() == before_target
        assert (rdir / "repair-mask.png").read_bytes() == before_mask

    def test_13_router_writes_no_repaired_output(self, tmp_path):
        # Router must not execute any backend
        vlm = MockVLM()
        result = _route(tmp_path, vlm)
        odir = tmp_path / "out"
        names = {p.name for p in odir.iterdir()}
        assert "repaired.png" not in names
        assert result.get("output_path") is None
        assert set(names) == {"target.png", "repair-overlay.png", "route-run-000.json"}

    def test_vlm_transport_error_propagates(self, tmp_path):
        from vlm_client import VLMTransportError  # analyzer scripts on sys.path
        vlm = MockVLM(exc=VLMTransportError("HTTP 500: boom"))
        result = _route(tmp_path, vlm)
        assert result["status"] == "failed"
        assert result["reason_code"] == "vlm_transport_error"

    def test_route_schema_validates(self, tmp_path):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            pytest.skip("jsonschema not available")
        vlm = MockVLM()
        result = _route(tmp_path, vlm)
        schema = json.loads(
            (REPAIRER_DIR / "schemas" / "repair-route.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(result)
