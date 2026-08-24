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

from ui_audit_models import TextAuditResult  # noqa: E402
from ui_vlm_text_auditor import (  # noqa: E402
    TextAuditClientError,
    TextAuditResponseError,
    UIRasterTextProcessor,
)


def _stage_a_items() -> list[dict[str, Any]]:
    return [
        {
            "id": "text_000",
            "text": "HERO",
            "confidence": 0.99,
            "rect": {"x": 10, "y": 15, "width": 50, "height": 25},
            "style": {"fontFamily": "Arial", "fontSize": 20},
            "mask_mode": "estimated_glyphs",
        },
        {
            "id": "text_001",
            "text": "$99.99",
            "confidence": 0.98,
            "rect": {"x": 90, "y": 15, "width": 60, "height": 25},
            "style": {"fontFamily": "Arial", "fontSize": 20},
            "mask_mode": "estimated_glyphs",
        },
        {
            "id": "text_002",
            "text": "176+",
            "confidence": 0.97,
            "rect": {"x": 90, "y": 60, "width": 70, "height": 25},
            "style": {"fontFamily": "Arial", "fontSize": 20},
            "mask_mode": "estimated_glyphs",
        },
    ]


def _audit_payload() -> dict[str, Any]:
    return {
        "scene_summary": "Store UI with a team logo, price, and currency value.",
        "raster_text_ids": ["text_000"],
        "editable_texts": [
            {"id": "text_001", "text": "$99.99", "role": "button_label"},
            {"id": "text_002", "text": "176", "role": "value"},
        ],
        "stripped_symbols": [
            {"source_text_id": "text_002", "symbol": "+", "role": "button"}
        ],
    }


