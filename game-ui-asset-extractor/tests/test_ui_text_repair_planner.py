from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from pydantic import ValidationError


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import ui_text_repair_planner as planner_module  # noqa: E402
from ui_audit_models import TextItem  # noqa: E402
from ui_text_extractor import UITextExtractor  # noqa: E402
from ui_text_repair_planner import (  # noqa: E402
    COVERAGE_SYSTEM_PROMPT,
    AnalysisBBox,
    CoarseMaskRefinementResult,
    MaskMetrics,
    RepairTextCandidate,
    SEMANTIC_ROLE_TO_DECISION,
    SYSTEM_PROMPT,
    TextRepairDecision,
    TextRepairDecisionDocument,
    TextRepairContractError,
    UITextRepairPlanner,
    VLMCoverageAuditResponse,
    VLMTextDecisionResponse,
    _validate_complete_classification,
    _small_text_failure_reason,
    _is_small_text_refinement_eligible,
    build_union_text_mask,
    decision_for_semantic_role,
    normalize_and_deduplicate_corrections,
    normalize_ocr_candidates,
    refine_coarse_text_mask,
    refine_coarse_text_mask_with_diagnostics,
    validate_and_map_analysis_bbox,
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
                "semantic_role": "button_label",
                "confidence": 0.99,
                "reason": "runtime button label",
            },
            {
                "id": "text_001",
                "semantic_role": "embedded_in_artwork",
                "confidence": 0.96,
                "reason": "baked into item artwork",
            },
        ]
    }


