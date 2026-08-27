#!/usr/bin/env python3
"""Plan Stage 0 Route B text removal masks without repairing the image."""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

import ui_vlm_text_auditor as auditor
from ui_audit_models import Rect, TextItem, TextStyle
from ui_text_extractor import UITextExtractor


SCHEMA_VERSION = "0.4"
Decision = Literal[
    "remove_for_background_repair",
    "preserve_as_visual_asset",
]
SemanticRole = Literal[
    "navigation_label",
    "button_label",
    "runtime_value",
    "body_text",
    "ordinary_title",
    "status_text",
    "embedded_in_artwork",
    "embedded_logo",
    "decorative_art_text",
]
MaskMode = Literal["estimated_glyphs", "coarse"]
MaskQuality = Literal["native", "refined", "failed"]
CandidateSource = Literal["ocr", "vlm_correction"]
MaskRefinementPath = Literal[
    "native",
    "standard_coarse_refine",
    "small_text",
    "coarse_small_text",
    "failed",
]
MaskFailureReason = Literal[
    "invalid_roi",
    "empty_candidate",
    "extractor_coarse_unusable",
    "coverage_too_high",
    "coverage_too_low",
    "bbox_fill_too_high",
    "row_saturation",
    "column_saturation",
    "low_contrast",
    "post_halo_too_high",
    "background_contamination",
    "no_reliable_candidate",
]

# Geometry is authoritative for duplicate suppression. Text is only an extra
# signal for moderately overlapping boxes because OCR spellings are fallible.
DEDUPE_IOU_THRESHOLD = 0.50
DEDUPE_INTERSECTION_OVER_SMALLER_THRESHOLD = 0.80
DEDUPE_CENTER_CONTAINMENT_OVERLAP_THRESHOLD = 0.50
DEDUPE_CENTER_PROXIMITY_RATIO = 0.35
DEDUPE_PROXIMITY_MIN_SIZE_RATIO = 0.50
DEDUPE_TEXT_MATCH_OVERLAP_THRESHOLD = 0.50

# A geometry-only eligibility gate for the correction-specific fallback path.
SMALL_TEXT_MAX_WIDTH = 40
SMALL_TEXT_MAX_HEIGHT = 40
SMALL_TEXT_MAX_AREA = 1200

# Small-text validation deliberately combines these constraints. In particular,
# own-bounds fill and column saturation are not standalone rejection reasons.
SMALL_TEXT_MIN_COVERAGE = 0.015
SMALL_TEXT_MAX_COVERAGE = 0.62
SMALL_TEXT_MIN_MEDIAN_CONTRAST = 12.0
SMALL_TEXT_MAX_POST_HALO_COVERAGE = 0.70
SMALL_TEXT_MAX_BBOX_FILL = 0.92
SMALL_TEXT_MAX_LARGEST_COMPONENT_ROI_COVERAGE = 0.50
SMALL_TEXT_SURFACE_COVERAGE = 0.38
SMALL_TEXT_COARSE_MAX_COVERAGE = 0.45
SMALL_TEXT_COARSE_MAX_POST_HALO_COVERAGE = 0.60
SMALL_TEXT_COARSE_MAX_BBOX_FILL = 0.82
SMALL_TEXT_COMPONENT_MIN_AREA = 1
SMALL_TEXT_COMPONENT_MAX_AREA_RATIO = 0.55
SMALL_TEXT_SECONDARY_DISTANCE_FACTORS = (0.75, 1.0, 1.25)
SMALL_TEXT_COLOR_BIN_SIZE = 16
SMALL_TEXT_MAX_COLOR_CLUSTERS = 4

REMOVE_ROLES = frozenset(
    {
        "navigation_label",
        "button_label",
        "runtime_value",
        "body_text",
        "ordinary_title",
        "status_text",
    }
)
PRESERVE_ROLES = frozenset(
    {
        "embedded_in_artwork",
        "embedded_logo",
        "decorative_art_text",
    }
)
SEMANTIC_ROLE_TO_DECISION: dict[str, Decision] = {
    **{role: "remove_for_background_repair" for role in REMOVE_ROLES},
    **{role: "preserve_as_visual_asset" for role in PRESERVE_ROLES},
}


SYSTEM_PROMPT = """You are reviewing all OCR text in a complete game UI screenshot.
Classify every supplied OCR text ID exactly once into one semantic_role from the
closed schema. Do not make a removal/preservation policy decision yourself.

Classify by visual ownership, not by text meaning alone. Never use the OCR string
as the classification rule. Decide whether each text region belongs to the UI
information layer or belongs to a
visual artwork/asset. Use the complete screenshot, spatial containment, visual
integration, rendering style, and surrounding composition as evidence.

Text belongs to the UI information layer when it functions as independent,
replaceable interface information rendered over the visual design. Use:
- navigation_label: tabs, categories, menus, or navigation labels
- button_label: an ordinary caption rendered over a control surface
- runtime_value: counters, values, timers, quantities, or other dynamic data
- body_text: descriptions, instructions, or ordinary paragraph copy
- ordinary_title: a normal screen, panel, or section title
- status_text: runtime state, availability, ownership, or remaining-time text

Text belongs to a visual artwork/asset when its letterforms are visually fused
with that asset and should remain part of the same bitmap. Use:
- embedded_in_artwork: lettering painted into an icon, illustration, badge,
  texture, or other artwork
- embedded_logo: a logo, brand mark, emblem, or logo-like lettering
- decorative_art_text: display lettering fused with decoration or illustration

Identical OCR strings may have different semantic roles in different locations.
Do not infer visual ownership from vocabulary. A stylized surrounding surface
alone also does not make independent UI information part of an artwork asset.

Return strict JSON matching the supplied schema. Do not return a decision field,
an uncertain/free-text role, omitted IDs, duplicate IDs, or invented IDs."""


COVERAGE_SYSTEM_PROMPT = """Review the complete screenshot and the supplied OCR regions.

Find clearly visible text regions in the screenshot that are not already
represented by any supplied OCR candidate.

This is a coverage task, not a semantic ownership or repair-policy task. Do not
classify semantic roles and do not decide whether text should be removed or
preserved.

Use the screenshot and supplied OCR locations to avoid returning text that is
already covered. Search the complete interface rather than focusing on any
specific component type, screen type, text size, or content category.

Return only genuinely missing visible text regions. Each bbox_analysis is an
axis-aligned rectangle in pixels of the supplied analysis image. Do not return
source-image coordinates, candidate IDs, final decisions, or repair policy."""


class _RepairModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalysisBBox(_RepairModel):
    """Axis-aligned rectangle in analysis-image pixel coordinates."""

    x: float = Field(ge=0.0)
    y: float = Field(ge=0.0)
    width: float = Field(gt=0.0)
    height: float = Field(gt=0.0)

    @model_validator(mode="after")
    def require_finite_values(self) -> AnalysisBBox:
        if not all(
            math.isfinite(value)
            for value in (self.x, self.y, self.width, self.height)
        ):
            raise ValueError("bbox_analysis values must be finite")
        return self


