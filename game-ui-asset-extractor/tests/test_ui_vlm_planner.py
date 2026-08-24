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

import ui_vlm_planner as planner_module  # noqa: E402
from ui_plan_models import GeometryHint, LayerPlanResult  # noqa: E402
from ui_vlm_planner import OpenAICompatibleVLMClient, UIVLMPlanner  # noqa: E402


def _plan_payload() -> dict[str, Any]:
    return {
        "scene_summary": "Fantasy inventory with a framed card and crystal icon.",
        "raster_text_ids": ["text_000"],
        "queries": [
            {
                "id": "main_panel",
                "name": "Main inventory panel",
                "kind": "panel",
                "role": "container",
                "parent_query_id": None,
                "z_order": 0,
                "element_repair_mode": "surface",
                "geometry_hints": [
                    {
                        "bbox_norm": [0.1, 0.1, 0.9, 0.9],
                        "positive_points_norm": [[0.15, 0.15], [0.85, 0.85]],
                        "negative_points_norm": [],
                    }
                ],
            },
            {
                "id": "crystal_icon_01",
                "name": "Blue crystal icon",
                "kind": "icon",
                "role": "visual_artwork",
                "parent_query_id": "main_panel",
                "z_order": 1,
                "element_repair_mode": "none",
                "geometry_hints": [
                    {
                        "bbox_norm": [0.35, 0.3, 0.6, 0.7],
                        "positive_points_norm": [[0.48, 0.5]],
                        "negative_points_norm": [[0.65, 0.5]],
                    }
                ],
            },
        ],
        "background_repair": {
            "mode": "scene",
            "description": "Continue the painted room behind the panel.",
        },
    }


