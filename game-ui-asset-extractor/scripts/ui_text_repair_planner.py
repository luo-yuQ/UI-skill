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


SCHEMA_VERSION = "0.1"
Decision = Literal[
    "remove_for_background_repair",
    "preserve_as_visual_asset",
]


SYSTEM_PROMPT = """You are reviewing all OCR text in a complete game UI screenshot.
Classify every supplied OCR text ID exactly once.

Use remove_for_background_repair for ordinary runtime UI copy that should later be
rebuilt with UI text components: button labels, tabs/categories, values, timers,
status information, ordinary titles, descriptions, and item quantity badges.

Use preserve_as_visual_asset only when the text is an inseparable part of a bitmap
visual asset: a logo, text inside an icon, text baked into item artwork, artistic
display lettering, or text strongly fused with an illustration/icon.

Judge from the full visual context, not OCR wording alone. For an inventory UI,
ordinary labels such as 查看畅玩池 and 豪华皮肤畅玩卡, runtime values such as
638050, and item quantity badges should normally be removed. Words such as HERO,
DYG, 皮肤, or 货币 should be preserved when they are part of the item-icon art.

Do not return an uncertain category. Return strict JSON matching the schema. Do
not omit, duplicate, or invent OCR IDs."""


class _RepairModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VLMTextDecision(_RepairModel):
    """One compact decision returned directly by the VLM."""

    id: str = Field(min_length=1)
    decision: Decision
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
    decision: Decision
    rect: Rect
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)


class TextRepairDecisionDocument(_RepairModel):
    """The public text-repair-decisions.json v0.1 contract."""

    schema_version: Literal["0.1"] = SCHEMA_VERSION
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


def build_union_text_mask(
    original_image: np.ndarray,
    texts: list[TextItem],
    document: TextRepairDecisionDocument,
) -> np.ndarray:
    """Positively OR rebuilt masks for remove decisions into an empty mask.

    ``UITextExtractor.rebuild_text_mask`` already applies its adaptive Stage A
    glyph dilation. This output is therefore the selected-text baseline mask;
    the separate repair expansion is applied only by ``expand_repair_mask``.
    The all-text ``raw_text_mask`` is deliberately not sliced or subtracted.
    """

    item_by_id = {item.id: item for item in texts}
    union_mask = np.zeros(original_image.shape[:2], dtype=np.uint8)
    for decision in document.decisions:
        if decision.decision != "remove_for_background_repair":
            continue
        source = item_by_id[decision.id]
        item_mask = UITextExtractor.rebuild_text_mask(
            original_image,
            source.rect,
            source.text,
        )
        if item_mask.shape != union_mask.shape or item_mask.dtype != np.uint8:
            raise TextRepairPlannerError(
                f"Rebuilt mask for {source.id} must be a full-size uint8 mask"
            )
        union_mask = cv2.bitwise_or(union_mask, item_mask)
    return np.where(union_mask > 0, 255, 0).astype(np.uint8)


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
                    decision=classified.decision,
                    rect=source.rect,
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
        union_mask = build_union_text_mask(original_image, texts, document)
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
    print(
        f"Route B text plan complete: {remove_count} remove, "
        f"{len(result.decisions) - remove_count} preserve."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