class VLMMissingTextCandidate(_RepairModel):
    """One missing visible text region returned by Coverage Audit."""

    text: str = Field(min_length=1)
    bbox_analysis: AnalysisBBox
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("text")
    @classmethod
    def reject_blank_candidate_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text must not be blank")
        return value


class VLMCoverageAuditResponse(_RepairModel):
    """Strict response contract for the coverage-only VLM pass."""

    missing_text_candidates: list[VLMMissingTextCandidate]


class RepairTextCandidate(_RepairModel):
    """Unified OCR/correction candidate consumed by semantic and mask stages."""

    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    rect: Rect
    confidence: float = Field(ge=0.0, le=1.0)
    source: CandidateSource
    mask_mode: MaskMode
    style: TextStyle | None = None


class AnalysisImageSize(_RepairModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class AcceptedCorrection(_RepairModel):
    text: str
    bbox_analysis: AnalysisBBox
    bbox_source: Rect
    confidence: float = Field(ge=0.0, le=1.0)
    assigned_id: str
    source: Literal["vlm_correction"] = "vlm_correction"


class RejectedDuplicate(_RepairModel):
    text: str
    bbox_analysis: AnalysisBBox
    bbox_source: Rect
    confidence: float = Field(ge=0.0, le=1.0)
    duplicate_of: str
    reason: Literal["duplicate_existing_ocr", "duplicate_correction"]


class CoverageAuditDocument(_RepairModel):
    analysis_image_size: AnalysisImageSize
    raw_missing_candidates: list[VLMMissingTextCandidate]
    accepted_corrections: list[AcceptedCorrection]
    rejected_duplicates: list[RejectedDuplicate]


class VLMTextDecision(_RepairModel):
    """One visual-ownership classification returned directly by the VLM."""

    id: str = Field(min_length=1)
    semantic_role: SemanticRole
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)

    @field_validator("id", "reason")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


class VLMTextDecisionResponse(_RepairModel):
    """Complete compact response requested from the VLM."""

    decisions: list[VLMTextDecision]


class MaskMetrics(_RepairModel):
    """Serializable local-mask evidence used by refinement diagnostics."""

    coverage: float = Field(ge=0.0, le=1.0)
    bbox_fill_ratio: float = Field(ge=0.0, le=1.0)
    row_saturation_count: int = Field(ge=0)
    column_saturation_count: int = Field(ge=0)
    median_contrast: float = Field(ge=0.0)
    post_halo_coverage: float = Field(ge=0.0, le=1.0)
    connected_component_count: int = Field(ge=0)
    largest_component_ratio: float = Field(ge=0.0, le=1.0)
    largest_component_roi_coverage: float = Field(ge=0.0, le=1.0)
    edge_touch_count: int = Field(ge=0, le=4)
    edge_touching_component_count: int = Field(ge=0)
    centroid_x_ratio: float = Field(ge=0.0, le=1.0)
    centroid_y_ratio: float = Field(ge=0.0, le=1.0)


class TextRepairDecision(_RepairModel):
    """One authoritative, source-geometry-backed Route B decision."""

    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    semantic_role: SemanticRole
    decision: Decision
    rect: Rect
    source: CandidateSource = "ocr"
    mask_mode: MaskMode
    mask_quality: MaskQuality
    mask_refinement_path: MaskRefinementPath = "failed"
    mask_failure_reason: MaskFailureReason | None = None
    mask_metrics: MaskMetrics | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)


class TextRepairDecisionDocument(_RepairModel):
    """The public text-repair-decisions.json v0.4 contract."""

    schema_version: Literal["0.4"] = SCHEMA_VERSION
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    decisions: list[TextRepairDecision]


class TextRepairPlannerError(RuntimeError):
    """Base Route B planner failure."""


class TextRepairContractError(TextRepairPlannerError):
    """Raised when either VLM stage violates its strict response contract."""


