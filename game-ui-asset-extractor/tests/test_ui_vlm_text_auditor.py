from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from ui_audit_models import (  # noqa: E402
    StrippedSymbol,
    TextAuditResult,
    TextCorrection,
    TextItem,
    TextStyle,
)
from ui_vlm_text_auditor import (  # noqa: E402
    SYSTEM_PROMPT,
    TextAuditClientError,
    TextAuditResponseError,
    UIRasterTextProcessor,
    UIVLMTextAuditor,
)


IMAGE_WIDTH = 160
IMAGE_HEIGHT = 120


def _style(font_family: str = "Arial") -> dict[str, Any]:
    return {
        "fontFamily": font_family,
        "fontSize": 18,
        "color": "#FFFFFF",
        "fontWeight": 700,
        "strokeColor": "#1e2322",
        "strokeWidth": 1,
    }


def _stage_a_items() -> list[dict[str, Any]]:
    return [
        {
            "id": "text_000",
            "text": "英雄",
            "confidence": 0.99,
            "rect": {"x": 10, "y": 10, "width": 30, "height": 20},
            "style": _style("Microsoft YaHei"),
            "mask_mode": "estimated_glyphs",
        },
        {
            "id": "text_001",
            "text": "$99.99",
            "confidence": 0.98,
            "rect": {"x": 60, "y": 10, "width": 40, "height": 20},
            "style": _style(),
            "mask_mode": "estimated_glyphs",
        },
        {
            "id": "text_002",
            "text": "176+",
            "confidence": 0.97,
            "rect": {"x": 20, "y": 50, "width": 80, "height": 20},
            "style": _style(),
            "mask_mode": "estimated_glyphs",
        },
    ]


def _text_items() -> list[TextItem]:
    return [TextItem.model_validate(item) for item in _stage_a_items()]


def _audit_payload(*, include_corrections: bool = True) -> dict[str, Any]:
    corrections = []
    if include_corrections:
        corrections = [
            {
                "text": "7",
                "bbox_norm": [110 / IMAGE_WIDTH, 80 / IMAGE_HEIGHT,
                              118 / IMAGE_WIDTH, 92 / IMAGE_HEIGHT],
                "confidence": 0.95,
                "estimated_role": "slot_count",
            },
            {
                "text": "1",
                "bbox_norm": [130 / IMAGE_WIDTH, 80 / IMAGE_HEIGHT,
                               136 / IMAGE_WIDTH, 92 / IMAGE_HEIGHT],
                "confidence": 0.95,
                "estimated_role": "slot_count",
            },
            {
                "text": "2",
                "bbox_norm": [145 / IMAGE_WIDTH, 80 / IMAGE_HEIGHT,
                               153 / IMAGE_WIDTH, 92 / IMAGE_HEIGHT],
                "confidence": 0.95,
                "estimated_role": "slot_count",
            },
        ]
    return {
        "scene_summary": "Inventory UI with text embedded in a chest.",
        "raster_text_ids": ["text_000"],
        "editable_texts": [
            {"id": "text_001", "text": "$99.99", "role": "button_label"},
            {"id": "text_002", "text": "176", "role": "value"},
        ],
        "stripped_symbols": [
            {
                "source_text_id": "text_002",
                "symbol": "+",
                "role": "button",
                "estimated_bbox_norm": None,
            }
        ],
        "text_corrections": corrections,
    }


class FakeVLMClient:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = _audit_payload() if response is None else response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def infer_json(
        self,
        *,
        image_path: Path,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
    ) -> Any:
        with Image.open(image_path) as prepared:
            image_size = prepared.size
            image_format = prepared.format
        self.calls.append(
            {
                "image_size": image_size,
                "image_format": image_format,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "response_schema": response_schema,
            }
        )
        if self.error is not None:
            raise self.error
        return self.response


