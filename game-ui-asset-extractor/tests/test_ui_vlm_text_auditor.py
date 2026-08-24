from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from pydantic import ValidationError


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from ui_audit_models import TextAuditResult, TextCorrection  # noqa: E402
from ui_vlm_text_auditor import (  # noqa: E402
    AuditClientError,
    AuditResponseError,
    UITextAuditor,
)


def _stage_a_candidates() -> list[dict[str, Any]]:
    return [
        {
            "id": "text_000",
            "text": "HERO",
            "confidence": 0.99,
            "rect": {"x": 10, "y": 10, "width": 80, "height": 30},
            "style": {"fontFamily": "Arial"},
        },
        {
            "id": "text_001",
            "text": "DYG",
            "confidence": 0.98,
            "rect": {"x": 100, "y": 10, "width": 70, "height": 30},
            "style": {"fontFamily": "Arial"},
        },
        {
            "id": "text_002",
            "text": "$99.99",
            "confidence": 0.97,
            "rect": {"x": 20, "y": 100, "width": 90, "height": 28},
            "style": {"fontFamily": "Arial"},
        },
        {
            "id": "text_003",
            "text": "176+",
            "confidence": 0.96,
            "rect": {"x": 20, "y": 150, "width": 75, "height": 26},
            "style": {"fontFamily": "Arial"},
        },
    ]


def _valid_audit_payload() -> dict[str, Any]:
    return {
        "scene_summary": "A store panel with team marks, a price, and currency.",
        "raster_text_ids": ["text_000", "text_001"],
        "editable_texts": [
            {"id": "text_002", "text": "$99.99", "role": "button_label"},
            {"id": "text_003", "text": "176", "role": "value"},
        ],
        "stripped_symbols": [
            {
                "source_text_id": "text_003",
                "symbol": "+",
                "role": "button",
                "estimated_bbox_norm": [0.42, 0.50, 0.02, 0.04],
            }
        ],
        "text_corrections": [
            {
                "text": "重置",
                "bbox_norm": [0.10, 0.80, 0.08, 0.04],
                "confidence": 0.88,
            }
        ],
    }


class FakeVLMClient:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = _valid_audit_payload() if response is None else response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def infer_json(
        self,
        image_path: Path,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
    ) -> Any:
        with Image.open(image_path) as analysis_image:
            analysis_size = analysis_image.size
            analysis_format = analysis_image.format
        self.calls.append(
            {
                "image_path": image_path,
                "analysis_size": analysis_size,
                "analysis_format": analysis_format,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "response_schema": response_schema,
            }
        )
        if self.error is not None:
            raise self.error
        return self.response


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    image = tmp_path / "source.png"
    Image.new("RGB", (2048, 1080), "navy").save(image)
    texts_json = tmp_path / "texts.json"
    candidates = _stage_a_candidates()
    texts_json.write_text(
        json.dumps(
            {
                "image_width": 2048,
                "image_height": 1080,
                "count": len(candidates),
                "items": candidates,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return image, texts_json


def test_normal_audit_parses_and_validates_pydantic_result(tmp_path: Path) -> None:
    image, texts_json = _write_inputs(tmp_path)
    client = FakeVLMClient()

    result = UITextAuditor(client=client).audit(image, texts_json)

    assert isinstance(result, TextAuditResult)
    assert result.scene_summary.startswith("A store panel")
    assert result.text_corrections[0].text == "重置"
    assert client.calls[0]["image_path"].name == "analysis-image.png"
    assert client.calls[0]["analysis_size"] == (1024, 540)
    assert client.calls[0]["analysis_format"] == "PNG"
    assert "button labels" in client.calls[0]["system_prompt"]
    assert '"text": "$99.99"' in client.calls[0]["user_prompt"]
    assert '"width": 1024' in client.calls[0]["user_prompt"]
    assert '"analysis_rect"' in client.calls[0]["user_prompt"]
    assert client.calls[0]["response_schema"]["title"] == "TextAuditResult"
    stripped_schema = client.calls[0]["response_schema"]["$defs"]["StrippedSymbol"]
    assert "role" in stripped_schema["required"]
    assert "default" not in stripped_schema["properties"]["role"]


def test_raster_logos_and_editable_price_are_classified_correctly(
    tmp_path: Path,
) -> None:
    image, texts_json = _write_inputs(tmp_path)

    result = UITextAuditor(client=FakeVLMClient()).audit(image, texts_json)

    assert result.raster_text_ids == ["text_000", "text_001"]
    editable_by_id = {item.id: item for item in result.editable_texts}
    assert editable_by_id["text_002"].text == "$99.99"
    assert editable_by_id["text_002"].role == "button_label"
    assert not set(result.raster_text_ids) & set(editable_by_id)


def test_plus_symbol_is_stripped_while_numeric_value_remains_editable(
    tmp_path: Path,
) -> None:
    image, texts_json = _write_inputs(tmp_path)

    result = UITextAuditor(client=FakeVLMClient()).audit(image, texts_json)

    editable_by_id = {item.id: item.text for item in result.editable_texts}
    assert editable_by_id["text_003"] == "176"
    assert result.stripped_symbols[0].source_text_id == "text_003"
    assert result.stripped_symbols[0].symbol == "+"
    assert result.stripped_symbols[0].role == "button"


def test_export_artifacts_uses_utf8_without_ascii_escaping(tmp_path: Path) -> None:
    image, texts_json = _write_inputs(tmp_path)
    auditor = UITextAuditor(client=FakeVLMClient())
    result = auditor.audit(image, texts_json)
    output = tmp_path / "nested" / "audit.json"

    auditor.export_artifacts(result, output)

    serialized = output.read_text(encoding="utf-8")
    assert "重置" in serialized
    assert "\\u91cd" not in serialized
    assert json.loads(serialized)["raster_text_ids"] == ["text_000", "text_001"]


def test_invalid_json_response_raises_audit_response_error(tmp_path: Path) -> None:
    image, texts_json = _write_inputs(tmp_path)
    auditor = UITextAuditor(client=FakeVLMClient(response="not valid JSON"))

    with pytest.raises(AuditResponseError, match="not valid JSON"):
        auditor.audit(image, texts_json)


def test_schema_invalid_response_raises_audit_response_error(tmp_path: Path) -> None:
    image, texts_json = _write_inputs(tmp_path)
    incomplete = {"scene_summary": "missing required arrays"}
    auditor = UITextAuditor(client=FakeVLMClient(response=incomplete))

    with pytest.raises(AuditResponseError, match="schema validation"):
        auditor.audit(image, texts_json)


def test_network_failure_is_wrapped_as_client_error(tmp_path: Path) -> None:
    image, texts_json = _write_inputs(tmp_path)
    client = FakeVLMClient(error=ConnectionError("relay unavailable"))

    with pytest.raises(AuditClientError, match="relay unavailable"):
        UITextAuditor(client=client).audit(image, texts_json)


def test_pydantic_rejects_invalid_normalized_bbox() -> None:
    with pytest.raises(ValidationError):
        TextCorrection(text="重置", bbox_norm=[-0.1, 0.2, 0.3, 0.4])