def _strict_model_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = copy.deepcopy(model.model_json_schema())

    def make_strict(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                properties = node.get("properties", {})
                node["additionalProperties"] = False
                node["required"] = list(properties)
            node.pop("default", None)
            for value in node.values():
                make_strict(value)
        elif isinstance(node, list):
            for value in node:
                make_strict(value)

    make_strict(schema)
    return schema


def _strict_response_schema() -> dict[str, Any]:
    return _strict_model_schema(VLMTextDecisionResponse)


def _strict_coverage_response_schema() -> dict[str, Any]:
    return _strict_model_schema(VLMCoverageAuditResponse)


def normalize_ocr_candidates(texts: list[TextItem]) -> list[RepairTextCandidate]:
    """Convert Stage A OCR items into the planner's unified candidate model."""

    return [
        RepairTextCandidate(
            id=item.id,
            text=item.text,
            rect=item.rect,
            confidence=item.confidence,
            source="ocr",
            mask_mode=item.mask_mode,
            style=item.style,
        )
        for item in texts
    ]


def _source_rect_to_analysis_bbox(
    rect: Rect,
    *,
    source_width: int,
    source_height: int,
    analysis_width: int,
    analysis_height: int,
) -> AnalysisBBox:
    """Map a source rectangle outward to the prepared analysis image."""

    scale_x = analysis_width / source_width
    scale_y = analysis_height / source_height
    x0 = max(0, min(analysis_width - 1, math.floor(rect.x * scale_x)))
    y0 = max(0, min(analysis_height - 1, math.floor(rect.y * scale_y)))
    x1 = max(x0 + 1, min(analysis_width, math.ceil((rect.x + rect.width) * scale_x)))
    y1 = max(y0 + 1, min(analysis_height, math.ceil((rect.y + rect.height) * scale_y)))
    return AnalysisBBox(x=x0, y=y0, width=x1 - x0, height=y1 - y0)


def validate_and_map_analysis_bbox(
    bbox: AnalysisBBox,
    *,
    analysis_width: int,
    analysis_height: int,
    source_width: int,
    source_height: int,
) -> Rect:
    """Validate an analysis bbox and map it outward to source pixels.

    Analysis boxes are rejected when any edge is outside the analysis image.
    Source mapping uses floor for the top/left edge, ceil for bottom/right, then
    clamps each source edge to the inclusive image extent [0, width/height].
    """

    if bbox.x + bbox.width > analysis_width or bbox.y + bbox.height > analysis_height:
        raise TextRepairContractError(
            "bbox_analysis must be fully contained in the analysis image"
        )
    scale_x = source_width / analysis_width
    scale_y = source_height / analysis_height
    x0 = max(0, min(source_width - 1, math.floor(bbox.x * scale_x)))
    y0 = max(0, min(source_height - 1, math.floor(bbox.y * scale_y)))
    x1 = max(x0 + 1, min(source_width, math.ceil((bbox.x + bbox.width) * scale_x)))
    y1 = max(y0 + 1, min(source_height, math.ceil((bbox.y + bbox.height) * scale_y)))
    return Rect(x=x0, y=y0, width=x1 - x0, height=y1 - y0)


def _compact_coverage_candidates(
    candidates: list[RepairTextCandidate],
    *,
    source_width: int,
    source_height: int,
    analysis_width: int,
    analysis_height: int,
) -> str:
    rows = [
        {
            "id": candidate.id,
            "text": candidate.text,
            "bbox_analysis": _source_rect_to_analysis_bbox(
                candidate.rect,
                source_width=source_width,
                source_height=source_height,
                analysis_width=analysis_width,
                analysis_height=analysis_height,
            ).model_dump(mode="json"),
        }
        for candidate in candidates
    ]
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def _rect_intersection_area(left: Rect, right: Rect) -> int:
    width = max(
        0,
        min(left.x + left.width, right.x + right.width) - max(left.x, right.x),
    )
    height = max(
        0,
        min(left.y + left.height, right.y + right.height) - max(left.y, right.y),
    )
    return width * height


def _normalized_text(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _is_duplicate_rect(
    candidate: Rect,
    existing: Rect,
    *,
    candidate_text: str,
    existing_text: str,
) -> bool:
    intersection = _rect_intersection_area(candidate, existing)
    candidate_area = candidate.width * candidate.height
    existing_area = existing.width * existing.height
    union = candidate_area + existing_area - intersection
    iou = intersection / union if union else 0.0
    overlap_smaller = intersection / min(candidate_area, existing_area)
    if iou >= DEDUPE_IOU_THRESHOLD:
        return True
    if overlap_smaller >= DEDUPE_INTERSECTION_OVER_SMALLER_THRESHOLD:
        return True

    candidate_center = (
        candidate.x + candidate.width / 2.0,
        candidate.y + candidate.height / 2.0,
    )
    existing_center = (
        existing.x + existing.width / 2.0,
        existing.y + existing.height / 2.0,
    )
    candidate_contains_existing_center = (
        candidate.x <= existing_center[0] <= candidate.x + candidate.width
        and candidate.y <= existing_center[1] <= candidate.y + candidate.height
    )
    existing_contains_candidate_center = (
        existing.x <= candidate_center[0] <= existing.x + existing.width
        and existing.y <= candidate_center[1] <= existing.y + existing.height
    )
    if (
        candidate_contains_existing_center or existing_contains_candidate_center
    ) and overlap_smaller >= DEDUPE_CENTER_CONTAINMENT_OVERLAP_THRESHOLD:
        return True

    center_distance = math.dist(candidate_center, existing_center)
    min_diagonal = min(
        math.hypot(candidate.width, candidate.height),
        math.hypot(existing.width, existing.height),
    )
    width_ratio = min(candidate.width, existing.width) / max(
        candidate.width, existing.width
    )
    height_ratio = min(candidate.height, existing.height) / max(
        candidate.height, existing.height
    )
    if (
        center_distance <= DEDUPE_CENTER_PROXIMITY_RATIO * min_diagonal
        and width_ratio >= DEDUPE_PROXIMITY_MIN_SIZE_RATIO
        and height_ratio >= DEDUPE_PROXIMITY_MIN_SIZE_RATIO
    ):
        return True

    normalized_candidate = _normalized_text(candidate_text)
    normalized_existing = _normalized_text(existing_text)
    return bool(
        normalized_candidate
        and normalized_candidate == normalized_existing
        and overlap_smaller >= DEDUPE_TEXT_MATCH_OVERLAP_THRESHOLD
    )


def normalize_and_deduplicate_corrections(
    response: VLMCoverageAuditResponse,
    existing_candidates: list[RepairTextCandidate],
    *,
    analysis_width: int,
    analysis_height: int,
    source_width: int,
    source_height: int,
) -> tuple[list[RepairTextCandidate], CoverageAuditDocument]:
    """Map, dedupe, stably identify, and normalize VLM corrections."""

    mapped = [
        (
            item,
            validate_and_map_analysis_bbox(
                item.bbox_analysis,
                analysis_width=analysis_width,
                analysis_height=analysis_height,
                source_width=source_width,
                source_height=source_height,
            ),
        )
        for item in response.missing_text_candidates
    ]
    mapped.sort(
        key=lambda pair: (
            pair[1].y,
            pair[1].x,
            _normalized_text(pair[0].text),
            pair[0].text.casefold(),
            pair[1].width,
            pair[1].height,
            -pair[0].confidence,
        )
    )

    accepted: list[tuple[VLMMissingTextCandidate, Rect]] = []
    rejected: list[
        tuple[VLMMissingTextCandidate, Rect, str | int, Literal[
            "duplicate_existing_ocr", "duplicate_correction"
        ]]
    ] = []
    for missing, source_rect in mapped:
        duplicate_ocr = next(
            (
                candidate
                for candidate in sorted(existing_candidates, key=lambda item: item.id)
                if _is_duplicate_rect(
                    source_rect,
                    candidate.rect,
                    candidate_text=missing.text,
                    existing_text=candidate.text,
                )
            ),
            None,
        )
        if duplicate_ocr is not None:
            rejected.append(
                (missing, source_rect, duplicate_ocr.id, "duplicate_existing_ocr")
            )
            continue

        duplicate_index = next(
            (
                index
                for index, (accepted_item, accepted_rect) in enumerate(accepted)
                if _is_duplicate_rect(
                    source_rect,
                    accepted_rect,
                    candidate_text=missing.text,
                    existing_text=accepted_item.text,
                )
            ),
            None,
        )
        if duplicate_index is not None:
            rejected.append(
                (missing, source_rect, duplicate_index, "duplicate_correction")
            )
            continue
        accepted.append((missing, source_rect))

    corrections: list[RepairTextCandidate] = []
    accepted_records: list[AcceptedCorrection] = []
    accepted_ids = [f"text_corr_{index:03d}" for index in range(1, len(accepted) + 1)]
    for assigned_id, (missing, source_rect) in zip(
        accepted_ids, accepted, strict=True
    ):
        corrections.append(
            RepairTextCandidate(
                id=assigned_id,
                text=missing.text,
                rect=source_rect,
                confidence=missing.confidence,
                source="vlm_correction",
                mask_mode="coarse",
                style=None,
            )
        )
        accepted_records.append(
            AcceptedCorrection(
                text=missing.text,
                bbox_analysis=missing.bbox_analysis,
                bbox_source=source_rect,
                confidence=missing.confidence,
                assigned_id=assigned_id,
            )
        )

    rejected_records = [
        RejectedDuplicate(
            text=missing.text,
            bbox_analysis=missing.bbox_analysis,
            bbox_source=source_rect,
            confidence=missing.confidence,
            duplicate_of=(
                accepted_ids[duplicate_of]
                if isinstance(duplicate_of, int)
                else duplicate_of
            ),
            reason=reason,
        )
        for missing, source_rect, duplicate_of, reason in rejected
    ]
    audit_document = CoverageAuditDocument(
        analysis_image_size=AnalysisImageSize(
            width=analysis_width,
            height=analysis_height,
        ),
        raw_missing_candidates=response.missing_text_candidates,
        accepted_corrections=accepted_records,
        rejected_duplicates=rejected_records,
    )
    return corrections, audit_document


def _validate_complete_classification(
    texts: list[RepairTextCandidate] | list[TextItem],
    response: VLMTextDecisionResponse,
) -> dict[str, VLMTextDecision]:
    """Require an exact one-to-one classification of every merged candidate."""

    input_ids = [item.id for item in texts]
    classified_ids = [item.id for item in response.decisions]
    duplicate_ids = sorted(
        text_id for text_id, count in Counter(classified_ids).items() if count > 1
    )
    if duplicate_ids:
        raise TextRepairContractError(
            f"Duplicate text candidate classifications: {duplicate_ids}"
        )

    input_id_set = set(input_ids)
    classified_id_set = set(classified_ids)
    unknown_ids = sorted(classified_id_set - input_id_set)
    if unknown_ids:
        raise TextRepairContractError(
            f"VLM returned unknown text candidate IDs: {unknown_ids}"
        )
    missing_ids = sorted(input_id_set - classified_id_set)
    if missing_ids:
        raise TextRepairContractError(
            f"VLM omitted text candidate IDs: {missing_ids}"
        )
    if classified_id_set != input_id_set:
        raise TextRepairContractError(
            "classified_ids must exactly equal all_candidate_ids"
        )
    return {item.id: item for item in response.decisions}


def decision_for_semantic_role(semantic_role: SemanticRole) -> Decision:
    """Map a VLM-owned semantic role to the fixed engineering policy."""

    try:
        return SEMANTIC_ROLE_TO_DECISION[semantic_role]
    except KeyError as exc:  # Defensive if this function is called without Pydantic.
        raise TextRepairContractError(
            f"No deterministic decision mapping for semantic_role={semantic_role!r}"
        ) from exc


def _style_rgb(item: RepairTextCandidate | TextItem) -> np.ndarray | None:
    if getattr(item, "style", None) is None:
        return None
    value = item.style.color.removeprefix("#")
    return np.array(
        [int(value[index : index + 2], 16) for index in (0, 2, 4)],
        dtype=np.float32,
    )


def _is_reliable_local_glyph_mask(
    mask: np.ndarray,
    crop_rgb: np.ndarray,
    background_rgb: np.ndarray,
) -> bool:
    """Reject empty, low-signal, and rectangle-like coarse-mask candidates."""

    if mask.shape != crop_rgb.shape[:2] or mask.dtype != np.uint8:
        return False
    selected = mask > 0
    pixel_count = int(np.count_nonzero(selected))
    if pixel_count == 0:
        return False
    coverage = pixel_count / float(mask.size)
    if coverage < UITextExtractor.MIN_GLYPH_COVERAGE or coverage > 0.45:
        return False

    ys, xs = np.where(selected)
    bounds_area = int((ys.max() - ys.min() + 1) * (xs.max() - xs.min() + 1))
    if bounds_area <= 0 or pixel_count / float(bounds_area) > 0.68:
        return False

    row_coverage = np.count_nonzero(selected, axis=1) / max(1, mask.shape[1])
    column_coverage = np.count_nonzero(selected, axis=0) / max(1, mask.shape[0])
    if np.count_nonzero(row_coverage >= 0.90) >= max(2, round(0.15 * mask.shape[0])):
        return False
    if np.count_nonzero(column_coverage >= 0.90) >= max(2, round(0.15 * mask.shape[1])):
        return False

    background_distance = np.linalg.norm(
        crop_rgb.astype(np.float32) - background_rgb.reshape(1, 1, 3),
        axis=2,
    )
    if float(np.median(background_distance[selected])) < 12.0:
        return False
    return True


@dataclass(frozen=True)
class CoarseMaskRefinementResult:
    mask: np.ndarray | None
    path: MaskRefinementPath
    failure_reason: MaskFailureReason | None
    metrics: MaskMetrics | None


def _is_small_text_refinement_eligible(
    item: RepairTextCandidate | TextItem,
) -> bool:
    """Use source and pixel geometry only; never inspect text content or scene."""

    return bool(
        getattr(item, "source", "ocr") == "vlm_correction"
        and item.mask_mode == "coarse"
        and item.rect.width <= SMALL_TEXT_MAX_WIDTH
        and item.rect.height <= SMALL_TEXT_MAX_HEIGHT
        and item.rect.width * item.rect.height <= SMALL_TEXT_MAX_AREA
    )


def _measure_local_mask(
    mask: np.ndarray,
    crop_rgb: np.ndarray,
    background_rgb: np.ndarray,
) -> MaskMetrics | None:
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    selected = binary > 0
    pixel_count = int(np.count_nonzero(selected))
    if pixel_count == 0:
        return None

    height, width = binary.shape
    ys, xs = np.where(selected)
    bounds_area = int((ys.max() - ys.min() + 1) * (xs.max() - xs.min() + 1))
    row_coverage = np.count_nonzero(selected, axis=1) / max(1, width)
    column_coverage = np.count_nonzero(selected, axis=0) / max(1, height)
    row_saturation_count = int(np.count_nonzero(row_coverage >= 0.90))
    column_saturation_count = int(np.count_nonzero(column_coverage >= 0.90))
    background_distance = np.linalg.norm(
        crop_rgb.astype(np.float32) - background_rgb.reshape(1, 1, 3),
        axis=2,
    )
    halo = cv2.dilate(
        binary,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
        iterations=1,
    )

    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(
        binary,
        connectivity=8,
    )
    component_areas = stats[1:, cv2.CC_STAT_AREA].astype(np.int64)
    largest_area = int(component_areas.max()) if component_areas.size else 0
    edge_touching_components = 0
    for label in range(1, component_count):
        component = labels == label
        component_ys, component_xs = np.where(component)
        if (
            np.any(component_ys == 0)
            or np.any(component_ys == height - 1)
            or np.any(component_xs == 0)
            or np.any(component_xs == width - 1)
        ):
            edge_touching_components += 1

    edge_touch_count = sum(
        (
            bool(np.any(selected[0, :])),
            bool(np.any(selected[-1, :])),
            bool(np.any(selected[:, 0])),
            bool(np.any(selected[:, -1])),
        )
    )
    return MaskMetrics(
        coverage=pixel_count / float(binary.size),
        bbox_fill_ratio=pixel_count / float(bounds_area),
        row_saturation_count=row_saturation_count,
        column_saturation_count=column_saturation_count,
        median_contrast=float(np.median(background_distance[selected])),
        post_halo_coverage=np.count_nonzero(halo) / float(halo.size),
        connected_component_count=component_count - 1,
        largest_component_ratio=largest_area / float(pixel_count),
        largest_component_roi_coverage=largest_area / float(binary.size),
        edge_touch_count=edge_touch_count,
        edge_touching_component_count=edge_touching_components,
        centroid_x_ratio=float(xs.mean()) / max(1, width - 1),
        centroid_y_ratio=float(ys.mean()) / max(1, height - 1),
    )


def _standard_failure_reason(
    metrics: MaskMetrics | None,
    *,
    roi_width: int,
    roi_height: int,
) -> MaskFailureReason:
    if metrics is None:
        return "empty_candidate"
    if metrics.coverage < UITextExtractor.MIN_GLYPH_COVERAGE:
        return "coverage_too_low"
    if metrics.coverage > 0.45:
        return "coverage_too_high"
    if metrics.bbox_fill_ratio > 0.68:
        return "bbox_fill_too_high"
    if metrics.row_saturation_count >= max(2, round(0.15 * roi_height)):
        return "row_saturation"
    if metrics.column_saturation_count >= max(2, round(0.15 * roi_width)):
        return "column_saturation"
    if metrics.median_contrast < 12.0:
        return "low_contrast"
    if metrics.post_halo_coverage > 0.55:
        return "post_halo_too_high"
    return "no_reliable_candidate"


def _small_text_failure_reason(
    metrics: MaskMetrics | None,
    *,
    coarse_candidate: bool,
    roi_width: int,
    roi_height: int,
) -> MaskFailureReason | None:
    if metrics is None or metrics.connected_component_count == 0:
        return "empty_candidate"
    if metrics.coverage < SMALL_TEXT_MIN_COVERAGE:
        return "coverage_too_low"

    max_coverage = (
        SMALL_TEXT_COARSE_MAX_COVERAGE
        if coarse_candidate
        else SMALL_TEXT_MAX_COVERAGE
    )
    max_halo = (
        SMALL_TEXT_COARSE_MAX_POST_HALO_COVERAGE
        if coarse_candidate
        else SMALL_TEXT_MAX_POST_HALO_COVERAGE
    )
    max_fill = (
        SMALL_TEXT_COARSE_MAX_BBOX_FILL
        if coarse_candidate
        else SMALL_TEXT_MAX_BBOX_FILL
    )
    if metrics.coverage > max_coverage:
        return "coverage_too_high"
    if metrics.post_halo_coverage > max_halo:
        return "post_halo_too_high"
    if metrics.median_contrast < SMALL_TEXT_MIN_MEDIAN_CONTRAST:
        return "low_contrast"
    if (
        metrics.largest_component_roi_coverage
        > SMALL_TEXT_MAX_LARGEST_COMPONENT_ROI_COVERAGE
    ):
        return "background_contamination"
    if metrics.bbox_fill_ratio > max_fill and (
        metrics.coverage >= SMALL_TEXT_SURFACE_COVERAGE
        or metrics.edge_touch_count >= 3
    ):
        return "bbox_fill_too_high"

    row_limit = max(2, round(0.35 * roi_height))
    column_limit = max(2, round(0.35 * roi_width))
    row_saturated = metrics.row_saturation_count >= row_limit
    column_saturated = metrics.column_saturation_count >= column_limit
    if row_saturated and column_saturated:
        return "background_contamination"
    if (
        metrics.edge_touch_count >= 3
        and metrics.coverage >= SMALL_TEXT_SURFACE_COVERAGE
        and metrics.edge_touching_component_count > 0
    ):
        return "background_contamination"
    if row_saturated and metrics.coverage > 0.50:
        return "row_saturation"
    if column_saturated and metrics.coverage > 0.55:
        return "column_saturation"
    return None


def _filter_small_text_components(
    mask: np.ndarray,
    crop_rgb: np.ndarray,
    background_rgb: np.ndarray,
) -> np.ndarray:
    """Remove large, low-contrast, multi-edge surface components."""

    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        binary,
        connectivity=8,
    )
    result = np.zeros_like(binary)
    roi_area = binary.size
    background_distance = np.linalg.norm(
        crop_rgb.astype(np.float32) - background_rgb.reshape(1, 1, 3),
        axis=2,
    )
    height, width = binary.shape
    for label in range(1, count):
        component = labels == label
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < SMALL_TEXT_COMPONENT_MIN_AREA:
            continue
        if area / float(roi_area) > SMALL_TEXT_COMPONENT_MAX_AREA_RATIO:
            continue
        x = int(stats[label, cv2.CC_STAT_LEFT])
        y = int(stats[label, cv2.CC_STAT_TOP])
        component_width = int(stats[label, cv2.CC_STAT_WIDTH])
        component_height = int(stats[label, cv2.CC_STAT_HEIGHT])
        touched_edges = sum(
            (
                x == 0,
                y == 0,
                x + component_width == width,
                y + component_height == height,
            )
        )
        if touched_edges >= 3 and area / float(roi_area) > 0.15:
            continue
        if float(np.median(background_distance[component])) < 10.0:
            continue
        result[component] = 255
    return result


def _secondary_small_text_candidates(
    crop_rgb: np.ndarray,
    background_rgb: np.ndarray,
) -> list[np.ndarray]:
    """Build a small deterministic set of color/contrast candidates."""

    candidates: list[np.ndarray] = []
    background_distance = np.linalg.norm(
        crop_rgb.astype(np.float32) - background_rgb.reshape(1, 1, 3),
        axis=2,
    )
    distance_u8 = np.clip(background_distance, 0, 255).astype(np.uint8)
    otsu_threshold, _ = cv2.threshold(
        distance_u8,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )
    for factor in SMALL_TEXT_SECONDARY_DISTANCE_FACTORS:
        threshold = max(12.0, float(otsu_threshold) * factor)
        candidates.append(
            np.where(background_distance >= threshold, 255, 0).astype(np.uint8)
        )

    lab = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2LAB)
    quantized = (lab // SMALL_TEXT_COLOR_BIN_SIZE).reshape(-1, 3)
    keys, inverse, counts = np.unique(
        quantized,
        axis=0,
        return_inverse=True,
        return_counts=True,
    )
    del keys
    height, width = crop_rgb.shape[:2]
    border = np.zeros((height, width), dtype=bool)
    border[0, :] = border[-1, :] = True
    border[:, 0] = border[:, -1] = True
    border_labels = inverse.reshape(height, width)[border]
    ranked_clusters = sorted(
        range(len(counts)),
        key=lambda index: (int(counts[index]), index),
    )
    added_clusters = 0
    for cluster in ranked_clusters:
        cluster_mask = inverse.reshape(height, width) == cluster
        area_ratio = int(counts[cluster]) / float(cluster_mask.size)
        if area_ratio < SMALL_TEXT_MIN_COVERAGE or area_ratio > 0.45:
            continue
        border_ratio = np.count_nonzero(border_labels == cluster) / float(
            max(1, border_labels.size)
        )
        if border_ratio > 0.25:
            continue
        if float(np.median(background_distance[cluster_mask])) < 12.0:
            continue
        candidate = np.where(cluster_mask, 255, 0).astype(np.uint8)
        candidate = cv2.morphologyEx(
            candidate,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)),
        )
        candidates.append(candidate)
        added_clusters += 1
        if added_clusters >= SMALL_TEXT_MAX_COLOR_CLUSTERS:
            break

    gray = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    border_median = float(np.median(gray[border]))
    candidates.append(np.where(gray >= border_median + 24.0, 255, 0).astype(np.uint8))
    candidates.append(np.where(gray <= border_median - 24.0, 255, 0).astype(np.uint8))

    unique: list[np.ndarray] = []
    seen: set[bytes] = set()
    for candidate in candidates:
        key = np.packbits(candidate > 0).tobytes()
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _small_candidate_score(metrics: MaskMetrics) -> float:
    return (
        min(metrics.median_contrast, 255.0)
        + 30.0 * (1.0 - metrics.post_halo_coverage)
        + 10.0 * min(metrics.connected_component_count, 4)
        - 50.0 * metrics.largest_component_roi_coverage
    )