class FakeVLMClient:
    def __init__(self, response: Any | None = None) -> None:
        self.response = _plan_payload() if response is None else response
        self.calls: list[dict[str, Any]] = []

    def infer_json(
        self,
        original_image_path: Path,
        cleaned_image_path: Path,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append(
            {
                "original": original_image_path,
                "cleaned": cleaned_image_path,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "response_schema": response_schema,
            }
        )
        return self.response


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    original = tmp_path / "original.png"
    cleaned = tmp_path / "cleaned.png"
    Image.new("RGB", (200, 100), (30, 45, 70)).save(original)
    Image.new("RGB", (200, 100), (35, 50, 75)).save(cleaned)
    texts_json = tmp_path / "texts.json"
    texts_json.write_text(
        json.dumps(
            {
                "image_width": 200,
                "image_height": 100,
                "items": [
                    {
                        "id": "text_000",
                        "text": "ARCANE",
                        "confidence": 0.99,
                        "rect": {"x": 40, "y": 8, "width": 120, "height": 20},
                        "style": {"fontFamily": "decorative"},
                    },
                    {
                        "id": "text_001",
                        "text": "Equip",
                        "confidence": 0.98,
                        "rect": {"x": 80, "y": 75, "width": 40, "height": 12},
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return original, cleaned, texts_json


def test_geometry_contract_rejects_invalid_boxes_and_points() -> None:
    with pytest.raises(ValidationError, match="x0 < x1"):
        GeometryHint.model_validate(
            {
                "bbox_norm": [0.8, 0.1, 0.2, 0.9],
                "positive_points_norm": [[0.5, 0.5]],
                "negative_points_norm": [],
            }
        )

    with pytest.raises(ValidationError, match="at most 3 items"):
        GeometryHint.model_validate(
            {
                "bbox_norm": [0.1, 0.1, 0.9, 0.9],
                "positive_points_norm": [[0.2, 0.2]] * 4,
                "negative_points_norm": [],
            }
        )


def test_layer_plan_rejects_invalid_parent_z_order() -> None:
    payload = _plan_payload()
    child, parent = payload["queries"][1], payload["queries"][0]
    child["z_order"] = 1
    parent["z_order"] = 2
    payload["queries"] = [child, parent]

    with pytest.raises(ValidationError, match="must have a lower z_order"):
        LayerPlanResult.model_validate(payload)


def test_planner_uses_dual_image_order_and_dehydrated_texts(tmp_path: Path) -> None:
    original, cleaned, texts_json = _write_inputs(tmp_path)
    client = FakeVLMClient(response=json.dumps(_plan_payload(), ensure_ascii=False))

    result = UIVLMPlanner(client=client).plan(original, cleaned, texts_json)

    assert isinstance(result, LayerPlanResult)
    assert result.queries[1].parent_query_id == "main_panel"
    call = client.calls[0]
    assert call["original"] == original
    assert call["cleaned"] == cleaned
    assert "1. the original high-resolution UI image" in call["system_prompt"]
    assert "2. cleaned_image" in call["system_prompt"]
    assert '"text": "Equip"' in call["user_prompt"]
    assert "confidence" not in call["user_prompt"]
    assert "fontFamily" not in call["user_prompt"]
    assert call["response_schema"]["additionalProperties"] is False


class _FakeHTTPResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"output_text": json.dumps(_plan_payload())}


class _MalformedRelayHTTPResponse:
    def __init__(self) -> None:
        model_json = json.dumps(_plan_payload(), ensure_ascii=False)
        self.text = (
            '{"id":"resp_test","object":"response","status":"completed",'
            '"output":[{"type":"message","role":"assistant","content":['
            '{"type":"output_text","text":"'
            + model_json
            + '"}]}],"usage":{"input_tokens":10,"output_tokens":20}}'
        )

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return json.loads(self.text)


class _FakeSession:
    def __init__(self, response: Any | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response = _FakeHTTPResponse() if response is None else response

    def post(self, url: str, **kwargs: Any) -> Any:
        self.calls.append({"url": url, **kwargs})
        return self.response


def test_openai_compatible_client_sends_two_base64_images_in_order(
    tmp_path: Path,
) -> None:
    original, cleaned, _ = _write_inputs(tmp_path)
    session = _FakeSession()
    client = OpenAICompatibleVLMClient(
        base_url="https://relay.example/v1",
        api_key="secret",
        session=session,
    )

    result = client.infer_json(original, cleaned, "system", "user", None)

    assert result["queries"][0]["id"] == "main_panel"
    request = session.calls[0]
    assert request["url"] == "https://relay.example/v1/responses"
    assert request["json"]["temperature"] == 0.1
    assert request["json"]["stream"] is False
    content = request["json"]["input"][0]["content"]
    image_urls = [item["image_url"] for item in content if item["type"] == "input_image"]
    assert len(image_urls) == 2
    assert image_urls[0].startswith("data:image/png;base64,")
    assert image_urls[1].startswith("data:image/png;base64,")
    assert image_urls[0] != image_urls[1]


def test_client_recovers_unescaped_json_from_malformed_relay_envelope(
    tmp_path: Path,
) -> None:
    original, cleaned, _ = _write_inputs(tmp_path)
    session = _FakeSession(_MalformedRelayHTTPResponse())
    client = OpenAICompatibleVLMClient(
        base_url="https://relay.example/v1",
        api_key="secret",
        session=session,
    )

    result = client.infer_json(original, cleaned, "system", "user", None)

    assert result == _plan_payload()


def test_cli_exports_layer_plan_and_debug_visualization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original, cleaned, texts_json = _write_inputs(tmp_path)
    fake_client = FakeVLMClient()
    monkeypatch.setattr(
        planner_module,
        "_create_default_client",
        lambda model: fake_client,
    )
    output_json = tmp_path / "artifacts" / "layer_plan.json"
    output_vis = tmp_path / "artifacts" / "plan_debug.png"

    exit_code = planner_module.main(
        [
            "--original",
            str(original),
            "--cleaned",
            str(cleaned),
            "--texts-json",
            str(texts_json),
            "--output-json",
            str(output_json),
            "--output-vis",
            str(output_vis),
        ]
    )

    assert exit_code == 0
    exported = json.loads(output_json.read_text(encoding="utf-8"))
    assert exported == _plan_payload()
    with Image.open(output_vis) as debug_image:
        assert debug_image.format == "PNG"
        assert debug_image.size == (200, 100)
