"""Deterministic tests for Stage2-C Traditional CV Repair v0.1.

No SAM checkpoint, no VLM, no network, no real run artifacts. All inputs
are synthesized in-memory following the repair-input-v0.2 contract.
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

repair_asset_cv = importlib.import_module("repair_asset_cv")

W, H = 200, 120
BG_RGB = (236, 100, 102)          # smooth red plate
BG_GRADIENT = True                # subtle vertical gradient so linear fit is meaningful
DILATION_MIN = repair_asset_cv.DILATION_MIN_PX
DILATION_MAX = repair_asset_cv.DILATION_MAX_PX


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_target() -> np.ndarray:
    """RGBA target with a smooth red plate + black border frame (2px)."""
    ys, xs = np.mgrid[0:H, 0:W]
    img = np.zeros((H, W, 4), np.uint8)
    img[..., 0] = np.clip(BG_RGB[0] - ys // 40, 0, 255)
    img[..., 1] = BG_RGB[1]
    img[..., 2] = BG_RGB[2]
    img[..., 3] = 255
    img[:2, :, :3] = 0
    img[-2:, :, :3] = 0
    img[:, :2, :3] = 0
    img[:, -2:, :3] = 0
    return img


def _make_occluder_mask(w: int, h: int) -> np.ndarray:
    m = np.zeros((h, w), bool)
    m[2:h - 2, 2:w - 2] = True   # hollow border, mimics SAM mask with anti-alias edge
    return m


def _write_repair_input(tmp: Path, target: np.ndarray, occluders,
                        repair_mask: np.ndarray):
    """occluders: list of (asset_id, bbox dict). Writes files + returns json path."""
    tmp.mkdir(parents=True, exist_ok=True)
    tdir = tmp / "target"
    tdir.mkdir(exist_ok=True)
    Image.fromarray(target, "RGBA").save(tdir / "target-rgba.png")
    Image.fromarray(repair_mask.astype(np.uint8) * 255, "L").save(tdir / "repair-mask.png")
    occ_entries = []
    for occ_id, bbox in occluders:
        m = _make_occluder_mask(bbox["width"], bbox["height"])
        Image.fromarray(m.astype(np.uint8) * 255, "L").save(tmp / f"{occ_id}-mask.png")
        occ_entries.append({"asset_id": occ_id, "bbox_source": bbox,
                            "mask_path": (tmp / f"{occ_id}-mask.png").as_posix()})
    doc = {
        "schema_version": "repair-input-v0.2",
        "status": "success",
        "reason_code": "ok",
        "message": None,
        "target_asset_id": "target_a",
        "occluder_asset_ids": [o[0] for o in occluders],
        "target_bbox_source": {"x": 0, "y": 0, "width": W, "height": H},
        "target_frame": {"x": 0, "y": 0, "width": W, "height": H, "kind": "extraction_roi"},
        "target_asset_path": (tdir / "target-rgba.png").as_posix(),
        "target_mask_path": None,
        "occluder_masks": occ_entries,
        "repair_mask_path": (tdir / "repair-mask.png").as_posix(),
        "repair_working_image_path": None,
        "repair_pixel_count": int(repair_mask.sum()),
        "repair_ratio_of_target_mask": 0.1,
        "projected_pixels_before_frame_clip": int(repair_mask.sum()),
        "projected_pixels_after_frame_clip": int(repair_mask.sum()),
    }
    path = tmp / "repair-input.json"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def _center_square_mask(size: int = 30) -> np.ndarray:
    m = np.zeros((H, W), bool)
    y0 = (H - size) // 2
    x0 = (W - size) // 2
    m[y0:y0 + size, x0:x0 + size] = True
    return m


def _run(tmp_path, target=None, occluders=None, repair_mask=None, **kwargs):
    target = target if target is not None else _make_target()
    occluders = occluders if occluders is not None else [
        ("occl_44x54", {"x": 60, "y": 30, "width": 44, "height": 54})]
    repair_mask = repair_mask if repair_mask is not None else _center_square_mask()
    rdir = tmp_path / "in"
    odir = tmp_path / "out"
    json_path = _write_repair_input(rdir, target, occluders, repair_mask)
    result = repair_asset_cv.run_cv_repair(json_path, odir, **kwargs)
    return result, odir


# ---------------------------------------------------------------------------
# structural properties
# ---------------------------------------------------------------------------

class TestStructural:
    def test_1_output_size_equals_target_size(self, tmp_path):
        result, odir = _run(tmp_path)
        assert result["status"] == "success"
        out = np.array(Image.open(odir / "repaired.png").convert("RGBA"))
        assert out.shape == (H, W, 4)
        assert result["target_size"] == {"width": W, "height": H}

    def test_2_outside_rgb_byte_identical(self, tmp_path):
        target = _make_target()
        result, odir = _run(tmp_path, target=target)
        assert result["status"] == "success"
        out = np.array(Image.open(odir / "repaired.png").convert("RGBA"))
        eff = np.array(Image.open(odir / "repair-mask-effective.png").convert("L")) > 0
        assert np.array_equal(out[~eff, :3], target[~eff, :3])
        assert result["mask_outside_rgb_changed"] == 0

    def test_3_outside_alpha_byte_identical(self, tmp_path):
        target = _make_target()
        result, odir = _run(tmp_path, target=target)
        assert result["status"] == "success"
        out = np.array(Image.open(odir / "repaired.png").convert("RGBA"))
        eff = np.array(Image.open(odir / "repair-mask-effective.png").convert("L")) > 0
        assert np.array_equal(out[~eff, 3], target[~eff, 3])
        assert result["mask_outside_alpha_changed"] == 0

    def test_4_dilation_never_exceeds_target_frame(self, tmp_path):
        # mask touching the frame edge: dilated mask must be clipped, not crash
        m = np.zeros((H, W), bool)
        m[0:10, 0:10] = True
        result, odir = _run(tmp_path, repair_mask=m)
        assert result["status"] == "success"
        eff = np.array(Image.open(odir / "repair-mask-effective.png").convert("L")) > 0
        assert eff.shape == (H, W)
        assert (eff >= m).all()
        assert result["repair_mask_effective_pixels"] >= result["repair_mask_original_pixels"] > 0


# ---------------------------------------------------------------------------
# dilation policy
# ---------------------------------------------------------------------------

class TestDilationPolicy:
    def test_6_ratio_calibration_44x54_gives_2px(self):
        assert repair_asset_cv.compute_radius_for_bbox(44, 54, 0.05) == 2

    def test_7_small_asset_20x30_gives_1px(self):
        assert repair_asset_cv.compute_radius_for_bbox(20, 30, 0.05) == 1

    def test_8_large_asset_200x250_clamps_to_6px(self):
        assert repair_asset_cv.compute_radius_for_bbox(200, 250, 0.05) == 6
        # raw = 10 -> clamp to DILATION_MAX

    def test_10_multi_occluder_each_uses_own_bbox_radius(self, tmp_path):
        target = _make_target()
        occs = [("occ_big", {"x": 0, "y": 0, "width": 200, "height": 100}),
                ("occ_small", {"x": 140, "y": 90, "width": 20, "height": 30})]
        m = np.zeros((H, W), bool)
        m[10:40, 10:40] = True
        result, odir = _run(tmp_path, target=target, occluders=occs, repair_mask=m)
        assert result["status"] == "success"
        assert result["dilation_mode"] == "ratio"
        by_id = {o["asset_id"]: o for o in result["per_occluder_dilation"]}
        assert by_id["occ_big"]["effective_radius_px"] == 5              # 100*0.05=5, within clamp
        assert by_id["occ_small"]["effective_radius_px"] == 1            # 20*0.05=1

    def test_5_disable_dilation_effective_equals_input(self, tmp_path):
        m = _center_square_mask()
        result, odir = _run(tmp_path, repair_mask=m, disable_dilation=True)
        assert result["status"] == "success"
        assert result["dilation_mode"] == "disabled"
        eff = np.array(Image.open(odir / "repair-mask-effective.png").convert("L")) > 0
        assert np.array_equal(eff, m)
        assert result["repair_mask_effective_pixels"] == result["repair_mask_original_pixels"]

    def test_9_fixed_override_takes_priority(self, tmp_path):
        m = _center_square_mask()
        result, odir = _run(tmp_path, repair_mask=m, dilation_px=3)
        assert result["status"] == "success"
        assert result["dilation_mode"] == "fixed_override"
        eff = np.array(Image.open(odir / "repair-mask-effective.png").convert("L")) > 0
        # 3px binary dilation of the center square (scipy cross structure)
        import scipy.ndimage as ndi
        assert np.array_equal(eff, ndi.binary_dilation(m, iterations=3))
        assert result["repair_mask_dilation_ratio"] is None
        # override really took effect: differs from 2px default-ratio dilation (44x54 -> 2)
        r2, odir2 = _run(tmp_path / "ratio", repair_mask=m)
        assert r2["dilation_mode"] == "ratio"
        eff2 = np.array(Image.open(odir2 / "repair-mask-effective.png").convert("L")) > 0
        assert not np.array_equal(eff, eff2)

    def test_ratio_mode_records_ratio(self, tmp_path):
        result, _ = _run(tmp_path)
        assert result["status"] == "success"
        assert result["dilation_mode"] == "ratio"
        assert result["repair_mask_dilation_ratio"] == 0.05


# ---------------------------------------------------------------------------
# failure semantics
# ---------------------------------------------------------------------------

class TestFailures:
    def test_11_empty_mask_is_skipped(self, tmp_path):
        result, odir = _run(tmp_path, repair_mask=np.zeros((H, W), bool))
        assert result["status"] == "skipped"
        assert result["reason_code"] == "empty_repair_mask"
        assert not (odir / "repaired.png").exists()

    def test_12_mask_size_mismatch_fails(self, tmp_path):
        target = _make_target()
        bad_mask = np.zeros((H - 5, W - 5), bool)
        bad_mask[10:20, 10:20] = True
        result, odir = _run(tmp_path, target=target, repair_mask=bad_mask)
        assert result["status"] == "failed"
        assert result["reason_code"] == "mask_size_mismatch"
        assert not (odir / "repaired.png").exists()

    def test_upstream_failed_input_is_skipped(self, tmp_path):
        target = _make_target()
        occluders = [("occl", {"x": 60, "y": 30, "width": 44, "height": 54})]
        m = _center_square_mask()
        rdir = tmp_path / "in"
        odir = tmp_path / "out"
        jp = _write_repair_input(rdir, target, occluders, m)
        doc = json.loads(jp.read_text(encoding="utf-8"))
        doc["status"] = "failed"
        jp.write_text(json.dumps(doc), encoding="utf-8")
        result = repair_asset_cv.run_cv_repair(jp, odir)
        assert result["status"] == "skipped"

    def test_insufficient_sampling_fails(self, tmp_path):
        # mask covering the whole frame -> sampling ring lands outside the
        # frame, zero reliable pixels
        m = np.ones((H, W), bool)
        result, odir = _run(tmp_path, repair_mask=m)
        assert result["status"] == "failed"
        assert result["reason_code"] == "insufficient_sampling_pixels"


# ---------------------------------------------------------------------------
# end-to-end smooth-plate property
# ---------------------------------------------------------------------------

class TestSmoothPlate:
    def test_13_smooth_plate_repairs_stably(self, tmp_path):
        target = _make_target()
        result, odir = _run(tmp_path, target=target)
        assert result["status"] == "success"
        out = np.array(Image.open(odir / "repaired.png").convert("RGBA"))
        m = _center_square_mask()
        # repaired pixels close to the smooth background (fit should recover plate)
        fill = out[m, :3].astype(float)
        bg_ref = target[m, :3].astype(float)  # original had occluder pixels? no — synthesized mask over clean plate
        assert np.abs(fill - bg_ref).mean() < 6.0
        assert result["surface_fit_method"] == "linear_irls_huber_v0.1"
        assert result["alpha_repair_method"] == repair_asset_cv.ALPHA_REPAIR_METHOD

    def test_result_schema_validates(self, tmp_path):
        result, odir = _run(tmp_path)
        assert result["status"] == "success"
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            pytest.skip("jsonschema not available")
        schema = json.loads(
            (REPAIRER_DIR / "schemas" / "cv-repair-result.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(result)
        written = json.loads((odir / "result.json").read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(written)
