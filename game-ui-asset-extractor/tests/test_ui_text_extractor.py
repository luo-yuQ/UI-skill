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


@pytest.mark.parametrize("digit", ["1", "7", "2"])
def test_filter_keeps_single_digit_at_medium_confidence(digit: str) -> None:
    assert UITextExtractor._passes_filter(digit, 0.65, 5, 20)


@pytest.mark.parametrize("letter", ["x", "o"])
def test_filter_rejects_single_letter_at_medium_confidence(letter: str) -> None:
    assert not UITextExtractor._passes_filter(letter, 0.65, 20, 20)


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
    assert np.count_nonzero(coarse) < coarse.size
    assert np.all(coarse[:4] == 0)
    assert np.all(coarse[16:] == 0)
    assert np.all(coarse[4:16, 1:39] == 255)


def test_relaxed_coverage_keeps_small_and_dense_otsu_glyph_masks() -> None:
    background = np.array([30, 30, 30], dtype=np.float32)

    small = np.full((100, 100, 3), 30, dtype=np.uint8)
    small[45:54, 45:54] = 220
    small_mask, small_mode = UITextExtractor._extract_glyph_mask(
        small,
        background,
    )

    dense = np.full((20, 100, 3), 30, dtype=np.uint8)
    dense[:, 10:89] = 220
    dense_mask, dense_mode = UITextExtractor._extract_glyph_mask(
        dense,
        background,
    )

    assert UITextExtractor.MIN_GLYPH_COVERAGE == 0.008
    assert UITextExtractor.MAX_GLYPH_COVERAGE == 0.80
    assert small_mode == "estimated_glyphs"
    assert np.count_nonzero(small_mask) == 81
    assert dense_mode == "estimated_glyphs"
    assert np.count_nonzero(dense_mask) == 1580


def test_coarse_long_text_fallback_is_inset_center_band() -> None:
    crop = np.full((20, 200, 3), 30, dtype=np.uint8)
    background = np.array([30, 30, 30], dtype=np.float32)

    mask, mode = UITextExtractor._extract_glyph_mask(crop, background)

    assert mode == "coarse"
    assert np.all(mask[:4] == 0)
    assert np.all(mask[16:] == 0)
    assert np.all(mask[4:16, :4] == 0)
    assert np.all(mask[4:16, 196:] == 0)
    assert np.all(mask[4:16, 4:196] == 255)
    assert np.count_nonzero(mask) / mask.size < 0.60


def test_coarse_fallback_recovers_glyph_detail_from_textured_button() -> None:
    height, width = 36, 220
    horizontal = np.linspace(0, 24, width, dtype=np.uint8)
    crop = np.empty((height, width, 3), dtype=np.uint8)
    crop[:, :, 0] = 142 + horizontal
    crop[:, :, 1] = 104 + horizontal // 2
    crop[:, :, 2] = 36
    cv2.rectangle(crop, (0, 0), (width - 1, height - 1), (245, 205, 90), 2)
    cv2.putText(
        crop,
        "VIEW POOL",
        (30, 25),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (250, 225, 130),
        2,
        cv2.LINE_AA,
    )
    allowed = np.ones((height, width), dtype=bool)

    mask = UITextExtractor._build_coarse_fallback_mask(crop, allowed)

    coverage = np.count_nonzero(mask) / mask.size
    assert UITextExtractor.MIN_GLYPH_COVERAGE <= coverage <= 0.50
    assert not np.any(np.all(mask == 255, axis=1))
    assert np.count_nonzero(mask[:2]) == 0
    assert np.count_nonzero(mask[-2:]) == 0


