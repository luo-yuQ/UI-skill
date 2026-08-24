from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from pydantic import ValidationError


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import ui_text_extractor as extractor_module  # noqa: E402
from ui_text_extractor import UITextExtractor  # noqa: E402
from ui_text_models import (  # noqa: E402
    Rect,
    TextExtractionResult,
    TextStyle,
)


def _write_rgb(path: Path, image_rgb: np.ndarray) -> None:
    image_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    success, encoded = cv2.imencode(".png", image_bgr)
    assert success
    encoded.tofile(str(path))


def _read_gray(path: Path) -> np.ndarray:
    encoded = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE)
    assert image is not None
    return image


def _synthetic_source(path: Path) -> None:
    image = np.full((120, 180, 3), [24, 32, 40], dtype=np.uint8)
    image[30:50, 20:28] = [244, 212, 20]
    image[30:50, 36:44] = [244, 212, 20]
    _write_rgb(path, image)


def test_filter_rejects_low_letter_and_vertical_decoration() -> None:
    assert not UITextExtractor._passes_filter("A", 0.84, 20, 20)
    assert UITextExtractor._passes_filter("A", 0.85, 20, 20)
    assert not UITextExtractor._passes_filter("装饰", 0.84, 5, 20)
    assert UITextExtractor._passes_filter("装饰", 0.85, 5, 20)
    assert not UITextExtractor._passes_filter("按钮", 0.34, 40, 20)


def test_edge_median_background_and_otsu_glyph_separation() -> None:
    crop = np.full((24, 40, 3), [18, 34, 50], dtype=np.uint8)
    crop[8:16, 10:30] = [210, 100, 70]
    allowed = np.full((24, 40), 255, dtype=np.uint8)

    background = UITextExtractor._estimate_background(crop, allowed)
    mask, mode = UITextExtractor._extract_glyph_mask(
        crop,
        background,
        allowed,
    )

    np.testing.assert_array_equal(background, np.array([18, 34, 50]))
    assert mode == "estimated_glyphs"
    assert np.all(mask[9:15, 11:29] == 255)
    assert np.all(mask[:3] == 0)


def test_color_quantization_and_rec709_stroke_inference() -> None:
    crop = np.full((40, 40, 3), [20, 24, 28], dtype=np.uint8)
    crop[10:30, 10:30] = [250, 210, 20]
    glyph_mask = np.zeros((40, 40), dtype=np.uint8)
    glyph_mask[10:30, 10:30] = 255

    style = UITextExtractor._estimate_typography(
        "Price",
        40,
        glyph_mask,
        crop,
        np.array([20, 24, 28], dtype=np.float32),
    )

    assert style.color == "#F0D010"
    assert style.fontFamily == "Arial"
    assert style.fontSize == 33
    assert style.fontWeight == 700
    assert style.strokeColor == "#1e2322"
    assert style.strokeWidth == 2

    dark_crop = np.full((20, 20, 3), 240, dtype=np.uint8)
    dark_crop[5:15, 5:15] = [10, 20, 30]
    dark_mask = np.zeros((20, 20), dtype=np.uint8)
    dark_mask[5:15, 5:15] = 255
    dark_style = UITextExtractor._estimate_typography(
        "12",
        20,
        dark_mask,
        dark_crop,
        np.array([240, 240, 240], dtype=np.float32),
    )
    assert dark_style.color == "#101010"
    assert dark_style.strokeColor == "#f0f4f1"


def test_mask_mode_switches_between_estimated_and_coarse() -> None:
    background = np.array([30, 30, 30], dtype=np.float32)
    separated = np.full((20, 40, 3), 30, dtype=np.uint8)
    separated[6:14, 10:30] = 220
    estimated, estimated_mode = UITextExtractor._extract_glyph_mask(
        separated,
        background,
    )

    uniform = np.full((20, 40, 3), 30, dtype=np.uint8)
    coarse, coarse_mode = UITextExtractor._extract_glyph_mask(
        uniform,
        background,
    )

    assert estimated_mode == "estimated_glyphs"
    assert np.count_nonzero(estimated) < estimated.size
    assert coarse_mode == "coarse"
    assert np.all(coarse == 255)


def test_single_chinese_uses_larger_elliptical_dilation() -> None:
    rect = Rect(x=20, y=20, width=20, height=40)
    glyph = np.zeros((40, 20), dtype=np.uint8)
    glyph[20, 10] = 255

    chinese = UITextExtractor._build_dilated_full_mask(
        glyph,
        rect,
        "中",
        (80, 80),
    )
    latin = UITextExtractor._build_dilated_full_mask(
        glyph,
        rect,
        "AB",
        (80, 80),
    )

    assert np.count_nonzero(chinese) > np.count_nonzero(latin)
    assert np.all(latin[chinese == 0] == 0)