def refine_coarse_text_mask_with_diagnostics(
    original_image: np.ndarray,
    item: RepairTextCandidate | TextItem,
) -> CoarseMaskRefinementResult:
    """Extract a conservative glyph mask and explain the selected path.

    The frozen standard gate is tried first. Only a geometry-eligible VLM
    correction may proceed to connected-component filtering and deterministic
    secondary segmentation. No path accepts a filled bbox as a fallback.
    """

    rect = item.rect
    crop_rgb = original_image[
        rect.y : rect.y + rect.height,
        rect.x : rect.x + rect.width,
    ]
    if crop_rgb.shape[:2] != (rect.height, rect.width) or crop_rgb.size == 0:
        return CoarseMaskRefinementResult(
            mask=None,
            path="failed",
            failure_reason="invalid_roi",
            metrics=None,
        )

    allowed = np.full(crop_rgb.shape[:2], 255, dtype=np.uint8)
    background = UITextExtractor._estimate_background(crop_rgb, allowed)
    extracted, extracted_mode = UITextExtractor._extract_glyph_mask(
        crop_rgb,
        background,
        allowed,
    )

    background_distance = np.linalg.norm(
        crop_rgb.astype(np.float32) - background.reshape(1, 1, 3),
        axis=2,
    )
    style_mask = np.zeros(crop_rgb.shape[:2], dtype=np.uint8)
    target = _style_rgb(item)
    if target is not None:
        target_distance = np.linalg.norm(
            crop_rgb.astype(np.float32) - target.reshape(1, 1, 3),
            axis=2,
        )
        target_background_contrast = float(np.linalg.norm(target - background))
    else:
        target_distance = None
        target_background_contrast = 0.0
    if target_distance is not None and target_background_contrast >= 18.0:
        color_tolerance = float(np.clip(0.30 * target_background_contrast, 28, 64))
        style_mask[
            (target_distance <= color_tolerance) & (background_distance >= 14.0)
        ] = 255
        style_mask = cv2.morphologyEx(
            style_mask,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)),
        )

    standard_candidates: list[np.ndarray] = []
    if extracted_mode == "estimated_glyphs" and np.any(style_mask):
        style_neighborhood = cv2.dilate(
            style_mask,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
            iterations=1,
        )
        standard_candidates.append(cv2.bitwise_and(extracted, style_neighborhood))
    if np.any(style_mask):
        standard_candidates.append(style_mask)
    if extracted_mode == "estimated_glyphs":
        standard_candidates.append(extracted)

    observed_standard: list[tuple[MaskMetrics, MaskFailureReason]] = []
    for candidate in standard_candidates:
        candidate = np.where(candidate > 0, 255, 0).astype(np.uint8)
        metrics = _measure_local_mask(candidate, crop_rgb, background)
        if not _is_reliable_local_glyph_mask(candidate, crop_rgb, background):
            if metrics is not None:
                observed_standard.append(
                    (
                        metrics,
                        _standard_failure_reason(
                            metrics,
                            roi_width=rect.width,
                            roi_height=rect.height,
                        ),
                    )
                )
            continue
        refined = cv2.dilate(
            candidate,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
            iterations=1,
        )
        refined_coverage = np.count_nonzero(refined) / float(refined.size)
        if refined_coverage > 0.55:
            if metrics is not None:
                observed_standard.append((metrics, "post_halo_too_high"))
            continue
        full_mask = np.zeros(original_image.shape[:2], dtype=np.uint8)
        full_mask[
            rect.y : rect.y + rect.height,
            rect.x : rect.x + rect.width,
        ] = refined
        return CoarseMaskRefinementResult(
            mask=full_mask,
            path="standard_coarse_refine",
            failure_reason=None,
            metrics=metrics,
        )

    if not _is_small_text_refinement_eligible(item):
        if observed_standard:
            metrics, reason = max(
                observed_standard,
                key=lambda result: _small_candidate_score(result[0]),
            )
        else:
            metrics = _measure_local_mask(extracted, crop_rgb, background)
            reason = (
                "extractor_coarse_unusable"
                if extracted_mode == "coarse"
                else _standard_failure_reason(
                    metrics,
                    roi_width=rect.width,
                    roi_height=rect.height,
                )
            )
        return CoarseMaskRefinementResult(
            mask=None,
            path="failed",
            failure_reason=reason,
            metrics=metrics,
        )

    small_candidates: list[tuple[str, np.ndarray]] = [
        ("standard", candidate) for candidate in standard_candidates
    ]
    if extracted_mode == "coarse":
        small_candidates.append(("extractor_coarse", extracted))
    small_candidates.extend(
        ("secondary", candidate)
        for candidate in _secondary_small_text_candidates(crop_rgb, background)
    )

    accepted: list[tuple[float, str, np.ndarray, MaskMetrics]] = []
    rejected: list[tuple[float, MaskFailureReason, MaskMetrics]] = []
    for origin, candidate in small_candidates:
        raw_metrics = _measure_local_mask(candidate, crop_rgb, background)
        filtered = _filter_small_text_components(candidate, crop_rgb, background)
        metrics = _measure_local_mask(filtered, crop_rgb, background)
        if metrics is None and raw_metrics is not None:
            raw_reason = _small_text_failure_reason(
                raw_metrics,
                coarse_candidate=origin == "extractor_coarse",
                roi_width=rect.width,
                roi_height=rect.height,
            )
            rejected.append(
                (
                    _small_candidate_score(raw_metrics),
                    raw_reason or "background_contamination",
                    raw_metrics,
                )
            )
            continue
        reason = _small_text_failure_reason(
            metrics,
            coarse_candidate=origin == "extractor_coarse",
            roi_width=rect.width,
            roi_height=rect.height,
        )
        if metrics is None:
            continue
        score = _small_candidate_score(metrics)
        if reason is not None:
            rejected.append((score, reason, metrics))
            continue
        accepted.append((score, origin, filtered, metrics))

    if accepted:
        _, origin, candidate, metrics = max(accepted, key=lambda result: result[0])
        refined = cv2.dilate(
            candidate,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
            iterations=1,
        )
        full_mask = np.zeros(original_image.shape[:2], dtype=np.uint8)
        full_mask[
            rect.y : rect.y + rect.height,
            rect.x : rect.x + rect.width,
        ] = refined
        return CoarseMaskRefinementResult(
            mask=full_mask,
            path=(
                "coarse_small_text"
                if origin == "extractor_coarse"
                else "small_text"
            ),
            failure_reason=None,
            metrics=metrics,
        )

    if rejected:
        _, reason, metrics = max(rejected, key=lambda result: result[0])
    else:
        reason = (
            "extractor_coarse_unusable"
            if extracted_mode == "coarse"
            else "no_reliable_candidate"
        )
        metrics = None
    return CoarseMaskRefinementResult(
        mask=None,
        path="failed",
        failure_reason=reason,
        metrics=metrics,
    )