def test_long_text_uses_smaller_dilation_radius() -> None:
    assert UITextExtractor._dilation_radius("查看畅玩池", 40) == 2
    assert UITextExtractor._dilation_radius("OK", 40) == 4


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
    output_cleaned = tmp_path / "artifacts" / "cleaned.png"
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
        output_cleaned_path=output_cleaned,
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
    cleaned = cv2.imdecode(
        np.fromfile(str(output_cleaned), dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    assert cleaned is not None
    assert cleaned.shape == (120, 180, 3)
    assert output_debug.is_file()


def test_empty_ocr_writes_empty_result_and_zero_mask(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    _synthetic_source(source)
    extractor = UITextExtractor(ocr_engine=lambda _image: (None, None))

    result = extractor.extract(
        source,
        tmp_path / "texts.json",
        tmp_path / "mask.png",
        output_cleaned_path=tmp_path / "cleaned.png",
    )

    assert result.count == 0
    assert result.items == []
    assert np.count_nonzero(_read_gray(tmp_path / "mask.png")) == 0
    assert (tmp_path / "cleaned.png").is_file()


def test_telea_inpaint_removes_masked_text_color() -> None:
    image = np.full((60, 80, 3), [80, 120, 160], dtype=np.uint8)
    image[20:40, 35:45] = [250, 10, 10]
    mask = np.zeros((60, 80), dtype=np.uint8)
    mask[20:40, 35:45] = 255

    cleaned = UITextExtractor.inpaint_cleaned_image(image, mask)

    assert cleaned.shape == image.shape
    assert cleaned.dtype == np.uint8
    assert not np.array_equal(cleaned[mask > 0], image[mask > 0])
    repaired_mean = cleaned[mask > 0].mean(axis=0)
    np.testing.assert_allclose(repaired_mean, [80, 120, 160], atol=4)


def test_empty_mask_returns_independent_image_copy() -> None:
    image = np.full((12, 16, 3), 70, dtype=np.uint8)
    mask = np.zeros((12, 16), dtype=np.uint8)

    cleaned = UITextExtractor.inpaint_cleaned_image(image, mask)

    assert np.array_equal(cleaned, image)
    assert cleaned is not image
    assert not np.shares_memory(cleaned, image)


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
    calls: list[tuple[Path, Path, Path, Path | None, Path | None]] = []

    class FakeExtractor:
        def extract(
            self,
            image: Path,
            output_json: Path,
            output_mask: Path,
            output_debug: Path | None,
            output_cleaned_path: Path | None = None,
        ) -> TextExtractionResult:
            calls.append(
                (
                    image,
                    output_json,
                    output_mask,
                    output_debug,
                    output_cleaned_path,
                )
            )
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
    output_cleaned = tmp_path / "cleaned.png"
    output_debug = tmp_path / "debug.png"

    code = extractor_module.main(
        [
            "--image",
            str(image),
            "--output-json",
            str(output_json),
            "--output-mask",
            str(output_mask),
            "--output-cleaned",
            str(output_cleaned),
            "--output-debug",
            str(output_debug),
        ]
    )

    assert code == 0
    assert calls == [
        (image, output_json, output_mask, output_debug, output_cleaned)
    ]


def test_cli_batch_restores_positional_directory_and_output_dir(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    calls: list[tuple[Path, Path, Path, Path | None, Path | None]] = []

    class FakeExtractor:
        def extract(
            self,
            image: Path,
            output_json: Path,
            output_mask: Path,
            output_debug: Path | None,
            output_cleaned_path: Path | None = None,
        ) -> TextExtractionResult:
            calls.append(
                (
                    image,
                    output_json,
                    output_mask,
                    output_debug,
                    output_cleaned_path,
                )
            )
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
            outputs / "a_cleaned.png",
        ),
        (
            second,
            outputs / "b_texts.json",
            outputs / "b_raw_text_mask.png",
            outputs / "b_debug.png",
            outputs / "b_cleaned.png",
        ),
    ]
    assert "Summary: 2 succeeded, 0 failed, 2 total" in capsys.readouterr().out


def test_cli_output_cleaned_is_written_with_real_extractor(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    _synthetic_source(source)
    actual = UITextExtractor(ocr_engine=lambda _image: (None, None))
    monkeypatch.setattr(extractor_module, "UITextExtractor", lambda: actual)
    output_cleaned = tmp_path / "explicit-cleaned.png"

    code = extractor_module.main(
        [
            "--image",
            str(source),
            "--output-json",
            str(tmp_path / "texts.json"),
            "--output-mask",
            str(tmp_path / "mask.png"),
            "--output-cleaned",
            str(output_cleaned),
        ]
    )

    assert code == 0
    encoded = np.fromfile(str(output_cleaned), dtype=np.uint8)
    cleaned = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    assert cleaned is not None
    assert cleaned.shape == (120, 180, 3)
    source_bgr = cv2.imdecode(
        np.fromfile(str(source), dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    assert np.array_equal(cleaned, source_bgr)