def test_extract_serializes_contract_and_full_size_mask(tmp_path: Path) -> None:
    source = tmp_path / "界面.png"
    output_json = tmp_path / "artifacts" / "texts.json"
    output_mask = tmp_path / "artifacts" / "raw_text_mask.png"
    output_debug = tmp_path / "artifacts" / "debug.png"
    _synthetic_source(source)
    ocr_rows = [
        [[[10, 20], [70, 20], [70, 60], [10, 60]], "开始", 0.96],
        [[[80, 20], [100, 20], [100, 40], [80, 40]], "X", 0.80],
        [[[110, 10], [115, 10], [115, 50], [110, 50]], "装饰", 0.60],
    ]
    extractor = UITextExtractor(ocr_engine=lambda _image: (ocr_rows, [0.01]))

    result = extractor.extract(
        source,
        output_json,
        output_mask,
        output_debug,
    )

    assert isinstance(result, TextExtractionResult)
    assert result.image_width == 180
    assert result.image_height == 120
    assert result.count == 1
    assert result.items[0].id == "text_000"
    assert result.items[0].text == "开始"
    assert result.items[0].style.fontFamily == "Microsoft YaHei"
    assert result.items[0].style.strokeColor == "#1e2322"
    assert result.items[0].mask_mode == "estimated_glyphs"

    serialized = json.loads(output_json.read_text(encoding="utf-8"))
    assert serialized == result.model_dump(mode="json")
    assert serialized["count"] == len(serialized["items"])
    mask = _read_gray(output_mask)
    assert mask.shape == (120, 180)
    assert set(np.unique(mask)).issubset({0, 255})
    assert np.count_nonzero(mask) > 0
    assert output_debug.is_file()


def test_empty_ocr_writes_empty_result_and_zero_mask(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    _synthetic_source(source)
    extractor = UITextExtractor(ocr_engine=lambda _image: (None, None))

    result = extractor.extract(
        source,
        tmp_path / "texts.json",
        tmp_path / "mask.png",
    )

    assert result.count == 0
    assert result.items == []
    assert np.count_nonzero(_read_gray(tmp_path / "mask.png")) == 0


def test_pydantic_contract_rejects_invalid_style_and_count() -> None:
    with pytest.raises(ValidationError):
        TextStyle(
            color="white",
            fontFamily="Arial",
            fontSize=7,
            fontWeight=500,
            strokeColor="#000000",
            strokeWidth=3,
        )
    with pytest.raises(ValidationError, match="count"):
        TextExtractionResult(
            image_width=100,
            image_height=100,
            count=1,
            items=[],
        )


def test_cli_uses_required_stage_a_paths(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[Path, Path, Path, Path | None]] = []

    class FakeExtractor:
        def extract(
            self,
            image: Path,
            output_json: Path,
            output_mask: Path,
            output_debug: Path | None,
        ) -> TextExtractionResult:
            calls.append((image, output_json, output_mask, output_debug))
            return TextExtractionResult(
                image_width=1,
                image_height=1,
                count=0,
                items=[],
            )

    monkeypatch.setattr(extractor_module, "UITextExtractor", FakeExtractor)
    image = tmp_path / "source.png"
    image.write_bytes(b"placeholder")
    output_json = tmp_path / "texts.json"
    output_mask = tmp_path / "raw_text_mask.png"
    output_debug = tmp_path / "debug.png"

    code = extractor_module.main(
        [
            "--image",
            str(image),
            "--output-json",
            str(output_json),
            "--output-mask",
            str(output_mask),
            "--output-debug",
            str(output_debug),
        ]
    )

    assert code == 0
    assert calls == [(image, output_json, output_mask, output_debug)]


def test_cli_batch_restores_positional_directory_and_output_dir(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    calls: list[tuple[Path, Path, Path, Path | None]] = []

    class FakeExtractor:
        def extract(
            self,
            image: Path,
            output_json: Path,
            output_mask: Path,
            output_debug: Path | None,
        ) -> TextExtractionResult:
            calls.append((image, output_json, output_mask, output_debug))
            return TextExtractionResult(
                image_width=1,
                image_height=1,
                count=0,
                items=[],
            )

    monkeypatch.setattr(extractor_module, "UITextExtractor", FakeExtractor)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    first = inputs / "a.png"
    second = inputs / "b.JPG"
    first.write_bytes(b"placeholder")
    second.write_bytes(b"placeholder")
    (inputs / "ignore.txt").write_text("ignored", encoding="utf-8")
    outputs = tmp_path / "outputs"

    code = extractor_module.main(
        [str(inputs), "--output-dir", str(outputs)]
    )

    assert code == 0
    assert calls == [
        (
            first,
            outputs / "a_texts.json",
            outputs / "a_raw_text_mask.png",
            outputs / "a_debug.png",
        ),
        (
            second,
            outputs / "b_texts.json",
            outputs / "b_raw_text_mask.png",
            outputs / "b_debug.png",
        ),
    ]
    assert "Summary: 2 succeeded, 0 failed, 2 total" in capsys.readouterr().out