class FakeVLMClient:
    def __init__(self, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
        self.payloads = payload if isinstance(payload, list) else [payload]
        self.calls: list[dict[str, Any]] = []

    def infer_json(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if not self.payloads:
            raise AssertionError("FakeVLMClient has no response left")
        return self.payloads.pop(0)


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


def test_semantic_role_is_closed_and_vlm_cannot_supply_policy_decision() -> None:
    invalid_role = _payload()
    invalid_role["decisions"][0]["semantic_role"] = "free_text_role"
    with pytest.raises(ValidationError):
        VLMTextDecisionResponse.model_validate(invalid_role)

    policy_override = _payload()
    policy_override["decisions"][0]["decision"] = "preserve_as_visual_asset"
    with pytest.raises(ValidationError):
        VLMTextDecisionResponse.model_validate(policy_override)


@pytest.mark.parametrize(
    ("semantic_role", "expected"),
    list(
        zip(
            (
                "navigation_label",
                "button_label",
                "runtime_value",
                "body_text",
                "ordinary_title",
                "status_text",
            ),
            ["remove_for_background_repair"] * 6,
            strict=True,
        )
    )
    + list(
        zip(
            (
                "embedded_in_artwork",
                "embedded_logo",
                "decorative_art_text",
            ),
            ["preserve_as_visual_asset"] * 3,
            strict=True,
        )
    ),
)
def test_semantic_role_maps_to_deterministic_policy(
    semantic_role: Any,
    expected: str,
) -> None:
    assert decision_for_semantic_role(semantic_role) == expected
    assert SEMANTIC_ROLE_TO_DECISION[semantic_role] == expected


def test_prompt_is_generic_and_requires_visual_ownership() -> None:
    assert "Classify by visual ownership, not by text meaning alone" in SYSTEM_PROMPT
    assert "belongs to the UI information layer" in SYSTEM_PROMPT
    assert "belongs to a visual artwork/asset" in SYSTEM_PROMPT
    assert "Do not infer visual ownership from vocabulary" in SYSTEM_PROMPT
    assert "Do not return a decision field" in SYSTEM_PROMPT
    pilot_literals = (
        "皮肤",
        "英雄",
        "HERO",
        "DYG",
        "查看畅玩池",
        "豪华皮肤畅玩卡",
        "背包",
        "批量使用",
        "638050",
        "50209",
        "inventory",
    )
    assert all(value not in SYSTEM_PROMPT for value in pilot_literals)


def test_pilot_semantics_are_role_driven_including_same_skin_text() -> None:
    remove_examples = {
        "背包": "ordinary_title",
        "批量使用": "button_label",
        "全部": "navigation_label",
        "最近获得": "navigation_label",
        "限时道具": "navigation_label",
        "道具": "navigation_label",
        "宝箱": "navigation_label",
        "体验卡": "navigation_label",
        "638050": "runtime_value",
        "50209": "runtime_value",
        "176+": "runtime_value",
        "可前往邮件或者背包查看": "body_text",
        "豪华皮肤畅玩卡": "ordinary_title",
        "拥有 7": "status_text",
        "正文说明": "body_text",
        "剩余时间": "status_text",
        "查看畅玩池": "button_label",
        "物品数量角标": "runtime_value",
        "皮肤": "navigation_label",
    }
    preserve_examples = {
        "英雄": "embedded_in_artwork",
        "战令": "embedded_in_artwork",
        "1级": "embedded_in_artwork",
        "HE2D / HERO": "embedded_logo",
        "DYG": "embedded_logo",
        "皮肤": "embedded_in_artwork",
        "货币": "embedded_in_artwork",
        "元流": "decorative_art_text",
    }
    assert all(
        decision_for_semantic_role(role) == "remove_for_background_repair"
        for role in remove_examples.values()
    )
    assert all(
        decision_for_semantic_role(role) == "preserve_as_visual_asset"
        for role in preserve_examples.values()
    )
    assert remove_examples["皮肤"] != preserve_examples["皮肤"]


@pytest.mark.parametrize(
    ("decisions", "message"),
    [
        (
            [
                {
                    "id": "text_000",
                    "semantic_role": "button_label",
                    "confidence": 0.9,
                    "reason": "runtime label",
                }
            ],
            "omitted text candidate IDs",
        ),
        (
            [
                {
                    "id": "text_000",
                    "semantic_role": "button_label",
                    "confidence": 0.9,
                    "reason": "runtime label",
                },
                {
                    "id": "text_000",
                    "semantic_role": "embedded_in_artwork",
                    "confidence": 0.8,
                    "reason": "artwork",
                },
                {
                    "id": "text_001",
                    "semantic_role": "embedded_in_artwork",
                    "confidence": 0.8,
                    "reason": "artwork",
                },
            ],
            "Duplicate text candidate classifications",
        ),
        (
            [
                {
                    "id": "text_000",
                    "semantic_role": "button_label",
                    "confidence": 0.9,
                    "reason": "runtime label",
                },
                {
                    "id": "text_001",
                    "semantic_role": "embedded_in_artwork",
                    "confidence": 0.8,
                    "reason": "artwork",
                },
                {
                    "id": "text_unknown",
                    "semantic_role": "embedded_logo",
                    "confidence": 0.8,
                    "reason": "artwork",
                },
            ],
            "unknown text candidate IDs",
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


def _coarse_item() -> TextItem:
    return TextItem.model_validate(
        {
            "id": "text_032",
            "text": "查看畅玩池",
            "confidence": 0.99,
            "rect": {"x": 5, "y": 4, "width": 70, "height": 24},
            "style": {
                **_style(),
                "fontFamily": "Microsoft YaHei",
                "color": "#FFFFFF",
                "fontSize": 20,
            },
            "mask_mode": "coarse",
        }
    )


def _coarse_document(decision: str = "remove_for_background_repair") -> TextRepairDecisionDocument:
    role = (
        "button_label"
        if decision == "remove_for_background_repair"
        else "embedded_in_artwork"
    )
    return TextRepairDecisionDocument(
        image_width=80,
        image_height=32,
        decisions=[
            TextRepairDecision(
                id="text_032",
                text="查看畅玩池",
                semantic_role=role,
                decision=decision,
                rect=_coarse_item().rect,
                mask_mode="coarse",
                mask_quality="failed",
                confidence=0.99,
                reason="visual ownership classification",
            )
        ],
    )


def test_refine_coarse_text_mask_extracts_glyphs_not_the_whole_button_bbox() -> None:
    image = np.full((32, 80, 3), [32, 70, 120], dtype=np.uint8)
    for x in (14, 25, 36, 47, 58):
        cv2.rectangle(image, (x, 10), (x + 3, 21), (255, 255, 255), -1)
        cv2.rectangle(image, (x - 2, 14), (x + 5, 17), (255, 255, 255), -1)

    refined = refine_coarse_text_mask(image, _coarse_item())

    assert refined is not None
    roi = refined[4:28, 5:75]
    assert 0 < np.count_nonzero(roi) < 0.55 * roi.size
    assert not np.all(roi == 255)
    assert not np.any(np.all(roi == 255, axis=1))


def test_refine_coarse_text_mask_fails_on_unseparated_surface() -> None:
    image = np.full((32, 80, 3), [32, 70, 120], dtype=np.uint8)
    assert refine_coarse_text_mask(image, _coarse_item()) is None


def test_coarse_remove_enters_union_only_after_successful_refine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = np.zeros((32, 80, 3), dtype=np.uint8)
    refined = np.zeros((32, 80), dtype=np.uint8)
    refined[12:16, 20:36] = 255
    monkeypatch.setattr(
        planner_module,
        "refine_coarse_text_mask_with_diagnostics",
        lambda _image, _item: CoarseMaskRefinementResult(
            mask=refined.copy(),
            path="standard_coarse_refine",
            failure_reason=None,
            metrics=None,
        ),
    )
    monkeypatch.setattr(
        UITextExtractor,
        "rebuild_text_mask",
        classmethod(
            lambda cls, source, rect, text: pytest.fail(
                "coarse item must not use rebuild_text_mask"
            )
        ),
    )

    union, updated = build_union_text_mask(
        image,
        [_coarse_item()],
        _coarse_document(),
    )

    assert np.array_equal(union, refined)
    assert updated.decisions[0].mask_quality == "refined"


def test_failed_coarse_refine_and_preserve_items_never_enter_union(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = np.zeros((32, 80, 3), dtype=np.uint8)
    calls: list[str] = []

    def failed_refine(
        _image: np.ndarray,
        item: TextItem,
    ) -> CoarseMaskRefinementResult:
        calls.append(item.id)
        return CoarseMaskRefinementResult(
            mask=None,
            path="failed",
            failure_reason="no_reliable_candidate",
            metrics=None,
        )

    monkeypatch.setattr(
        planner_module,
        "refine_coarse_text_mask_with_diagnostics",
        failed_refine,
    )
    failed_union, failed_document = build_union_text_mask(
        image,
        [_coarse_item()],
        _coarse_document(),
    )
    preserve_union, preserve_document = build_union_text_mask(
        image,
        [_coarse_item()],
        _coarse_document("preserve_as_visual_asset"),
    )

    assert calls == ["text_032"]
    assert np.count_nonzero(failed_union) == 0
    assert failed_document.decisions[0].mask_quality == "failed"
    assert np.count_nonzero(preserve_union) == 0
    assert preserve_document.decisions[0].mask_quality == "failed"


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

    client = FakeVLMClient(
        [
            {"missing_text_candidates": []},
            _payload(),
        ]
    )
    result = UITextRepairPlanner(client=client).process(
        image_path,
        texts_path,
        raw_mask_path,
        output_dir,
        dilation_radius=2,
    )

    assert rebuilt_ids == ["4,5"]
    assert [item.id for item in result.decisions] == ["text_000", "text_001"]
    assert result.schema_version == "0.4"
    assert result.decisions[0].semantic_role == "button_label"
    assert result.decisions[0].decision == "remove_for_background_repair"
    assert result.decisions[0].source == "ocr"
    assert result.decisions[0].mask_quality == "native"
    assert result.decisions[0].mask_refinement_path == "native"
    assert result.decisions[0].mask_failure_reason is None
    assert result.decisions[0].mask_metrics is None
    assert result.decisions[1].semantic_role == "embedded_in_artwork"
    assert result.decisions[1].decision == "preserve_as_visual_asset"
    assert {path.name for path in output_dir.iterdir()} == {
        "coverage-audit.json",
        "text-repair-decisions.json",
        "union-text-mask.png",
        "repair-mask.png",
        "repair-mask-overlay.png",
    }
    serialized = json.loads(
        (output_dir / "text-repair-decisions.json").read_text(encoding="utf-8")
    )
    assert serialized == result.model_dump(mode="json")

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


def _coverage_payload(
    candidates: list[dict[str, Any]],
) -> VLMCoverageAuditResponse:
    return VLMCoverageAuditResponse.model_validate(
        {"missing_text_candidates": candidates}
    )


def _missing(
    text: str,
    x: float,
    y: float,
    width: float,
    height: float,
    confidence: float = 0.96,
) -> dict[str, Any]:
    return {
        "text": text,
        "bbox_analysis": {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        },
        "confidence": confidence,
    }


def test_coverage_prompt_is_generic_and_coverage_only() -> None:
    assert "complete screenshot" in COVERAGE_SYSTEM_PROMPT
    assert "coverage task" in COVERAGE_SYSTEM_PROMPT
    assert "not a semantic ownership or repair-policy task" in COVERAGE_SYSTEM_PROMPT
    assert "complete interface" in COVERAGE_SYSTEM_PROMPT
    assert "Do not return" in COVERAGE_SYSTEM_PROMPT
    pilot_literals = (
        "inventory",
        "backpack",
        "slot_count",
        "查看畅玩池",
        "638050",
    )
    assert all(value not in COVERAGE_SYSTEM_PROMPT for value in pilot_literals)


def test_coverage_contract_rejects_policy_and_assigned_id() -> None:
    with pytest.raises(ValidationError):
        _coverage_payload(
            [
                {
                    **_missing("42", 10, 10, 5, 6),
                    "decision": "remove_for_background_repair",
                }
            ]
        )
    with pytest.raises(ValidationError):
        _coverage_payload(
            [{**_missing("42", 10, 10, 5, 6), "id": "text_corr_001"}]
        )


@pytest.mark.parametrize(
    "bbox",
    [
        {"x": -1, "y": 0, "width": 1, "height": 1},
        {"x": 0, "y": 0, "width": 0, "height": 1},
        {"x": 0, "y": 0, "width": 1, "height": -1},
    ],
)
def test_coverage_bbox_rejects_negative_or_empty_dimensions(
    bbox: dict[str, float],
) -> None:
    with pytest.raises(ValidationError):
        AnalysisBBox.model_validate(bbox)


def test_analysis_bbox_out_of_bounds_fails() -> None:
    with pytest.raises(TextRepairContractError, match="fully contained"):
        validate_and_map_analysis_bbox(
            AnalysisBBox(x=95, y=10, width=6, height=5),
            analysis_width=100,
            analysis_height=50,
            source_width=200,
            source_height=100,
        )


def test_analysis_to_source_uses_real_axis_scales_and_outward_rounding() -> None:
    mapped = validate_and_map_analysis_bbox(
        AnalysisBBox(x=10.2, y=20.1, width=5.2, height=10.3),
        analysis_width=100,
        analysis_height=100,
        source_width=200,
        source_height=50,
    )
    assert mapped.model_dump() == {"x": 20, "y": 10, "width": 11, "height": 6}

    edge = validate_and_map_analysis_bbox(
        AnalysisBBox(x=99.5, y=49.5, width=0.5, height=0.5),
        analysis_width=100,
        analysis_height=50,
        source_width=333,
        source_height=77,
    )
    assert edge.x + edge.width == 333
    assert edge.y + edge.height == 77


def test_correction_overlapping_ocr_is_rejected_despite_text_difference() -> None:
    existing = normalize_ocr_candidates(_items())
    corrections, audit = normalize_and_deduplicate_corrections(
        _coverage_payload([_missing("ST4RT+", 4, 5, 8, 6)]),
        existing,
        analysis_width=40,
        analysis_height=24,
        source_width=40,
        source_height=24,
    )
    assert corrections == []
    assert audit.rejected_duplicates[0].duplicate_of == "text_000"
    assert audit.rejected_duplicates[0].reason == "duplicate_existing_ocr"


def test_correction_duplicates_collapse_but_distant_same_text_survives() -> None:
    response = _coverage_payload(
        [
            _missing("42", 14, 14, 5, 5),
            _missing("42+", 14.5, 14.5, 5, 5),
            _missing("42", 31, 15, 5, 5),
        ]
    )
    corrections, audit = normalize_and_deduplicate_corrections(
        response,
        normalize_ocr_candidates(_items()),
        analysis_width=40,
        analysis_height=24,
        source_width=40,
        source_height=24,
    )
    assert [item.id for item in corrections] == ["text_corr_001", "text_corr_002"]
    assert [item.text for item in corrections] == ["42", "42"]
    assert len(audit.rejected_duplicates) == 1
    assert audit.rejected_duplicates[0].reason == "duplicate_correction"


def test_correction_ids_are_independent_of_vlm_response_order() -> None:
    raw = [
        _missing("bottom", 22, 17, 5, 4),
        _missing("top-right", 25, 12, 6, 4),
        _missing("top-left", 14, 12, 6, 4),
    ]

    def normalize(items: list[dict[str, Any]]) -> list[tuple[str, str]]:
        corrections, _ = normalize_and_deduplicate_corrections(
            _coverage_payload(items),
            normalize_ocr_candidates(_items()),
            analysis_width=40,
            analysis_height=24,
            source_width=40,
            source_height=24,
        )
        return [(item.id, item.text) for item in corrections]

    expected = [
        ("text_corr_001", "top-left"),
        ("text_corr_002", "top-right"),
        ("text_corr_003", "bottom"),
    ]
    assert normalize(raw) == expected
    assert normalize(list(reversed(raw))) == expected


def test_correction_normalizes_without_fake_style_and_defaults_to_coarse() -> None:
    corrections, audit = normalize_and_deduplicate_corrections(
        _coverage_payload([_missing("new", 15, 15, 5, 5)]),
        normalize_ocr_candidates(_items()),
        analysis_width=40,
        analysis_height=24,
        source_width=40,
        source_height=24,
    )
    correction = corrections[0]
    assert correction.source == "vlm_correction"
    assert correction.mask_mode == "coarse"
    assert correction.style is None
    assert correction.bbox_analysis == AnalysisBBox(
        x=15,
        y=15,
        width=5,
        height=5,
    )
    assert correction.analysis_image_width == 40
    assert correction.analysis_image_height == 24
    assert audit.accepted_corrections[0].assigned_id == "text_corr_001"


def test_correction_is_merged_before_semantic_classification_and_audited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path, texts_path, raw_mask_path = _write_inputs(tmp_path)
    output_dir = tmp_path / "route-b-v03"
    coverage = {
        "missing_text_candidates": [_missing("NEW", 400, 400, 80, 80)]
    }
    semantic = _payload(
        [
            *_payload()["decisions"],
            {
                "id": "text_corr_001",
                "semantic_role": "runtime_value",
                "confidence": 0.91,
                "reason": "independent runtime information",
            },
        ]
    )
    refined = np.zeros((IMAGE_HEIGHT, IMAGE_WIDTH), dtype=np.uint8)
    refined[16, 16] = 255
    monkeypatch.setattr(
        planner_module,
        "refine_coarse_text_mask_with_diagnostics",
        lambda _image, item: CoarseMaskRefinementResult(
            mask=refined.copy() if item.source == "vlm_correction" else None,
            path=("small_text" if item.source == "vlm_correction" else "failed"),
            failure_reason=(
                None if item.source == "vlm_correction" else "no_reliable_candidate"
            ),
            metrics=None,
        ),
    )
    monkeypatch.setattr(
        UITextExtractor,
        "rebuild_text_mask",
        classmethod(
            lambda cls, image, rect, text: np.zeros(image.shape[:2], dtype=np.uint8)
        ),
    )
    client = FakeVLMClient([coverage, semantic])

    result = UITextRepairPlanner(client=client).process(
        image_path,
        texts_path,
        raw_mask_path,
        output_dir,
        dilation_radius=0,
    )

    correction = next(item for item in result.decisions if item.id == "text_corr_001")
    assert correction.source == "vlm_correction"
    assert correction.mask_mode == "coarse"
    assert correction.mask_quality == "refined"
    assert correction.decision == "remove_for_background_repair"
    assert "text_corr_001" in client.calls[1]["user_prompt"]
    assert "text_corr_001" not in client.calls[0]["user_prompt"]
    union = cv2.imread(
        str(output_dir / "union-text-mask.png"), cv2.IMREAD_GRAYSCALE
    )
    assert union is not None and union[16, 16] == 255
    debug = json.loads(
        (output_dir / "coverage-audit.json").read_text(encoding="utf-8")
    )
    assert debug["accepted_corrections"][0]["assigned_id"] == "text_corr_001"
    assert debug["accepted_corrections"][0]["bbox_analysis"] == {
        "x": 400.0,
        "y": 400.0,
        "width": 80.0,
        "height": 80.0,
    }


def test_correction_must_be_semantically_classified_exactly_once() -> None:
    candidates = normalize_ocr_candidates(_items()) + [
        RepairTextCandidate(
            id="text_corr_001",
            text="NEW",
            rect={"x": 15, "y": 15, "width": 5, "height": 5},
            confidence=0.9,
            source="vlm_correction",
            mask_mode="coarse",
            style=None,
        )
    ]
    with pytest.raises(TextRepairContractError, match="text_corr_001"):
        _validate_complete_classification(
            candidates,
            VLMTextDecisionResponse.model_validate(_payload()),
        )


@pytest.mark.parametrize(
    ("decision", "refine_succeeds", "expected_pixels", "quality"),
    [
        ("remove_for_background_repair", True, 1, "refined"),
        ("remove_for_background_repair", False, 0, "failed"),
        ("preserve_as_visual_asset", True, 0, "failed"),
    ],
)
def test_correction_uses_existing_coarse_refine_without_rectangle_fallback(
    decision: str,
    refine_succeeds: bool,
    expected_pixels: int,
    quality: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = np.zeros((24, 40, 3), dtype=np.uint8)
    candidate = RepairTextCandidate(
        id="text_corr_001",
        text="NEW",
        rect={"x": 15, "y": 15, "width": 5, "height": 5},
        confidence=0.9,
        source="vlm_correction",
        mask_mode="coarse",
        style=None,
    )
    refined = np.zeros((24, 40), dtype=np.uint8)
    refined[16, 16] = 255
    monkeypatch.setattr(
        planner_module,
        "refine_coarse_text_mask_with_diagnostics",
        lambda _image, _item: CoarseMaskRefinementResult(
            mask=refined.copy() if refine_succeeds else None,
            path="small_text" if refine_succeeds else "failed",
            failure_reason=None if refine_succeeds else "no_reliable_candidate",
            metrics=None,
        ),
    )
    role = "runtime_value" if decision == "remove_for_background_repair" else "embedded_logo"
    document = TextRepairDecisionDocument(
        image_width=40,
        image_height=24,
        decisions=[
            TextRepairDecision(
                id=candidate.id,
                text=candidate.text,
                semantic_role=role,
                decision=decision,
                rect=candidate.rect,
                source=candidate.source,
                mask_mode="coarse",
                mask_quality="failed",
                confidence=0.9,
                reason="visual ownership",
            )
        ],
    )
    union, updated = build_union_text_mask(image, [candidate], document)
    assert np.count_nonzero(union) == expected_pixels
    assert updated.decisions[0].mask_quality == quality
    assert np.count_nonzero(union[15:20, 15:20]) != 25


def _small_refinement_candidate(
    text: str = "label",
    *,
    candidate_id: str = "text_corr_900",
    width: int = 14,
    height: int = 14,
    analysis_bbox_width: float = 10.0,
    analysis_bbox_height: float = 14.0,
) -> RepairTextCandidate:
    return RepairTextCandidate(
        id=candidate_id,
        text=text,
        rect={"x": 0, "y": 0, "width": width, "height": height},
        confidence=0.95,
        source="vlm_correction",
        mask_mode="coarse",
        style=None,
        bbox_analysis={
            "x": 100,
            "y": 100,
            "width": analysis_bbox_width,
            "height": analysis_bbox_height,
        },
        analysis_image_width=1024,
        analysis_image_height=461,
    )


def _routing_candidate(
    candidate_id: str,
    *,
    source_width: int,
    source_height: int,
    bbox_analysis_width: float | None = 10.0,
    bbox_analysis_height: float | None = 14.0,
) -> RepairTextCandidate:
    payload: dict[str, Any] = {
        "id": candidate_id,
        "text": "fixture",
        "rect": {
            "x": 100,
            "y": 100,
            "width": source_width,
            "height": source_height,
        },
        "confidence": 0.95,
        "source": "vlm_correction",
        "mask_mode": "coarse",
        "style": None,
    }
    if bbox_analysis_width is not None and bbox_analysis_height is not None:
        payload.update(
            {
                "bbox_analysis": {
                    "x": 100,
                    "y": 100,
                    "width": bbox_analysis_width,
                    "height": bbox_analysis_height,
                },
                "analysis_image_width": 1024,
                "analysis_image_height": 461,
            }
        )
    return RepairTextCandidate.model_validate(payload)


@pytest.mark.parametrize(
    ("candidate_id", "bbox_width", "bbox_height"),
    [
        ("text_corr_003", 29, 45),
        ("text_corr_006", 32, 45),
        ("text_corr_011", 32, 44),
        ("text_corr_013", 32, 44),
        ("text_corr_019", 32, 45),
        ("text_corr_fixture", 33, 45),
    ],
)
def test_real_3200_pilot_bbox_sizes_route_by_analysis_geometry(
    candidate_id: str,
    bbox_width: int,
    bbox_height: int,
) -> None:
    candidate = _routing_candidate(
        candidate_id,
        source_width=bbox_width,
        source_height=bbox_height,
    )
    assert _is_small_text_refinement_eligible(
        candidate,
        source_width=3200,
        source_height=1440,
    )


@pytest.mark.parametrize(
    ("source_width", "source_height", "bbox_width", "bbox_height"),
    [
        (1600, 720, 16, 23),
        (3200, 1440, 32, 45),
        (6400, 2880, 64, 90),
    ],
)
def test_same_analysis_text_size_routes_across_source_resolutions(
    source_width: int,
    source_height: int,
    bbox_width: int,
    bbox_height: int,
) -> None:
    candidate = _routing_candidate(
        "text_corr_scaled",
        source_width=bbox_width,
        source_height=bbox_height,
    )
    assert _is_small_text_refinement_eligible(
        candidate,
        source_width=source_width,
        source_height=source_height,
    )


@pytest.mark.parametrize(
    ("source_width", "source_height", "bbox_width", "bbox_height"),
    [
        (1600, 720, 16, 23),
        (3200, 1440, 32, 45),
        (6400, 2880, 64, 90),
    ],
)
def test_normalized_geometry_fallback_is_resolution_independent(
    source_width: int,
    source_height: int,
    bbox_width: int,
    bbox_height: int,
) -> None:
    candidate = _routing_candidate(
        "text_corr_normalized",
        source_width=bbox_width,
        source_height=bbox_height,
        bbox_analysis_width=None,
        bbox_analysis_height=None,
    )
    assert _is_small_text_refinement_eligible(
        candidate,
        source_width=source_width,
        source_height=source_height,
    )


@pytest.mark.parametrize(
    "candidate_id",
    ["text_corr_002", "text_corr_016", "text_corr_022"],
)
def test_type_b_candidates_receive_secondary_routing_opportunity(
    candidate_id: str,
) -> None:
    candidate = _routing_candidate(
        candidate_id,
        source_width=33,
        source_height=45,
    )
    assert _is_small_text_refinement_eligible(
        candidate,
        source_width=3200,
        source_height=1440,
    )


def test_native_ocr_candidate_never_uses_correction_small_text_routing() -> None:
    ocr_candidate = normalize_ocr_candidates(_items())[0]
    assert not _is_small_text_refinement_eligible(
        ocr_candidate,
        source_width=3200,
        source_height=1440,
    )


def _refinement_image(mask: np.ndarray) -> np.ndarray:
    image = np.full((*mask.shape, 3), 24, dtype=np.uint8)
    image[mask > 0] = [245, 245, 245]
    return image


def _install_extractor_candidate(
    monkeypatch: pytest.MonkeyPatch,
    mask: np.ndarray,
    mode: str = "estimated_glyphs",
) -> None:
    monkeypatch.setattr(
        UITextExtractor,
        "_estimate_background",
        staticmethod(lambda crop, allowed: np.array([24, 24, 24], dtype=np.float32)),
    )
    monkeypatch.setattr(
        UITextExtractor,
        "_extract_glyph_mask",
        classmethod(lambda cls, crop, background, allowed: (mask.copy(), mode)),
    )


@pytest.mark.parametrize("text", ["1", "I", "新", "label"])
def test_small_text_path_is_geometry_driven_for_numeric_and_non_numeric_text(
    text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_mask = np.zeros((14, 14), dtype=np.uint8)
    candidate_mask[3:11, 6:9] = 255
    _install_extractor_candidate(monkeypatch, candidate_mask)

    result = refine_coarse_text_mask_with_diagnostics(
        _refinement_image(candidate_mask),
        _small_refinement_candidate(text),
    )

    assert result.mask is not None
    assert result.path == "small_text"
    assert result.failure_reason is None
    assert result.metrics is not None
    assert result.metrics.bbox_fill_ratio > 0.68


def test_large_text_does_not_enter_small_text_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_mask = np.zeros((14, 41), dtype=np.uint8)
    candidate_mask[3:11, 18:21] = 255
    _install_extractor_candidate(monkeypatch, candidate_mask)

    result = refine_coarse_text_mask_with_diagnostics(
        _refinement_image(candidate_mask),
        _small_refinement_candidate(
            width=41,
            height=14,
            analysis_bbox_width=19,
        ),
    )

    assert result.mask is None
    assert result.path == "failed"
    assert result.failure_reason == "bbox_fill_too_high"


def test_small_narrow_character_can_pass_despite_column_saturation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_mask = np.zeros((14, 14), dtype=np.uint8)
    candidate_mask[:, 6:8] = 255
    _install_extractor_candidate(monkeypatch, candidate_mask)

    result = refine_coarse_text_mask_with_diagnostics(
        _refinement_image(candidate_mask),
        _small_refinement_candidate("!"),
    )

    assert result.mask is not None
    assert result.path == "small_text"
    assert result.metrics is not None
    assert result.metrics.column_saturation_count == 2


def test_background_contaminated_small_candidate_stays_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contaminated = np.zeros((14, 14), dtype=np.uint8)
    contaminated[:12, :] = 255
    _install_extractor_candidate(monkeypatch, contaminated)
    monkeypatch.setattr(
        planner_module,
        "_secondary_small_text_candidates",
        lambda crop, background: [contaminated.copy()],
    )

    result = refine_coarse_text_mask_with_diagnostics(
        _refinement_image(contaminated),
        _small_refinement_candidate(),
    )

    assert result.mask is None
    assert result.path == "failed"
    assert result.failure_reason in {
        "coverage_too_high",
        "post_halo_too_high",
        "background_contamination",
        "no_reliable_candidate",
    }


def test_secondary_segmentation_can_replace_a_contaminated_primary_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contaminated = np.zeros((14, 14), dtype=np.uint8)
    contaminated[:12, :] = 255
    clean = np.zeros((14, 14), dtype=np.uint8)
    clean[3:11, 6:9] = 255
    _install_extractor_candidate(monkeypatch, contaminated)
    monkeypatch.setattr(
        planner_module,
        "_secondary_small_text_candidates",
        lambda crop, background: [clean.copy()],
    )
    image = _refinement_image(clean)

    result = refine_coarse_text_mask_with_diagnostics(
        image,
        _small_refinement_candidate("x"),
    )

    assert result.mask is not None
    assert result.path == "small_text"
    assert result.metrics is not None
    assert result.metrics.coverage < 0.62


def test_secondary_segmentation_failure_does_not_fill_bbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rectangle = np.full((14, 14), 255, dtype=np.uint8)
    _install_extractor_candidate(monkeypatch, rectangle)
    monkeypatch.setattr(
        planner_module,
        "_secondary_small_text_candidates",
        lambda crop, background: [rectangle.copy()],
    )

    result = refine_coarse_text_mask_with_diagnostics(
        _refinement_image(rectangle),
        _small_refinement_candidate(),
    )

    assert result.mask is None
    assert result.path == "failed"


def test_extractor_coarse_fallback_requires_secondary_quality_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rectangle = np.full((14, 14), 255, dtype=np.uint8)
    _install_extractor_candidate(monkeypatch, rectangle, mode="coarse")
    monkeypatch.setattr(
        planner_module,
        "_secondary_small_text_candidates",
        lambda crop, background: [],
    )
    rejected = refine_coarse_text_mask_with_diagnostics(
        _refinement_image(rectangle),
        _small_refinement_candidate(),
    )
    assert rejected.mask is None
    assert rejected.path == "failed"

    clean = np.zeros((14, 14), dtype=np.uint8)
    clean[:, 6:8] = 255
    _install_extractor_candidate(monkeypatch, clean, mode="coarse")
    accepted = refine_coarse_text_mask_with_diagnostics(
        _refinement_image(clean),
        _small_refinement_candidate(),
    )
    assert accepted.mask is not None
    assert accepted.path == "coarse_small_text"


def test_standard_v03_coarse_success_path_does_not_regress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clean = np.zeros((14, 14), dtype=np.uint8)
    clean[4:10, 3:5] = 255
    clean[4:10, 9:11] = 255
    _install_extractor_candidate(monkeypatch, clean)

    result = refine_coarse_text_mask_with_diagnostics(
        _refinement_image(clean),
        _small_refinement_candidate(),
    )

    assert result.mask is not None
    assert result.path == "standard_coarse_refine"
    assert result.failure_reason is None


def _reported_metrics(
    *,
    coverage: float,
    bbox_fill: float,
    contrast: float,
    halo: float,
    rows: int = 0,
    columns: int = 0,
) -> MaskMetrics:
    return MaskMetrics(
        coverage=coverage,
        bbox_fill_ratio=bbox_fill,
        row_saturation_count=rows,
        column_saturation_count=columns,
        median_contrast=contrast,
        post_halo_coverage=halo,
        connected_component_count=1,
        largest_component_ratio=1.0,
        largest_component_roi_coverage=min(coverage, 0.49),
        edge_touch_count=2,
        edge_touching_component_count=1,
        centroid_x_ratio=0.5,
        centroid_y_ratio=0.5,
    )


@pytest.mark.parametrize(
    "metrics",
    [
        _reported_metrics(
            coverage=0.2874,
            bbox_fill=0.6893,
            contrast=257.2,
            halo=0.3548,
        ),
        _reported_metrics(
            coverage=0.3674,
            bbox_fill=0.7287,
            contrast=234.6,
            halo=0.4403,
        ),
        _reported_metrics(
            coverage=0.2685,
            bbox_fill=0.6949,
            contrast=244.3,
            halo=0.3324,
        ),
        _reported_metrics(
            coverage=0.4090,
            bbox_fill=0.4090,
            contrast=75.3,
            halo=0.4917,
            columns=5,
        ),
    ],
)
def test_reported_type_a_metrics_are_not_rejected_by_one_legacy_gate(
    metrics: MaskMetrics,
) -> None:
    assert (
        _small_text_failure_reason(
            metrics,
            coarse_candidate=False,
            roi_width=10,
            roi_height=10,
        )
        is None
    )


@pytest.mark.parametrize(
    "metrics",
    [
        _reported_metrics(
            coverage=0.7868,
            bbox_fill=0.7868,
            contrast=200.0,
            halo=0.8576,
            columns=22,
        ),
        _reported_metrics(
            coverage=0.6626,
            bbox_fill=0.7015,
            contrast=200.0,
            halo=0.7037,
            rows=15,
            columns=6,
        ),
        _reported_metrics(
            coverage=0.7845,
            bbox_fill=0.9957,
            contrast=200.0,
            halo=0.8148,
            columns=25,
        ),
    ],
)
def test_reported_type_b_metrics_still_reject_contaminated_primary_masks(
    metrics: MaskMetrics,
) -> None:
    assert _small_text_failure_reason(
        metrics,
        coarse_candidate=False,
        roi_width=30,
        roi_height=20,
    ) in {
        "coverage_too_high",
        "post_halo_too_high",
        "background_contamination",
    }


def test_reported_type_c_coarse_metrics_can_reach_strict_secondary_validation() -> None:
    metrics = _reported_metrics(
        coverage=0.0803,
        bbox_fill=0.2340,
        contrast=80.0,
        halo=0.20,
    )
    assert (
        _small_text_failure_reason(
            metrics,
            coarse_candidate=True,
            roi_width=14,
            roi_height=14,
        )
        is None
    )


@pytest.mark.parametrize(
    "candidate_id",
    ["text_corr_003", "text_corr_006", "text_corr_011", "text_corr_019"],
)
def test_type_a_regressions_can_use_combined_small_text_quality(
    candidate_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    high_fill_glyph = np.zeros((14, 14), dtype=np.uint8)
    high_fill_glyph[3:11, 6:9] = 255
    _install_extractor_candidate(monkeypatch, high_fill_glyph)
    result = refine_coarse_text_mask_with_diagnostics(
        _refinement_image(high_fill_glyph),
        _small_refinement_candidate(candidate_id=candidate_id),
    )
    assert result.mask is not None
    assert result.path == "small_text"


@pytest.mark.parametrize(
    "candidate_id",
    ["text_corr_002", "text_corr_016", "text_corr_022"],
)
def test_type_b_regressions_never_accept_the_contaminated_primary_mask(
    candidate_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contaminated = np.zeros((14, 14), dtype=np.uint8)
    contaminated[:12, :] = 255
    _install_extractor_candidate(monkeypatch, contaminated)
    monkeypatch.setattr(
        planner_module,
        "_secondary_small_text_candidates",
        lambda crop, background: [],
    )
    result = refine_coarse_text_mask_with_diagnostics(
        _refinement_image(contaminated),
        _small_refinement_candidate(candidate_id=candidate_id),
    )
    assert result.mask is None
    assert result.path == "failed"


def test_type_c_regression_can_validate_clean_extractor_coarse_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coarse_glyph = np.zeros((14, 14), dtype=np.uint8)
    coarse_glyph[:, 6:8] = 255
    _install_extractor_candidate(monkeypatch, coarse_glyph, mode="coarse")
    monkeypatch.setattr(
        planner_module,
        "_secondary_small_text_candidates",
        lambda crop, background: [],
    )
    result = refine_coarse_text_mask_with_diagnostics(
        _refinement_image(coarse_glyph),
        _small_refinement_candidate(candidate_id="text_corr_013"),
    )
    assert result.mask is not None
    assert result.path == "coarse_small_text"


@pytest.mark.parametrize(
    "candidate_id",
    [
        "text_corr_004",
        "text_corr_005",
        "text_corr_007",
        "text_corr_008",
        "text_corr_009",
        "text_corr_012",
        "text_corr_014",
        "text_corr_015",
        "text_corr_017",
        "text_corr_018",
        "text_corr_020",
        "text_corr_021",
    ],
)
def test_known_v03_success_regressions_keep_standard_clean_masks(
    candidate_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clean = np.zeros((14, 14), dtype=np.uint8)
    clean[4:10, 3:5] = 255
    clean[4:10, 9:11] = 255
    _install_extractor_candidate(monkeypatch, clean)
    result = refine_coarse_text_mask_with_diagnostics(
        _refinement_image(clean),
        _small_refinement_candidate(candidate_id=candidate_id),
    )
    assert result.mask is not None
    assert result.path == "standard_coarse_refine"


def test_mask_diagnostics_are_serialized_on_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clean = np.zeros((14, 14), dtype=np.uint8)
    clean[3:11, 6:9] = 255
    _install_extractor_candidate(monkeypatch, clean)
    candidate = _small_refinement_candidate("x")
    document = TextRepairDecisionDocument(
        image_width=14,
        image_height=14,
        decisions=[
            TextRepairDecision(
                id=candidate.id,
                text=candidate.text,
                semantic_role="runtime_value",
                decision="remove_for_background_repair",
                rect=candidate.rect,
                source=candidate.source,
                mask_mode="coarse",
                mask_quality="failed",
                confidence=0.9,
                reason="semantic classification",
            )
        ],
    )

    union, updated = build_union_text_mask(
        _refinement_image(clean),
        [candidate],
        document,
    )
    decision = updated.decisions[0]
    assert np.count_nonzero(union) > 0
    assert decision.mask_quality == "refined"
    assert decision.mask_refinement_path == "small_text"
    assert decision.mask_failure_reason is None
    assert decision.mask_metrics is not None
    assert decision.mask_metrics.connected_component_count == 1


def test_refinement_path_has_no_vlm_ocr_inpaint_or_image_generation_calls() -> None:
    source = inspect.getsource(refine_coarse_text_mask_with_diagnostics)
    source += inspect.getsource(planner_module._secondary_small_text_candidates)
    assert "infer_json" not in source
    assert "_read_stage_a" not in source
    assert "cv2.inpaint" not in source
    assert "image_gen" not in source


def test_planner_has_no_inpaint_or_image_generation_dependency() -> None:
    source = inspect.getsource(planner_module)
    assert "cv2.inpaint" not in source
    assert "image_gen" not in source
    assert "Image 2" not in source
