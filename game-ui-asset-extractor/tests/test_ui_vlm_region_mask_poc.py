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

import ui_vlm_region_mask_poc as poc  # noqa: E402


def _text_item(
    text_id: str,
    text: str,
    x: int,
    y: int,
    width: int,
    height: int,
) -> dict[str, Any]:
    return {
        "id": text_id,
        "text": text,
        "confidence": 0.9,
        "rect": {"x": x, "y": y, "width": width, "height": height},
        "style": {
            "color": "#FFFFFF",
            "fontFamily": "Arial",
            "fontSize": 12,
            "fontWeight": 600,
            "strokeColor": "#1e2322",
            "strokeWidth": 1,
        },
        "mask_mode": "coarse",
    }


def _payload() -> dict[str, Any]:
    return {
        "texts": [
            {
                "text": "Corrected UI",
                "bbox_analysis": {"x": 320, "y": 80, "width": 160, "height": 40},
                "ownership": "ui_owned",
                "semantic_role": "ordinary_title",
                "confidence": 0.99,
            },
            {
                "text": "LOGO",
                "bbox_analysis": {"x": 800, "y": 160, "width": 120, "height": 80},
                "ownership": "asset_owned",
                "semantic_role": "embedded_logo",
                "confidence": 0.98,
            },
            {
                "text": "7",
                "bbox_analysis": {"x": 160, "y": 400, "width": 24, "height": 32},
                "ownership": "ui_owned",
                "semantic_role": "runtime_value",
                "confidence": 0.96,
            },
        ]
    }


class FakeClient:
    def __init__(self, response: Any | None = None) -> None:
        self.response = _payload() if response is None else response
        self.calls: list[dict[str, Any]] = []

    def infer_json(
        self,
        *,
        image_path: Path,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
    ) -> Any:
        with Image.open(image_path) as image:
            image_size = image.size
        self.calls.append(
            {
                "image_size": image_size,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "response_schema": response_schema,
            }
        )
        return self.response


class SequencedClient(FakeClient):
    def __init__(self, responses: list[Any]) -> None:
        if not responses:
            raise ValueError("responses must not be empty")
        super().__init__(responses[0])
        self.responses = responses

    def infer_json(
        self,
        *,
        image_path: Path,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
    ) -> Any:
        call_index = len(self.calls)
        if call_index >= len(self.responses):
            raise AssertionError("unexpected extra VLM request")
        self.response = self.responses[call_index]
        return super().infer_json(
            image_path=image_path,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=response_schema,
        )


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    image_path = tmp_path / "original.png"
    Image.new("RGB", (128, 64), (25, 50, 75)).save(image_path)
    texts_path = tmp_path / "texts.json"
    items = [
        _text_item("text_000", "Wrong OCR", 2, 2, 10, 5),
        _text_item("text_001", "False detection", 80, 40, 30, 8),
    ]
    texts_path.write_text(
        json.dumps(
            {
                "image_width": 128,
                "image_height": 64,
                "count": len(items),
                "items": items,
            }
        ),
        encoding="utf-8",
    )
    return image_path, texts_path