def _make_arrays() -> tuple[np.ndarray, np.ndarray]:
    image = np.full(
        (IMAGE_HEIGHT, IMAGE_WIDTH, 3),
        (70, 110, 150),
        dtype=np.uint8,
    )
    raw_mask = np.zeros((IMAGE_HEIGHT, IMAGE_WIDTH), dtype=np.uint8)

    image[12:28, 12:38] = (230, 30, 30)
    raw_mask[12:28, 12:38] = 255

    image[12:28, 62:98] = (245, 245, 245)
    raw_mask[12:28, 62:98] = 255

    image[53:67, 24:65] = (245, 245, 245)
    raw_mask[53:67, 24:65] = 255

    image[59:62, 80:94] = (245, 245, 245)
    image[54:68, 85:89] = (245, 245, 245)
    raw_mask[59:62, 80:94] = 255
    raw_mask[54:68, 85:89] = 255

    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(image, "7", (110, 91), font, 0.35, (18, 18, 18), 3, cv2.LINE_AA)
    cv2.putText(image, "7", (110, 91), font, 0.35, (245, 245, 245), 1,
                cv2.LINE_AA)
    cv2.putText(image, "1", (130, 91), font, 0.35, (18, 18, 18), 3, cv2.LINE_AA)
    cv2.putText(image, "1", (130, 91), font, 0.35, (245, 245, 245), 1,
                cv2.LINE_AA)
    cv2.putText(image, "2", (145, 91), font, 0.35, (18, 18, 18), 3, cv2.LINE_AA)
    cv2.putText(image, "2", (145, 91), font, 0.35, (245, 245, 245), 1,
                cv2.LINE_AA)
    return image, raw_mask


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    image, raw_mask = _make_arrays()
    image_path = tmp_path / "source.png"
    success, encoded = cv2.imencode(".png", cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    assert success
    encoded.tofile(image_path)

    texts_path = tmp_path / "texts.json"
    items = _stage_a_items()
    texts_path.write_text(
        json.dumps(
            {
                "image_width": IMAGE_WIDTH,
                "image_height": IMAGE_HEIGHT,
                "count": len(items),
                "items": items,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    mask_path = tmp_path / "raw_text_mask.png"
    success, encoded = cv2.imencode(".png", raw_mask)
    assert success
    encoded.tofile(mask_path)
    return image_path, texts_path, mask_path


def test_data_contract_rejects_invalid_normalized_bbox() -> None:
    with pytest.raises(ValidationError, match="x0 < x1"):
        TextCorrection(text="7", bbox_norm=[0.5, 0.2, 0.4, 0.3])


def test_data_contract_keeps_general_typography_and_declared_defaults() -> None:
    style = TextStyle(
        fontFamily="Noto Sans CJK SC",
        fontSize=21,
        color="rgba(255, 255, 255, 0.9)",
        fontWeight=500,
        strokeColor="transparent",
        strokeWidth=3,
    )
    correction = TextCorrection(text="4", bbox_norm=[0.1, 0.2, 0.2, 0.3])
    symbol = StrippedSymbol(source_text_id="text_001", symbol="×")

    assert style.fontFamily == "Noto Sans CJK SC"
    assert correction.confidence == 0.95
    assert correction.estimated_role == "slot_count"
    assert symbol.role == "button"
    assert symbol.estimated_bbox_norm is None


def test_audit_uses_compact_prompt_and_validates_result(tmp_path: Path) -> None:
    image_path, texts_path, _ = _write_inputs(tmp_path)
    client = FakeVLMClient()

    result = UIVLMTextAuditor(client=client).audit(image_path, texts_path)

    assert isinstance(result, TextAuditResult)
    assert [item.text for item in result.text_corrections] == ["7", "1", "2"]
    call = client.calls[0]
    assert call["image_size"] == (1024, 768)
    assert call["image_format"] == "PNG"
    assert "空间依附载体 + 艺术特征" in SYSTEM_PROMPT
    assert "条件 A (道具与箱体贴图内嵌字)" in SYSTEM_PROMPT
    assert "自左向右、自上而下" in SYSTEM_PROMPT
    assert "英雄" in call["user_prompt"]
    assert "VLM 分析图尺寸：1024x768" in call["user_prompt"]
    assert '[text_000, "英雄", (64,64,192,128)]' in call["user_prompt"]
    assert "BBox 已换算为分析图像素坐标" in call["user_prompt"]
    assert "bbox_norm 必须使用原图归一化坐标" in call["user_prompt"]
    assert "fontFamily" not in call["user_prompt"].split("Required JSON Schema:")[0]
    assert "text_corrections" in call["response_schema"]["required"]
    correction_schema = call["response_schema"]["$defs"]["TextCorrection"]
    assert set(correction_schema["required"]) == {
        "text", "bbox_norm", "confidence", "estimated_role"
    }


def test_raster_mask_is_removed_and_original_pixels_are_preserved() -> None:
    image, raw_mask = _make_arrays()
    result = TextAuditResult.model_validate(_audit_payload(include_corrections=False))

    cleaned, final_mask, _ = UIVLMTextAuditor().filter_mask_and_inpaint(
        image, raw_mask, _text_items(), result
    )

    assert np.all(final_mask[10:31, 10:41] == 0)
    assert np.array_equal(cleaned[12:28, 12:38], image[12:28, 12:38])
    assert not np.array_equal(cleaned[12:28, 62:98], image[12:28, 62:98])
    assert final_mask[11, 62] == 255


def test_single_digit_corrections_extend_mask_and_unified_metadata() -> None:
    image, raw_mask = _make_arrays()
    result = TextAuditResult.model_validate(_audit_payload())

    cleaned, final_mask, unified = UIVLMTextAuditor().filter_mask_and_inpaint(
        image, raw_mask, _text_items(), result
    )

    background = np.array([70, 110, 150], dtype=np.uint8)
    first_roi = image[77:96, 107:122]
    first_mask = final_mask[77:96, 107:122]
    first_digit_pixels = np.any(first_roi != background, axis=2)
    assert np.all(first_mask[first_digit_pixels] == 255)
    assert np.count_nonzero(first_mask) < first_mask.size

    second_roi = image[77:96, 127:140]
    second_mask = final_mask[77:96, 127:140]
    second_digit_pixels = np.any(second_roi != background, axis=2)
    assert np.all(second_mask[second_digit_pixels] == 255)
    assert np.count_nonzero(second_mask) < second_mask.size

    third_roi = image[77:96, 142:157]
    third_mask = final_mask[77:96, 142:157]
    third_digit_pixels = np.any(third_roi != background, axis=2)
    assert np.all(third_mask[third_digit_pixels] == 255)
    assert np.count_nonzero(third_mask) < third_mask.size

    original_error = np.abs(first_roi.astype(int) - background).sum()
    cleaned_error = np.abs(
        cleaned[77:96, 107:122].astype(int) - background
    ).sum()
    assert cleaned_error < 0.10 * original_error
    assert [item.id for item in unified] == [
        "text_001",
        "text_002",
        "text_corr_001",
        "text_corr_002",
        "text_corr_003",
    ]
    assert [item.text for item in unified[-3:]] == ["7", "1", "2"]
    assert unified[-1].style.fontFamily == "Microsoft YaHei"
    assert unified[-1].style.fontSize >= 8


def test_stripped_plus_connected_component_is_protected() -> None:
    image, raw_mask = _make_arrays()
    result = TextAuditResult.model_validate(_audit_payload(include_corrections=False))

    _, final_mask, _ = UIVLMTextAuditor().filter_mask_and_inpaint(
        image, raw_mask, _text_items(), result
    )

    assert np.all(final_mask[59:62, 80:94] == 0)
    assert np.all(final_mask[54:68, 85:89] == 0)
    assert np.all(final_mask[53:67, 24:65] == 255)


def test_stale_coarse_long_text_mask_is_rebuilt_from_source_pixels() -> None:
    height, width = 70, 240
    horizontal = np.linspace(0, 30, width, dtype=np.uint8)
    clean_background = np.empty((height, width, 3), dtype=np.uint8)
    clean_background[:, :, 0] = 70 + horizontal
    clean_background[:, :, 1] = 95 + horizontal // 2
    clean_background[:, :, 2] = 130
    image = clean_background.copy()
    cv2.putText(
        image,
        "VIEW POOL",
        (28, 43),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (245, 235, 180),
        2,
        cv2.LINE_AA,
    )
    raw_mask = np.zeros((height, width), dtype=np.uint8)
    raw_mask[12:58, 14:226] = 255
    source = TextItem.model_validate(
        {
            "id": "text_100",
            "text": "VIEW POOL",
            "confidence": 0.99,
            "rect": {"x": 20, "y": 18, "width": 200, "height": 34},
            "style": _style(),
            "mask_mode": "coarse",
        }
    )
    result = TextAuditResult(
        scene_summary="Textured button",
        editable_texts=[
            {"id": "text_100", "text": "VIEW POOL", "role": "button_label"}
        ],
    )

    cleaned, final_mask, _ = UIVLMTextAuditor().filter_mask_and_inpaint(
        image,
        raw_mask,
        [source],
        result,
    )

    mask_roi = final_mask[12:58, 14:226]
    assert 0 < np.count_nonzero(mask_roi) < 0.50 * mask_roi.size
    assert not np.any(np.all(mask_roi == 255, axis=1))
    original_error = np.abs(image.astype(int) - clean_background).sum()
    cleaned_error = np.abs(cleaned.astype(int) - clean_background).sum()
    assert cleaned_error < original_error


def test_process_exports_four_artifacts_and_correction_items(tmp_path: Path) -> None:
    image_path, texts_path, mask_path = _write_inputs(tmp_path)
    output_dir = tmp_path / "stage_b"

    result = UIRasterTextProcessor(client=FakeVLMClient()).process(
        image_path, texts_path, mask_path, output_dir
    )

    assert len(result.text_corrections) == 3
    assert {path.name for path in output_dir.iterdir()} == {
        "audit_result.json",
        "final_inpaint_mask.png",
        "cleaned_image.png",
        "filtered_texts.json",
    }
    filtered = json.loads((output_dir / "filtered_texts.json").read_text("utf-8"))
    assert filtered["count"] == 5
    assert [item["id"] for item in filtered["items"]][-3:] == [
        "text_corr_001", "text_corr_002", "text_corr_003"
    ]
    assert [item["text"] for item in filtered["items"][-3:]] == ["7", "1", "2"]
    final_mask = cv2.imread(
        str(output_dir / "final_inpaint_mask.png"), cv2.IMREAD_GRAYSCALE
    )
    assert final_mask is not None
    assert 0 < np.count_nonzero(final_mask[77:96, 107:122]) < 19 * 15


def test_empty_ocr_and_empty_corrections_are_safe() -> None:
    image = np.full((20, 30, 3), 80, dtype=np.uint8)
    raw_mask = np.zeros((20, 30), dtype=np.uint8)
    result = TextAuditResult(scene_summary="Empty UI")

    cleaned, final_mask, unified = UIVLMTextAuditor().filter_mask_and_inpaint(
        image, raw_mask, [], result
    )

    assert np.array_equal(cleaned, image)
    assert np.array_equal(final_mask, raw_mask)
    assert unified == []


def test_correction_padding_clamps_safely_at_image_edge() -> None:
    image = np.full((20, 30, 3), 80, dtype=np.uint8)
    raw_mask = np.zeros((20, 30), dtype=np.uint8)
    result = TextAuditResult(
        scene_summary="Edge digit",
        text_corrections=[
            TextCorrection(text="1", bbox_norm=[0.98, 0.90, 1.0, 1.0])
        ],
    )

    _, final_mask, unified = UIVLMTextAuditor().filter_mask_and_inpaint(
        image, raw_mask, [], result
    )

    assert np.all(final_mask[18:20, 29:30] == 255)
    assert 0 < np.count_nonzero(final_mask[15:20, 26:30]) < 20
    assert unified[0].rect.model_dump() == {
        "x": 29,
        "y": 18,
        "width": 1,
        "height": 2,
    }


def test_invalid_json_and_network_failures_are_wrapped(tmp_path: Path) -> None:
    image_path, texts_path, _ = _write_inputs(tmp_path)

    with pytest.raises(TextAuditResponseError, match="Invalid VLM audit response"):
        UIVLMTextAuditor(client=FakeVLMClient(response="not-json")).audit(
            image_path, texts_path
        )

    with pytest.raises(TextAuditClientError, match="relay unavailable"):
        UIVLMTextAuditor(
            client=FakeVLMClient(error=ConnectionError("relay unavailable"))
        ).audit(image_path, texts_path)