class FakeVLMClient:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = _audit_payload() if response is None else response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def infer_json(
        self,
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


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    image_path = tmp_path / "source.png"
    image_rgb = np.full((100, 200, 3), (70, 110, 150), dtype=np.uint8)
    image_rgb[20:35, 20:50] = (230, 30, 30)
    image_rgb[20:35, 100:140] = (245, 245, 245)
    image_rgb[65:80, 100:130] = (245, 245, 245)
    image_rgb[65:80, 150:158] = (245, 245, 245)
    Image.fromarray(image_rgb, mode="RGB").save(image_path)

    texts_json = tmp_path / "texts.json"
    items = _stage_a_items()
    texts_json.write_text(
        json.dumps(
            {
                "image_width": 200,
                "image_height": 100,
                "count": len(items),
                "items": items,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    raw_mask = np.zeros((100, 200), dtype=np.uint8)
    raw_mask[20:35, 20:50] = 255
    raw_mask[20:35, 100:140] = 255
    raw_mask[65:80, 100:130] = 255
    raw_mask[65:80, 150:158] = 255
    raw_mask_path = tmp_path / "raw_text_mask.png"
    assert cv2.imwrite(str(raw_mask_path), raw_mask)
    return image_path, texts_json, raw_mask_path


def test_audit_parses_pydantic_and_classifies_raster_and_editable(
    tmp_path: Path,
) -> None:
    image_path, texts_json, _ = _write_inputs(tmp_path)
    client = FakeVLMClient()

    result = UIRasterTextProcessor(client=client).audit(image_path, texts_json)

    assert isinstance(result, TextAuditResult)
    assert result.raster_text_ids == ["text_000"]
    editable = {item.id: item.text for item in result.editable_texts}
    assert editable == {"text_001": "$99.99", "text_002": "176"}
    assert result.stripped_symbols[0].symbol == "+"
    assert client.calls[0]["image_size"] == (1024, 512)
    assert client.calls[0]["image_format"] == "PNG"
    assert "HERO" in client.calls[0]["system_prompt"]
    assert '"text": "$99.99"' in client.calls[0]["user_prompt"]
    symbol_schema = client.calls[0]["response_schema"]["$defs"]["StrippedSymbol"]
    assert "role" in symbol_schema["required"]
    assert "default" not in symbol_schema["properties"]["role"]


def test_mask_difference_protects_raster_region(tmp_path: Path) -> None:
    image_path, texts_json, raw_mask_path = _write_inputs(tmp_path)
    image = np.asarray(Image.open(image_path).convert("RGB"))
    raw_mask = cv2.imread(str(raw_mask_path), cv2.IMREAD_GRAYSCALE)
    result = TextAuditResult.model_validate(_audit_payload())

    _, final_mask = UIRasterTextProcessor().filter_mask_and_inpaint(
        image, raw_mask, texts_json, result
    )

    assert np.all(final_mask[15:40, 10:60] == 0)
    assert np.all(final_mask[20:35, 100:140] == 255)


def test_symbol_component_is_removed_but_numeric_components_remain(
    tmp_path: Path,
) -> None:
    image_path, texts_json, raw_mask_path = _write_inputs(tmp_path)
    image = np.asarray(Image.open(image_path).convert("RGB"))
    raw_mask = cv2.imread(str(raw_mask_path), cv2.IMREAD_GRAYSCALE)
    result = TextAuditResult.model_validate(_audit_payload())

    _, final_mask = UIRasterTextProcessor().filter_mask_and_inpaint(
        image, raw_mask, texts_json, result
    )

    assert np.all(final_mask[65:80, 150:158] == 0)
    assert np.all(final_mask[65:80, 100:130] == 255)


def test_inpaint_preserves_raster_pixels_and_fills_editable_text(
    tmp_path: Path,
) -> None:
    image_path, texts_json, raw_mask_path = _write_inputs(tmp_path)
    image = np.asarray(Image.open(image_path).convert("RGB"))
    raw_mask = cv2.imread(str(raw_mask_path), cv2.IMREAD_GRAYSCALE)
    result = TextAuditResult.model_validate(_audit_payload())

    cleaned, _ = UIRasterTextProcessor().filter_mask_and_inpaint(
        image, raw_mask, texts_json, result
    )

    assert cleaned.shape == image.shape
    assert np.array_equal(cleaned[20:35, 20:50], image[20:35, 20:50])
    assert not np.array_equal(cleaned[20:35, 100:140], image[20:35, 100:140])
    background = np.array([70, 110, 150])
    original_error = np.abs(image[27, 120].astype(int) - background).sum()
    cleaned_error = np.abs(cleaned[27, 120].astype(int) - background).sum()
    assert cleaned_error < original_error


def test_process_exports_all_artifacts_and_filtered_texts(tmp_path: Path) -> None:
    image_path, texts_json, raw_mask_path = _write_inputs(tmp_path)
    output_dir = tmp_path / "stage_b"

    result = UIRasterTextProcessor(client=FakeVLMClient()).process(
        image_path, texts_json, raw_mask_path, output_dir
    )

    assert result.raster_text_ids == ["text_000"]
    expected = {
        "audit_result.json",
        "final_inpaint_mask.png",
        "cleaned_image.png",
        "filtered_texts.json",
    }
    assert {path.name for path in output_dir.iterdir()} == expected
    filtered = json.loads((output_dir / "filtered_texts.json").read_text("utf-8"))
    assert filtered["count"] == 2
    assert [item["id"] for item in filtered["items"]] == ["text_001", "text_002"]
    assert filtered["items"][1]["text"] == "176"


def test_invalid_json_and_network_failures_are_wrapped(tmp_path: Path) -> None:
    image_path, texts_json, _ = _write_inputs(tmp_path)

    with pytest.raises(TextAuditResponseError, match="Invalid VLM audit response"):
        UIRasterTextProcessor(client=FakeVLMClient(response="not-json")).audit(
            image_path, texts_json
        )

    with pytest.raises(TextAuditClientError, match="relay unavailable"):
        UIRasterTextProcessor(
            client=FakeVLMClient(error=ConnectionError("relay unavailable"))
        ).audit(image_path, texts_json)