def refine_coarse_text_mask(
    original_image: np.ndarray,
    item: RepairTextCandidate | TextItem,
) -> np.ndarray | None:
    """Compatibility wrapper returning only the full-image refined mask."""

    return refine_coarse_text_mask_with_diagnostics(original_image, item).mask


def build_union_text_mask(
    original_image: np.ndarray,
    texts: list[RepairTextCandidate] | list[TextItem],
    document: TextRepairDecisionDocument,
) -> tuple[np.ndarray, TextRepairDecisionDocument]:
    """Positively OR rebuilt masks for remove decisions into an empty mask.

    ``UITextExtractor.rebuild_text_mask`` already applies its adaptive Stage A
    glyph dilation. This output is therefore the selected-text baseline mask;
    the separate repair expansion is applied only by ``expand_repair_mask``.
    The all-text ``raw_text_mask`` is deliberately not sliced or subtracted.
    """

    item_by_id = {item.id: item for item in texts}
    union_mask = np.zeros(original_image.shape[:2], dtype=np.uint8)
    updated_decisions: list[TextRepairDecision] = []
    for decision in document.decisions:
        if decision.decision != "remove_for_background_repair":
            updated_decisions.append(decision)
            continue
        source = item_by_id[decision.id]
        if source.mask_mode == "coarse":
            refinement = refine_coarse_text_mask_with_diagnostics(
                original_image,
                source,
            )
            item_mask = refinement.mask
            mask_quality: MaskQuality = (
                "refined" if item_mask is not None else "failed"
            )
            decision_update: dict[str, Any] = {
                "mask_quality": mask_quality,
                "mask_refinement_path": refinement.path,
                "mask_failure_reason": refinement.failure_reason,
                "mask_metrics": refinement.metrics,
            }
        else:
            item_mask = UITextExtractor.rebuild_text_mask(
                original_image,
                source.rect,
                source.text,
            )
            mask_quality = "native"
            decision_update = {
                "mask_quality": mask_quality,
                "mask_refinement_path": "native",
                "mask_failure_reason": None,
                "mask_metrics": None,
            }
        updated_decisions.append(
            decision.model_copy(update=decision_update)
        )
        if item_mask is None:
            continue
        if item_mask.shape != union_mask.shape or item_mask.dtype != np.uint8:
            raise TextRepairPlannerError(
                f"Rebuilt mask for {source.id} must be a full-size uint8 mask"
            )
        union_mask = cv2.bitwise_or(union_mask, item_mask)
    updated_document = document.model_copy(update={"decisions": updated_decisions})
    return (
        np.where(union_mask > 0, 255, 0).astype(np.uint8),
        updated_document,
    )