def test_process_uses_one_canonical_vlm_pass_and_only_vlm_boxes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path, texts_path = _write_inputs(tmp_path)
    output_dir = tmp_path / "out"
    client = FakeClient()

    def reject_inpaint(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("cv2.inpaint must not be called")

    monkeypatch.setattr(cv2, "inpaint", reject_inpaint)
    plan = poc.UIVLMRegionMaskPoC(client=client).process(
        image_path, texts_path, output_dir
    )

    assert len(client.calls) == 1
    assert client.calls[0]["image_size"] == (1024, 512)
    prompt = client.calls[0]["user_prompt"]
    assert "Wrong OCR" in prompt
    assert '"x":16,"y":16,"width":80,"height":40' in prompt
    assert "OCR IDs" in prompt
    assert [item["text"] for item in plan["texts"]] == [
        "Corrected UI",
        "LOGO",
        "7",
    ]
    assert "False detection" not in [item["text"] for item in plan["texts"]]

    # The first VLM box maps to source (40, 10, 20, 5); the OCR box was elsewhere.
    assert plan["texts"][0]["bbox_source"] == {
        "x": 40,
        "y": 10,
        "width": 20,
        "height": 5,
    }
    assert plan["texts"][0]["decision"] == "remove_for_background_repair"
    assert plan["texts"][1]["decision"] == "preserve_as_visual_asset"

    mask = cv2.imread(str(output_dir / "region-mask.png"), cv2.IMREAD_GRAYSCALE)
    assert mask is not None
    expected = np.zeros((64, 128), dtype=np.uint8)
    expected[10:15, 40:60] = 255
    expected[50:54, 20:23] = 255  # OCR-missing text added by the VLM.
    assert np.array_equal(mask, expected)
    assert np.all(mask[20:30, 100:115] == 0)  # Asset-owned VLM box.
    assert np.all(mask[2:7, 2:12] == 0)  # Original OCR box is not reused.
    assert set(path.name for path in output_dir.iterdir()) == {
        "vlm-region-plan.json",
        "region-mask.png",
        "region-mask-overlay.png",
    }


@pytest.mark.parametrize(
    ("ownership", "role"),
    [
        ("ui_owned", "embedded_logo"),
        ("asset_owned", "button_label"),
    ],
)
def test_ownership_role_mismatch_rejects_whole_response(
    tmp_path: Path, ownership: str, role: str
) -> None:
    image_path, texts_path = _write_inputs(tmp_path)
    payload = _payload()
    payload["texts"][0]["ownership"] = ownership
    payload["texts"][0]["semantic_role"] = role
    output_dir = tmp_path / "out"

    with pytest.raises(poc.RegionMaskResponseError, match="incompatible"):
        poc.UIVLMRegionMaskPoC(client=FakeClient(payload)).process(
            image_path, texts_path, output_dir
        )

    assert not output_dir.exists()


def test_out_of_bounds_vlm_bbox_rejects_whole_response(tmp_path: Path) -> None:
    image_path, texts_path = _write_inputs(tmp_path)
    payload = _payload()
    payload["texts"][0]["bbox_analysis"] = {
        "x": 1000,
        "y": 10,
        "width": 25,
        "height": 10,
    }
    output_dir = tmp_path / "out"

    with pytest.raises(poc.RegionMaskResponseError, match="exceeds"):
        poc.UIVLMRegionMaskPoC(client=FakeClient(payload)).process(
            image_path, texts_path, output_dir
        )

    assert not output_dir.exists()


def test_text_regions_is_not_accepted_as_a_local_alias() -> None:
    drifting_payload = {"text_regions": _payload()["texts"]}

    with pytest.raises(poc.RegionMaskResponseError) as exc_info:
        poc._validate_canonical_response(drifting_payload, 1024, 512)

    detail = str(exc_info.value)
    assert "texts" in detail
    assert "text_regions" in detail
    assert isinstance(exc_info.value.__cause__, ValidationError)


def test_text_regions_then_texts_retries_and_preserves_mask_behavior(
    tmp_path: Path,
) -> None:
    image_path, texts_path = _write_inputs(tmp_path)
    canonical_output = tmp_path / "canonical"
    retry_output = tmp_path / "retry"
    canonical_plan = poc.UIVLMRegionMaskPoC(client=FakeClient()).process(
        image_path, texts_path, canonical_output
    )
    client = SequencedClient(
        [
            {"text_regions": _payload()["texts"]},
            _payload(),
        ]
    )

    retry_plan = poc.UIVLMRegionMaskPoC(client=client).process(
        image_path, texts_path, retry_output
    )

    assert len(client.calls) == 2
    assert poc.SCHEMA_RETRY_INSTRUCTION not in client.calls[0]["user_prompt"]
    assert poc.SCHEMA_RETRY_INSTRUCTION in client.calls[1]["user_prompt"]
    assert client.calls[0]["response_schema"] == client.calls[1]["response_schema"]
    assert retry_plan == canonical_plan
    canonical_mask = cv2.imread(
        str(canonical_output / "region-mask.png"), cv2.IMREAD_GRAYSCALE
    )
    retry_mask = cv2.imread(
        str(retry_output / "region-mask.png"), cv2.IMREAD_GRAYSCALE
    )
    assert np.array_equal(retry_mask, canonical_mask)


def test_two_schema_violations_fail_after_exactly_two_requests(tmp_path: Path) -> None:
    image_path, texts_path = _write_inputs(tmp_path)
    invalid = {"text_regions": _payload()["texts"]}
    client = SequencedClient([invalid, invalid])
    output_dir = tmp_path / "out"

    with pytest.raises(
        poc.RegionMaskResponseError, match="failed schema validation after 2 attempts"
    ):
        poc.UIVLMRegionMaskPoC(client=client).process(
            image_path, texts_path, output_dir
        )

    assert len(client.calls) == 2
    assert not output_dir.exists()


def test_malformed_json_then_canonical_json_retries_successfully(
    tmp_path: Path,
) -> None:
    image_path, texts_path = _write_inputs(tmp_path)
    client = SequencedClient(['{"texts":', _payload()])

    plan = poc.UIVLMRegionMaskPoC(client=client).process(
        image_path, texts_path, tmp_path / "out"
    )

    assert len(client.calls) == 2
    assert plan["texts"][0]["text"] == "Corrected UI"


def test_analysis_to_source_uses_floor_ceil_and_clamp() -> None:
    mapped = poc._analysis_to_source_bbox(
        poc.AnalysisBBox(x=1, y=2, width=10, height=10),
        analysis_width=1024,
        analysis_height=683,
        source_width=300,
        source_height=200,
    )
    assert mapped == {"x": 0, "y": 0, "width": 4, "height": 4}

    edge = poc._analysis_to_source_bbox(
        poc.AnalysisBBox(x=1023, y=682, width=1, height=1),
        analysis_width=1024,
        analysis_height=683,
        source_width=300,
        source_height=200,
    )
    assert edge == {"x": 299, "y": 199, "width": 1, "height": 1}


def test_padding_zero_does_not_expand_rectangular_mask() -> None:
    mask = np.zeros((12, 15), dtype=np.uint8)
    poc._mask_bbox(mask, {"x": 4, "y": 3, "width": 5, "height": 4}, 0)

    expected = np.zeros_like(mask)
    expected[3:7, 4:9] = 255
    assert np.array_equal(mask, expected)
    assert np.count_nonzero(mask) == 5 * 4


def test_schema_requires_all_fields_and_forbids_extra_properties() -> None:
    schema = poc._strict_response_schema()
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["texts"]
    text_schema = schema["$defs"]["CanonicalText"]
    assert text_schema["additionalProperties"] is False
    assert set(text_schema["required"]) == {
        "text",
        "bbox_analysis",
        "ownership",
        "semantic_role",
        "confidence",
    }


def test_system_prompt_is_generic_and_has_no_pilot_specific_hints() -> None:
    prompt = poc.SYSTEM_PROMPT.casefold()
    assert "ocr candidates as hints" in prompt
    assert "do not output source-image coordinates" in prompt
    assert "do not output remove/preserve decisions" in prompt
    assert 'the only allowed top-level key is "texts"' in prompt
    assert '"text_regions"' in prompt
    assert '"regions"' in prompt
    assert '"detected_texts"' in prompt
    for forbidden in ("inventory", "backpack", "slot count", "quantity corner", "dyg"):
        assert forbidden not in prompt
