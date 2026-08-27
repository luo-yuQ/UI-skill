from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import ui_text_repair_planner as planner_module  # noqa: E402
from ui_audit_models import TextItem  # noqa: E402
from ui_text_extractor import UITextExtractor  # noqa: E402
from ui_text_repair_planner import (  # noqa: E402
    TextRepairContractError,
    UITextRepairPlanner,
    VLMTextDecisionResponse,
    _validate_complete_classification,
)


IMAGE_WIDTH = 40
IMAGE_HEIGHT = 24


def _style() -> dict[str, Any]:
    return {
        "fontFamily": "Arial",
        "fontSize": 12,
        "color": "#FFFFFF",
        "fontWeight": 700,
        "strokeColor": "#1e2322",
        "strokeWidth": 1,
    }


def _items() -> list[TextItem]:
    return [
        TextItem.model_validate(
            {
                "id": "text_000",
                "text": "START",
                "confidence": 0.98,
                "rect": {"x": 4, "y": 5, "width": 8, "height": 6},
                "style": _style(),
                "mask_mode": "estimated_glyphs",
            }
        ),
        TextItem.model_validate(
            {
                "id": "text_001",
                "text": "HERO",
                "confidence": 0.97,
                "rect": {"x": 25, "y": 5, "width": 8, "height": 6},
                "style": _style(),
                "mask_mode": "estimated_glyphs",
            }
        ),
    ]


def _payload(decisions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "decisions": decisions
        if decisions is not None
        else [
            {
                "id": "text_000",
                "decision": "remove_for_background_repair",
                "confidence": 0.99,
                "reason": "runtime button label",
            },
            {
                "id": "text_001",
                "decision": "preserve_as_visual_asset",
                "confidence": 0.96,
                "reason": "baked into item artwork",
            },
        ]
    }


class FakeVLMClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def infer_json(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.payload


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    image = np.full((IMAGE_HEIGHT, IMAGE_WIDTH, 3), 90, dtype=np.uint8)
    image_path = tmp_path / "original.png"
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    encoded.tofile(str(image_path))

    texts_path = tmp_path / "texts.json"
    items = _items()
    texts_path.write_text(
        json.dumps(
            {
                "image_width": IMAGE_WIDTH,
                "image_height": IMAGE_HEIGHT,
                "count": len(items),
                "items": [item.model_dump(mode="json") for item in items],
            }
        ),
        encoding="utf-8",
    )

    raw_mask_path = tmp_path / "raw_text_mask.png"
    raw_mask = np.full((IMAGE_HEIGHT, IMAGE_WIDTH), 255, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", raw_mask)
    assert ok
    encoded.tofile(str(raw_mask_path))
    return image_path, texts_path, raw_mask_path


def test_complete_classification_returns_one_decision_per_input_id() -> None:
    response = VLMTextDecisionResponse.model_validate(_payload())
    classified = _validate_complete_classification(_items(), response)
    assert set(classified) == {"text_000", "text_001"}


@pytest.mark.parametrize(
    ("decisions", "message"),
    [
        (
            [
                {
                    "id": "text_000",
                    "decision": "remove_for_background_repair",
                    "confidence": 0.9,
                    "reason": "runtime label",
                }
            ],
            "omitted OCR text IDs",
        ),
        (
            [
                {
                    "id": "text_000",
                    "decision": "remove_for_background_repair",
                    "confidence": 0.9,
                    "reason": "runtime label",
                },
                {
                    "id": "text_000",
                    "decision": "preserve_as_visual_asset",
                    "confidence": 0.8,
                    "reason": "artwork",
                },
                {
                    "id": "text_001",
                    "decision": "preserve_as_visual_asset",
                    "confidence": 0.8,
                    "reason": "artwork",
                },
            ],
            "Duplicate OCR text classifications",
        ),
        (
            [
                {
                    "id": "text_000",
                    "decision": "remove_for_background_repair",
                    "confidence": 0.9,
                    "reason": "runtime label",
                },
                {
                    "id": "text_001",
                    "decision": "preserve_as_visual_asset",
                    "confidence": 0.8,
                    "reason": "artwork",
                },
                {
                    "id": "text_unknown",
                    "decision": "preserve_as_visual_asset",
                    "confidence": 0.8,
                    "reason": "artwork",
                },
            ],
            "unknown OCR text IDs",
        ),
    ],
)
def test_incomplete_duplicate_and_unknown_classifications_fail(
    decisions: list[dict[str, Any]],
    message: str,
) -> None:
    response = VLMTextDecisionResponse.model_validate(_payload(decisions))
    with pytest.raises(TextRepairContractError, match=message):
        _validate_complete_classification(_items(), response)


def test_process_builds_positive_union_expansion_and_overlay_without_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path, texts_path, raw_mask_path = _write_inputs(tmp_path)
    output_dir = tmp_path / "route-b"
    rebuilt_ids: list[str] = []

    def fake_rebuild(
        cls: type[UITextExtractor],
        image: np.ndarray,
        rect: Any,
        text: str,
    ) -> np.ndarray:
        del cls, text
        rebuilt_ids.append(f"{rect.x},{rect.y}")
        result = np.zeros(image.shape[:2], dtype=np.uint8)
        result[rect.y + 2, rect.x + 2] = 255
        return result

    def forbidden_inpaint(*args: Any, **kwargs: Any) -> np.ndarray:
        del args, kwargs
        raise AssertionError("Route B planner must not call Telea/inpaint")

    monkeypatch.setattr(
        UITextExtractor,
        "rebuild_text_mask",
        classmethod(fake_rebuild),
    )
    monkeypatch.setattr(cv2, "inpaint", forbidden_inpaint)

    client = FakeVLMClient(_payload())
    result = UITextRepairPlanner(client=client).process(
        image_path,
        texts_path,
        raw_mask_path,
        output_dir,
        dilation_radius=2,
    )

    assert rebuilt_ids == ["4,5"]
    assert [item.id for item in result.decisions] == ["text_000", "text_001"]
    assert {path.name for path in output_dir.iterdir()} == {
        "text-repair-decisions.json",
        "union-text-mask.png",
        "repair-mask.png",
        "repair-mask-overlay.png",
    }

    union = cv2.imread(
        str(output_dir / "union-text-mask.png"), cv2.IMREAD_GRAYSCALE
    )
    repair = cv2.imread(str(output_dir / "repair-mask.png"), cv2.IMREAD_GRAYSCALE)
    overlay = cv2.imread(
        str(output_dir / "repair-mask-overlay.png"), cv2.IMREAD_COLOR
    )
    assert union is not None and repair is not None and overlay is not None
    assert union.shape == repair.shape == (IMAGE_HEIGHT, IMAGE_WIDTH)
    assert overlay.shape == (IMAGE_HEIGHT, IMAGE_WIDTH, 3)
    assert union[7, 6] == 255
    assert np.count_nonzero(union[:, 20:]) == 0

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    expected_repair = cv2.dilate(union, kernel, iterations=1)
    assert np.array_equal(repair, expected_repair)
    assert np.count_nonzero(repair) > np.count_nonzero(union)


def test_planner_has_no_inpaint_or_image_generation_dependency() -> None:
    source = inspect.getsource(planner_module)
    assert "cv2.inpaint" not in source
    assert "image_gen" not in source
    assert "Image 2" not in source