def expand_repair_mask(union_mask: np.ndarray, dilation_radius: int) -> np.ndarray:
    """Apply the explicit Route B repair expansion to the selected-text mask."""

    if dilation_radius < 0:
        raise ValueError("dilation_radius must be non-negative")
    binary = np.where(union_mask > 0, 255, 0).astype(np.uint8)
    if dilation_radius == 0 or not np.any(binary):
        return binary.copy()
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (2 * dilation_radius + 1, 2 * dilation_radius + 1),
    )
    return cv2.dilate(binary, kernel, iterations=1)


def build_repair_mask_overlay(
    original_image: np.ndarray,
    repair_mask: np.ndarray,
) -> np.ndarray:
    """Overlay repair pixels in the auditor's 55% red review style."""

    auditor._validate_image_and_mask(original_image, repair_mask)
    overlay = original_image.copy()
    mask_pixels = repair_mask > 0
    red = np.zeros_like(original_image)
    red[:, :, 0] = 255
    overlay[mask_pixels] = (
        original_image[mask_pixels].astype(np.float32) * 0.45
        + red[mask_pixels].astype(np.float32) * 0.55
    ).astype(np.uint8)
    return overlay


class UITextRepairPlanner:
    """Run the decision-only and mask-only half of Stage 0 Route B."""

    def __init__(
        self,
        client: auditor.VLMClient | None = None,
        model: str = auditor.DEFAULT_MODEL,
    ) -> None:
        self.client = client
        self.model = model

    def _audit_coverage_on_analysis(
        self,
        client: auditor.VLMClient,
        analysis_image: Path,
        existing_candidates: list[RepairTextCandidate],
        *,
        source_width: int,
        source_height: int,
        analysis_width: int,
        analysis_height: int,
    ) -> tuple[list[RepairTextCandidate], CoverageAuditDocument]:
        schema = _strict_coverage_response_schema()
        compact_candidates = _compact_coverage_candidates(
            existing_candidates,
            source_width=source_width,
            source_height=source_height,
            analysis_width=analysis_width,
            analysis_height=analysis_height,
        )
        user_prompt = (
            f"Analysis image size: {analysis_width}x{analysis_height}.\n"
            "All bbox_analysis values must be fully contained pixel coordinates "
            "in this analysis image. Check whether the supplied OCR candidates "
            "cover every clearly visible text region.\n\n"
            "Existing OCR candidates:\n"
            f"{compact_candidates}\n\n"
            "Required JSON Schema:\n"
            f"{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
        )
        payload = auditor._infer_audit_payload_with_parse_retry(
            client,
            image_path=analysis_image,
            system_prompt=COVERAGE_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_schema=schema,
        )
        try:
            if isinstance(payload, str):
                payload = json.loads(payload)
            response = VLMCoverageAuditResponse.model_validate(payload)
        except Exception as exc:
            raise TextRepairContractError(
                f"Invalid VLM coverage audit response: {exc}"
            ) from exc
        return normalize_and_deduplicate_corrections(
            response,
            existing_candidates,
            analysis_width=analysis_width,
            analysis_height=analysis_height,
            source_width=source_width,
            source_height=source_height,
        )

    def _decide_on_analysis(
        self,
        client: auditor.VLMClient,
        analysis_image: Path,
        candidates: list[RepairTextCandidate],
        *,
        image_width: int,
        image_height: int,
        analysis_width: int,
        analysis_height: int,
    ) -> TextRepairDecisionDocument:
        schema = _strict_response_schema()
        scale_x = analysis_width / image_width
        scale_y = analysis_height / image_height
        compact_candidates = auditor._compact_candidates(
            candidates,
            scale_x,
            scale_y,
        )
        user_prompt = (
            f"Source image size: {image_width}x{image_height}.\n"
            f"Analysis image size: {analysis_width}x{analysis_height}.\n"
            "Classify every candidate exactly once. This list contains original "
            "OCR candidates plus accepted coverage corrections. Candidate bboxes "
            "below are analysis-image pixel coordinates.\n\n"
            f"All text candidates:\n{compact_candidates}\n\n"
            "Required JSON Schema:\n"
            f"{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
        )
        payload = auditor._infer_audit_payload_with_parse_retry(
            client,
            image_path=analysis_image,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_schema=schema,
        )
        try:
            if isinstance(payload, str):
                payload = json.loads(payload)
            response = VLMTextDecisionResponse.model_validate(payload)
        except Exception as exc:
            raise TextRepairContractError(
                f"Invalid VLM text repair decision response: {exc}"
            ) from exc

        decision_by_id = _validate_complete_classification(candidates, response)
        decisions = []
        for source in candidates:
            classified = decision_by_id[source.id]
            decisions.append(
                TextRepairDecision(
                    id=source.id,
                    text=source.text,
                    semantic_role=classified.semantic_role,
                    decision=decision_for_semantic_role(classified.semantic_role),
                    rect=source.rect,
                    source=source.source,
                    mask_mode=source.mask_mode,
                    mask_quality=(
                        "native"
                        if source.mask_mode == "estimated_glyphs"
                        else "failed"
                    ),
                    mask_refinement_path=(
                        "native"
                        if source.mask_mode == "estimated_glyphs"
                        else "failed"
                    ),
                    confidence=classified.confidence,
                    reason=classified.reason,
                )
            )
        return TextRepairDecisionDocument(
            image_width=image_width,
            image_height=image_height,
            decisions=decisions,
        )

    def decide(
        self,
        image_path: Path,
        texts: list[TextItem],
        image_width: int,
        image_height: int,
    ) -> TextRepairDecisionDocument:
        """Run the frozen semantic pass for supplied OCR items only.

        ``process`` owns the complete v0.3 coverage + semantic flow. This method
        remains a focused entry point for callers that already have a final set.
        """

        client = self.client or auditor._create_default_client(self.model)
        if auditor.prepare_analysis_input is None:
            raise auditor.TextAuditClientError(
                "Repository image preparation helper is unavailable"
            )
        candidates = normalize_ocr_candidates(texts)
        try:
            with tempfile.TemporaryDirectory(prefix="ui-text-repair-plan-") as temp_dir:
                analysis_image = Path(temp_dir) / "analysis.png"
                metadata_path = Path(temp_dir) / "analysis.metadata.json"
                metadata = auditor.prepare_analysis_input(
                    image_path,
                    analysis_image,
                    metadata_path,
                    max_width=auditor.DEFAULT_MAX_IMAGE_WIDTH,
                    force_width=True,
                )
                analysis_width, analysis_height, _, _ = (
                    auditor._validate_analysis_input(
                        analysis_image,
                        metadata,
                        image_width,
                        image_height,
                    )
                )
                return self._decide_on_analysis(
                    client,
                    analysis_image,
                    candidates,
                    image_width=image_width,
                    image_height=image_height,
                    analysis_width=analysis_width,
                    analysis_height=analysis_height,
                )
        except (auditor.TextAuditError, TextRepairPlannerError):
            raise
        except Exception as exc:
            raise auditor.TextAuditClientError(
                f"VLM text repair decision failed: {exc}"
            ) from exc

    def process(
        self,
        image_path: Path | str,
        texts_json_path: Path | str,
        raw_mask_path: Path | str,
        output_dir: Path | str,
        *,
        dilation_radius: int,
    ) -> TextRepairDecisionDocument:
        """Run coverage, semantic ownership, and the frozen v0.2.1 mask flow."""

        if dilation_radius < 0:
            raise ValueError("dilation_radius must be non-negative")
        source_path = Path(image_path)
        original_image = auditor._load_rgb_image(source_path)
        image_height, image_width = original_image.shape[:2]
        envelope, texts = auditor._read_stage_a(Path(texts_json_path))
        if envelope is not None:
            if envelope.get("image_width") not in (None, image_width) or envelope.get(
                "image_height"
            ) not in (None, image_height):
                raise auditor.TextAuditInputError(
                    "texts.json image dimensions do not match the original image"
                )

        # raw_text_mask remains a required pipeline input, but only validates
        # compatibility. Union construction below is strictly positive/per-item.
        raw_mask = auditor._load_mask(Path(raw_mask_path))
        auditor._validate_image_and_mask(original_image, raw_mask)

        client = self.client or auditor._create_default_client(self.model)
        if auditor.prepare_analysis_input is None:
            raise auditor.TextAuditClientError(
                "Repository image preparation helper is unavailable"
            )
        ocr_candidates = normalize_ocr_candidates(texts)
        try:
            with tempfile.TemporaryDirectory(prefix="ui-text-repair-plan-") as temp_dir:
                analysis_image = Path(temp_dir) / "analysis.png"
                metadata_path = Path(temp_dir) / "analysis.metadata.json"
                metadata = auditor.prepare_analysis_input(
                    source_path,
                    analysis_image,
                    metadata_path,
                    max_width=auditor.DEFAULT_MAX_IMAGE_WIDTH,
                    force_width=True,
                )
                analysis_width, analysis_height, _, _ = (
                    auditor._validate_analysis_input(
                        analysis_image,
                        metadata,
                        image_width,
                        image_height,
                    )
                )
                corrections, coverage_document = self._audit_coverage_on_analysis(
                    client,
                    analysis_image,
                    ocr_candidates,
                    source_width=image_width,
                    source_height=image_height,
                    analysis_width=analysis_width,
                    analysis_height=analysis_height,
                )
                all_candidates = ocr_candidates + corrections
                document = self._decide_on_analysis(
                    client,
                    analysis_image,
                    all_candidates,
                    image_width=image_width,
                    image_height=image_height,
                    analysis_width=analysis_width,
                    analysis_height=analysis_height,
                )
        except (auditor.TextAuditError, TextRepairPlannerError):
            raise
        except Exception as exc:
            raise auditor.TextAuditClientError(
                f"VLM text repair planning failed: {exc}"
            ) from exc

        union_mask, document = build_union_text_mask(
            original_image,
            all_candidates,
            document,
        )
        repair_mask = expand_repair_mask(union_mask, dilation_radius)
        overlay = build_repair_mask_overlay(original_image, repair_mask)

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        try:
            (output_path / "coverage-audit.json").write_text(
                json.dumps(
                    coverage_document.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (output_path / "text-repair-decisions.json").write_text(
                json.dumps(
                    document.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise TextRepairPlannerError(
                f"Cannot write Route B decisions to {output_path}"
            ) from exc
        auditor._write_png(output_path / "union-text-mask.png", union_mask)
        auditor._write_png(output_path / "repair-mask.png", repair_mask)
        auditor._write_png(
            output_path / "repair-mask-overlay.png",
            cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR),
        )
        return document


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan Stage 0 Route B text masks without image repair."
    )
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--texts-json", type=Path, required=True)
    parser.add_argument("--raw-mask", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default=auditor.DEFAULT_MODEL)
    parser.add_argument(
        "--dilation-radius",
        type=int,
        required=True,
        help="Non-negative extra repair-mask expansion after union-mask creation",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        result = UITextRepairPlanner(model=args.model).process(
            args.image,
            args.texts_json,
            args.raw_mask,
            args.output_dir,
            dilation_radius=args.dilation_radius,
        )
    except (
        auditor.TextAuditError,
        TextRepairPlannerError,
        OSError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    remove_count = sum(
        item.decision == "remove_for_background_repair"
        for item in result.decisions
    )
    failed_remove_ids = [
        item.id
        for item in result.decisions
        if item.decision == "remove_for_background_repair"
        and item.mask_quality == "failed"
    ]
    print(
        f"Route B text plan complete: {remove_count} remove, "
        f"{len(result.decisions) - remove_count} preserve, "
        f"{len(failed_remove_ids)} mask failures."
    )
    if failed_remove_ids:
        print(f"Mask refinement failed for: {', '.join(failed_remove_ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
