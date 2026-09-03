from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from PIL import Image

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import ui_text_alpha_hole as alpha_hole  # noqa: E402
from ui_text_alpha_hole import AlphaHoleInputError, generate_alpha_hole  # noqa: E402


def _gradient_bgr(width: int, height: int) -> np.ndarray:
    """Distinct per-pixel BGR values so RGB preservation is verifiable."""

    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :, 0] = np.arange(width, dtype=np.uint8)[None, :]
    image[:, :, 1] = np.arange(height, dtype=np.uint8)[:, None]
    image[:, :, 2] = 173
    return image


def _write_source(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    encoded.tofile(path)


def _write_regions(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def _load_rgba(path: Path) -> np.ndarray:
    payload = np.fromfile(path, dtype=np.uint8)
    decoded = cv2.imdecode(payload, cv2.IMREAD_UNCHANGED)
    assert decoded is not None and decoded.ndim == 3 and decoded.shape[2] == 4
    return decoded


def _load_gray(path: Path) -> np.ndarray:
    payload = np.fromfile(path, dtype=np.uint8)
    decoded = cv2.imdecode(payload, cv2.IMREAD_GRAYSCALE)
    assert decoded is not None and decoded.ndim == 2
    return decoded


def _plan_entry(
    bbox_source: dict[str, int],
    *,
    ownership: str = "ui_owned",
) -> dict[str, Any]:
    return {
        "text": "开始游戏",
        "bbox_analysis": {"x": 5, "y": 8, "width": 10, "height": 5},
        "bbox_source": bbox_source,
        "ownership": ownership,
        "semantic_role": "button_label",
        "decision": (
            "remove_for_background_repair"
            if ownership == "ui_owned"
            else "preserve_as_visual_asset"
        ),
        "confidence": 0.98,
    }


def test_basic_bbox_padding_zero(tmp_path: Path) -> None:
    """Test 1: CLI run, alpha 0 inside bbox, 255 outside, size and mode kept."""

    source = _gradient_bgr(100, 100)
    image_path = tmp_path / "source.png"
    regions_path = tmp_path / "plan.json"
    output_dir = tmp_path / "out"
    _write_source(image_path, source)
    _write_regions(
        regions_path,
        {"texts": [_plan_entry({"x": 20, "y": 30, "width": 40, "height": 20})]},
    )

    exit_code = alpha_hole.main(
        [
            "--image",
            str(image_path),
            "--regions-json",
            str(regions_path),
            "--output-dir",
            str(output_dir),
            "--padding",
            "0",
        ]
    )

    assert exit_code == 0
    hole_path = output_dir / "alpha-hole.png"
    assert hole_path.is_file()
    with Image.open(hole_path) as probe:
        assert probe.mode == "RGBA"
        assert probe.size == (100, 100)
    rgba = _load_rgba(hole_path)
    assert rgba.shape == (100, 100, 4)
    expected = np.full((100, 100), 255, dtype=np.uint8)
    expected[30:50, 20:60] = 0
    assert np.array_equal(rgba[:, :, 3], expected)


def test_top_left_clamp_with_padding(tmp_path: Path) -> None:
    """Test 2: bbox at the top-left corner with padding must clamp, not crash."""

    source = _gradient_bgr(100, 100)
    _write_source(tmp_path / "source.png", source)
    _write_regions(tmp_path / "plan.json", [{"x": 0, "y": 0, "width": 10, "height": 10}])

    diagnostics = generate_alpha_hole(
        tmp_path / "source.png",
        tmp_path / "plan.json",
        tmp_path / "out",
        padding=10,
    )

    rgba = _load_rgba(tmp_path / "out" / "alpha-hole.png")
    assert rgba.shape == (100, 100, 4)
    expected = np.full((100, 100), 255, dtype=np.uint8)
    expected[0:20, 0:20] = 0
    assert np.array_equal(rgba[:, :, 3], expected)
    assert diagnostics.region_count == 1
    assert diagnostics.applied_count == 1
    assert diagnostics.skipped_count == 0


def test_bottom_right_clamp_with_padding(tmp_path: Path) -> None:
    """Test 3: bbox overflowing the bottom-right corner must clamp."""

    source = _gradient_bgr(100, 100)
    _write_source(tmp_path / "source.png", source)
    _write_regions(
        tmp_path / "plan.json",
        [{"x": 85, "y": 85, "width": 30, "height": 30}],
    )

    generate_alpha_hole(
        tmp_path / "source.png",
        tmp_path / "plan.json",
        tmp_path / "out",
        padding=10,
    )

    rgba = _load_rgba(tmp_path / "out" / "alpha-hole.png")
    expected = np.full((100, 100), 255, dtype=np.uint8)
    # x1 = 85 - 10 = 75, y1 = 85 - 10 = 75; x2/y2 = 125 clamp to 100.
    expected[75:100, 75:100] = 0
    assert np.array_equal(rgba[:, :, 3], expected)


def test_overlapping_bboxes_union_transparent(tmp_path: Path) -> None:
    """Test 4: overlapping bboxes produce one transparent union region."""

    source = _gradient_bgr(100, 100)
    _write_source(tmp_path / "source.png", source)
    _write_regions(
        tmp_path / "plan.json",
        [
            {"x": 10, "y": 10, "width": 20, "height": 20},
            {"x": 20, "y": 20, "width": 20, "height": 20},
        ],
    )

    generate_alpha_hole(
        tmp_path / "source.png",
        tmp_path / "plan.json",
        tmp_path / "out",
        padding=0,
    )

    rgba = _load_rgba(tmp_path / "out" / "alpha-hole.png")
    alpha = rgba[:, :, 3]
    expected = np.full((100, 100), 255, dtype=np.uint8)
    expected[10:30, 10:30] = 0
    expected[20:40, 20:40] = 0
    assert np.array_equal(alpha, expected)
    assert alpha[25, 25] == 0  # overlap center
    assert alpha[15, 25] == 0  # first box only
    assert alpha[35, 35] == 0  # second box only
    assert alpha[5, 5] == 255  # outside the union


def test_empty_regions_full_opaque(tmp_path: Path) -> None:
    """Test 5: regions=[] yields a fully opaque image and region_count = 0."""

    source = _gradient_bgr(100, 100)
    _write_source(tmp_path / "source.png", source)
    _write_regions(tmp_path / "plan.json", [])

    diagnostics = generate_alpha_hole(
        tmp_path / "source.png",
        tmp_path / "plan.json",
        tmp_path / "out",
        padding=8,
    )

    assert diagnostics.region_count == 0
    assert diagnostics.applied_count == 0
    assert diagnostics.skipped_count == 0
    rgba = _load_rgba(tmp_path / "out" / "alpha-hole.png")
    assert np.all(rgba[:, :, 3] == 255)
    assert np.array_equal(_load_gray(tmp_path / "out" / "alpha-mask.png"), rgba[:, :, 3])


def test_rgb_channels_unchanged_inside_hole(tmp_path: Path) -> None:
    """Test 6: RGB stays byte-identical to the source, even where alpha = 0."""

    source = _gradient_bgr(100, 100)
    _write_source(tmp_path / "source.png", source)
    _write_regions(
        tmp_path / "plan.json",
        [{"x": 20, "y": 30, "width": 40, "height": 20}],
    )

    generate_alpha_hole(
        tmp_path / "source.png",
        tmp_path / "plan.json",
        tmp_path / "out",
        padding=0,
    )

    rgba = _load_rgba(tmp_path / "out" / "alpha-hole.png")
    assert np.array_equal(rgba[:, :, :3], source)
    assert rgba[35, 25, 3] == 0  # inside the hole
    assert np.array_equal(rgba[35, 25, :3], source[35, 25])
