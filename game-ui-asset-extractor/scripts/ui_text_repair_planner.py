#!/usr/bin/env python3
"""Plan Stage 0 Route B text removal masks without repairing the image."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator

import ui_vlm_text_auditor as auditor
from ui_audit_models import Rect, TextItem
from ui_text_extractor import UITextExtractor


SCHEMA_VERSION = "0.2.1"
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


class _RepairModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


class TextRepairDecision(_RepairModel):
    """One authoritative, source-geometry-backed Route B decision."""

    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    semantic_role: SemanticRole
    decision: Decision
    rect: Rect
    mask_mode: MaskMode
    mask_quality: MaskQuality
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)


class TextRepairDecisionDocument(_RepairModel):
    """The public text-repair-decisions.json v0.2.1 contract."""

    schema_version: Literal["0.2.1"] = SCHEMA_VERSION
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    decisions: list[TextRepairDecision]


class TextRepairPlannerError(RuntimeError):
    """Base Route B planner failure."""


class TextRepairContractError(TextRepairPlannerError):
    """Raised when the VLM does not classify every OCR ID exactly once."""


def _strict_response_schema() -> dict[str, Any]:
    schema = copy.deepcopy(VLMTextDecisionResponse.model_json_schema())

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


def _validate_complete_classification(
    texts: list[TextItem],
    response: VLMTextDecisionResponse,
) -> dict[str, VLMTextDecision]:
    """Require an exact one-to-one classification of all input OCR IDs."""

    input_ids = [item.id for item in texts]
    classified_ids = [item.id for item in response.decisions]
    duplicate_ids = sorted(
        text_id for text_id, count in Counter(classified_ids).items() if count > 1
    )
    if duplicate_ids:
        raise TextRepairContractError(
            f"Duplicate OCR text classifications: {duplicate_ids}"
        )

    input_id_set = set(input_ids)
    classified_id_set = set(classified_ids)
    unknown_ids = sorted(classified_id_set - input_id_set)
    if unknown_ids:
        raise TextRepairContractError(
            f"VLM returned unknown OCR text IDs: {unknown_ids}"
        )
    missing_ids = sorted(input_id_set - classified_id_set)
    if missing_ids:
        raise TextRepairContractError(
            f"VLM omitted OCR text IDs: {missing_ids}"
        )
    if classified_id_set != input_id_set:
        raise TextRepairContractError(
            "classified_ids must exactly equal input_ocr_ids"
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


def _style_rgb(item: TextItem) -> np.ndarray:
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


def refine_coarse_text_mask(
    original_image: np.ndarray,
    item: TextItem,
) -> np.ndarray | None:
    """Extract a conservative glyph-shaped mask from one coarse OCR rectangle.

    The refiner combines the extractor's local background/glyph separation with
    the OCR-inferred foreground color. It never accepts the extractor's coarse
    fallback band and rejects candidates that are too dense or rectangle-like.
    A successful local mask receives only a one-pixel glyph halo before it is
    placed into full-image coordinates.
    """

    rect = item.rect
    crop_rgb = original_image[
        rect.y : rect.y + rect.height,
        rect.x : rect.x + rect.width,
    ]
    if crop_rgb.shape[:2] != (rect.height, rect.width) or crop_rgb.size == 0:
        return None

    allowed = np.full(crop_rgb.shape[:2], 255, dtype=np.uint8)
    background = UITextExtractor._estimate_background(crop_rgb, allowed)
    extracted, extracted_mode = UITextExtractor._extract_glyph_mask(
        crop_rgb,
        background,
        allowed,
    )

    target = _style_rgb(item)
    target_distance = np.linalg.norm(
        crop_rgb.astype(np.float32) - target.reshape(1, 1, 3),
        axis=2,
    )
    background_distance = np.linalg.norm(
        crop_rgb.astype(np.float32) - background.reshape(1, 1, 3),
        axis=2,
    )
    target_background_contrast = float(np.linalg.norm(target - background))
    style_mask = np.zeros(crop_rgb.shape[:2], dtype=np.uint8)
    if target_background_contrast >= 18.0:
        color_tolerance = float(np.clip(0.30 * target_background_contrast, 28, 64))
        style_mask[
            (target_distance <= color_tolerance) & (background_distance >= 14.0)
        ] = 255
        style_mask = cv2.morphologyEx(
            style_mask,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)),
        )

    candidates: list[np.ndarray] = []
    if extracted_mode == "estimated_glyphs" and np.any(style_mask):
        style_neighborhood = cv2.dilate(
            style_mask,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
            iterations=1,
        )
        candidates.append(cv2.bitwise_and(extracted, style_neighborhood))
    if np.any(style_mask):
        candidates.append(style_mask)
    if extracted_mode == "estimated_glyphs":
        candidates.append(extracted)

    for candidate in candidates:
        candidate = np.where(candidate > 0, 255, 0).astype(np.uint8)
        if not _is_reliable_local_glyph_mask(candidate, crop_rgb, background):
            continue
        refined = cv2.dilate(
            candidate,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
            iterations=1,
        )
        refined_coverage = np.count_nonzero(refined) / float(refined.size)
        if refined_coverage > 0.55:
            continue
        full_mask = np.zeros(original_image.shape[:2], dtype=np.uint8)
        full_mask[
            rect.y : rect.y + rect.height,
            rect.x : rect.x + rect.width,
        ] = refined
        return full_mask
    return None


def build_union_text_mask(
    original_image: np.ndarray,
    texts: list[TextItem],
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
            item_mask = refine_coarse_text_mask(original_image, source)
            mask_quality: MaskQuality = (
                "refined" if item_mask is not None else "failed"
            )
        else:
            item_mask = UITextExtractor.rebuild_text_mask(
                original_image,
                source.rect,
                source.text,
            )
            mask_quality = "native"
        updated_decisions.append(
            decision.model_copy(update={"mask_quality": mask_quality})
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

    def decide(
        self,
        image_path: Path,
        texts: list[TextItem],
        image_width: int,
        image_height: int,
    ) -> TextRepairDecisionDocument:
        """Ask the VLM for an exhaustive decision and attach source geometry."""

        client = self.client or auditor._create_default_client(self.model)
        if auditor.prepare_analysis_input is None:
            raise auditor.TextAuditClientError(
                "Repository image preparation helper is unavailable"
            )
        schema = _strict_response_schema()
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
                analysis_width, analysis_height, scale_x, scale_y = (
                    auditor._validate_analysis_input(
                        analysis_image,
                        metadata,
                        image_width,
                        image_height,
                    )
                )
                user_prompt = (
                    f"Source image size: {image_width}x{image_height}.\n"
                    f"Analysis image size: {analysis_width}x{analysis_height}.\n"
                    "Classify every candidate exactly once. Candidate bboxes below "
                    "are analysis-image pixel coordinates.\n\n"
                    f"OCR candidates:\n{auditor._compact_candidates(texts, scale_x, scale_y)}\n\n"
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
        except (auditor.TextAuditError, TextRepairPlannerError):
            raise
        except Exception as exc:
            raise auditor.TextAuditClientError(
                f"VLM text repair decision failed: {exc}"
            ) from exc

        try:
            if isinstance(payload, str):
                payload = json.loads(payload)
            response = VLMTextDecisionResponse.model_validate(payload)
        except Exception as exc:
            raise TextRepairContractError(
                f"Invalid VLM text repair decision response: {exc}"
            ) from exc

        decision_by_id = _validate_complete_classification(texts, response)
        decisions = []
        for source in texts:
            classified = decision_by_id[source.id]
            decisions.append(
                TextRepairDecision(
                    id=source.id,
                    text=source.text,
                    semantic_role=classified.semantic_role,
                    decision=decision_for_semantic_role(
                        classified.semantic_role
                    ),
                    rect=source.rect,
                    mask_mode=source.mask_mode,
                    mask_quality=(
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

    def process(
        self,
        image_path: Path | str,
        texts_json_path: Path | str,
        raw_mask_path: Path | str,
        output_dir: Path | str,
        *,
        dilation_radius: int,
    ) -> TextRepairDecisionDocument:
        """Write exactly the four decision/mask review artifacts for Route B."""

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

        document = self.decide(source_path, texts, image_width, image_height)
        union_mask, document = build_union_text_mask(
            original_image,
            texts,
            document,
        )
        repair_mask = expand_repair_mask(union_mask, dilation_radius)
        overlay = build_repair_mask_overlay(original_image, repair_mask)

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        try:
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
