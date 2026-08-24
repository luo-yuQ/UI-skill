#!/usr/bin/env python3
"""VLM auditing and targeted removal for editable UI text.

This module implements Stage 2.1.4. It asks a VLM to distinguish editable
copy from raster artwork, removes protected raster regions from the Stage A
mask, and applies OpenCV Telea inpainting only to the remaining pixels.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from pydantic import ValidationError

from ui_audit_models import TextAuditResult


DEFAULT_MODEL = "gpt-5.6-terra"
DEFAULT_MAX_IMAGE_WIDTH = 1024
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

ANALYZER_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[2] / "game-ui-asset-analyzer" / "scripts"
)
if str(ANALYZER_SCRIPTS_DIR) not in sys.path:
    sys.path.append(str(ANALYZER_SCRIPTS_DIR))

try:
    from prepare_analysis_input import (  # type: ignore[import-not-found]
        prepare_analysis_input,
    )
    from vlm_client import (  # type: ignore[import-not-found]
        ResponsesAPIVLMClient,
        VLMClientConfig,
    )
except ImportError:  # pragma: no cover - exercised only in broken deployments
    prepare_analysis_input = None
    ResponsesAPIVLMClient = None
    VLMClientConfig = None


SYSTEM_PROMPT = """You are a senior game UI art and typography auditor.
Classify every OCR candidate using the supplied screenshot and candidate list.

The critical distinction is:
- raster artwork: hand-drawn display lettering, team marks such as HERO or DYG,
  embossed game logos, irregular seals, or text inseparable from an illustration.
  These pixels MUST be preserved and their IDs go in raster_text_ids.
- editable copy: button labels, prices such as $99.99, login/reset labels, titles,
  descriptions, levels, and dynamic values such as 12,450 or Lv.1. These items
  go in editable_texts because their pixels may be removed and recreated.

When an OCR candidate combines editable text with an action glyph, for example
"176+", keep "176" as editable text and add "+" to stripped_symbols. A stripped
symbol is a UI control and must be protected from inpainting.

Inspect every supplied text ID. Never invent IDs. Return one JSON object only,
with exactly the requested schema and no Markdown or explanatory prose.
"""


class TextAuditError(RuntimeError):
    """Base exception raised by the Stage 2.1.4 processor."""


class TextAuditInputError(TextAuditError):
    """Raised when an input file or array is invalid."""


class TextAuditClientError(TextAuditError):
    """Raised when the VLM request fails."""


class TextAuditResponseError(TextAuditError):
    """Raised when the VLM response violates the audit contract."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TextAuditInputError(f"File does not exist: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise TextAuditInputError(f"Cannot read valid JSON from {path}: {exc}") from exc


def _read_stage_a(path: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    document = _read_json(path)
    if isinstance(document, list):
        items = document
        envelope = None
    elif isinstance(document, dict) and isinstance(document.get("items"), list):
        envelope = document
        items = document["items"]
    else:
        raise TextAuditInputError(
            f"Stage A JSON must be a list or an object containing 'items': {path}"
        )

    if not all(isinstance(item, dict) for item in items):
        raise TextAuditInputError(f"Every Stage A item must be an object: {path}")
    return envelope, items


def _validate_rect(item: dict[str, Any], image_width: int, image_height: int) -> dict[str, int]:
    rect = item.get("rect")
    if not isinstance(rect, dict):
        raise TextAuditInputError(f"Text item {item.get('id')!r} has no valid rect")
    try:
        x = int(rect["x"])
        y = int(rect["y"])
        width = int(rect["width"])
        height = int(rect["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TextAuditInputError(f"Invalid rect for text item {item.get('id')!r}") from exc

    if width <= 0 or height <= 0:
        raise TextAuditInputError(f"Non-positive rect for text item {item.get('id')!r}")
    x1 = max(0, min(x, image_width))
    y1 = max(0, min(y, image_height))
    x2 = max(0, min(x + width, image_width))
    y2 = max(0, min(y + height, image_height))
    if x2 <= x1 or y2 <= y1:
        raise TextAuditInputError(f"Rect is outside the image for {item.get('id')!r}")
    return {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}


def _load_rgb_image(path: Path) -> np.ndarray:
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        raise TextAuditInputError(f"Unsupported image extension: {path.suffix}")
    try:
        payload = np.fromfile(path, dtype=np.uint8)
        bgr = cv2.imdecode(payload, cv2.IMREAD_COLOR)
    except OSError as exc:
        raise TextAuditInputError(f"Cannot read image {path}: {exc}") from exc
    if bgr is None:
        raise TextAuditInputError(f"Cannot decode image: {path}")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def _load_mask(path: Path) -> np.ndarray:
    try:
        payload = np.fromfile(path, dtype=np.uint8)
        mask = cv2.imdecode(payload, cv2.IMREAD_GRAYSCALE)
    except OSError as exc:
        raise TextAuditInputError(f"Cannot read mask {path}: {exc}") from exc
    if mask is None:
        raise TextAuditInputError(f"Cannot decode mask: {path}")
    return np.where(mask > 0, 255, 0).astype(np.uint8)


def _write_png(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise TextAuditInputError(f"Failed to encode PNG: {path}")
    try:
        encoded.tofile(path)
    except OSError as exc:
        raise TextAuditInputError(f"Failed to write PNG {path}: {exc}") from exc


def _strict_response_schema() -> dict[str, Any]:
    schema = copy.deepcopy(TextAuditResult.model_json_schema())

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


def _create_default_client(model: str) -> Any:
    if ResponsesAPIVLMClient is None or VLMClientConfig is None:
        raise TextAuditClientError(
            "Repository VLM client is unavailable under game-ui-asset-analyzer/scripts"
        )
    base_url = os.getenv("BASE_URL") or os.getenv("STAGE2A_VLM_BASE_URL")
    api_key = os.getenv("API_KEY") or os.getenv("STAGE2A_VLM_API_KEY")
    if not base_url or not api_key:
        raise TextAuditClientError(
            "VLM configuration missing: set BASE_URL and API_KEY"
        )
    timeout_text = os.getenv("VLM_TIMEOUT") or os.getenv("STAGE2A_VLM_TIMEOUT") or "60"
    try:
        timeout = float(timeout_text)
    except ValueError as exc:
        raise TextAuditClientError(f"Invalid VLM_TIMEOUT: {timeout_text!r}") from exc
    config = VLMClientConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
    )
    return ResponsesAPIVLMClient(config)


class UIRasterTextProcessor:
    """Audit OCR text and generate a raster-art-preserving clean image."""

    def __init__(self, client: Any | None = None, model: str = DEFAULT_MODEL) -> None:
        self.client = client
        self.model = model

    def audit(
        self,
        image_path: Path | str,
        texts_json_path: Path | str,
    ) -> TextAuditResult:
        """Run visual-semantic auditing and return a validated result."""
        image_path = Path(image_path)
        texts_json_path = Path(texts_json_path)
        image = _load_rgb_image(image_path)
        height, width = image.shape[:2]
        envelope, items = _read_stage_a(texts_json_path)
        if envelope is not None:
            declared_width = envelope.get("image_width")
            declared_height = envelope.get("image_height")
            if declared_width not in (None, width) or declared_height not in (None, height):
                raise TextAuditInputError(
                    "Stage A image dimensions do not match the supplied screenshot"
                )

        candidates: list[dict[str, Any]] = []
        for item in items:
            rect = _validate_rect(item, width, height)
            candidates.append(
                {
                    "id": str(item.get("id", "")),
                    "text": str(item.get("text", "")),
                    "confidence": item.get("confidence"),
                    "rect": rect,
                    "bbox_norm": [
                        rect["x"] / width,
                        rect["y"] / height,
                        (rect["x"] + rect["width"]) / width,
                        (rect["y"] + rect["height"]) / height,
                    ],
                    "style": item.get("style", {}),
                }
            )

        if prepare_analysis_input is None:
            raise TextAuditClientError("Repository image preparation helper is unavailable")
        client = self.client or _create_default_client(self.model)
        user_prompt = (
            "Audit all OCR candidates below against the screenshot. "
            "Coordinates are normalized as [left, top, right, bottom].\n\n"
            f"Candidates:\n{json.dumps(candidates, ensure_ascii=False, indent=2)}\n\n"
            f"Required JSON Schema:\n"
            f"{json.dumps(_strict_response_schema(), ensure_ascii=False)}"
        )

        try:
            with tempfile.TemporaryDirectory(prefix="ui-text-audit-") as temp_dir:
                analysis_image = Path(temp_dir) / "analysis.png"
                metadata_path = Path(temp_dir) / "analysis.metadata.json"
                prepare_analysis_input(
                    image_path,
                    analysis_image,
                    metadata_path,
                    max_width=DEFAULT_MAX_IMAGE_WIDTH,
                    force_width=True,
                )
                payload = client.infer_json(
                    image_path=analysis_image,
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    response_schema=_strict_response_schema(),
                )
        except TextAuditError:
            raise
        except Exception as exc:
            raise TextAuditClientError(f"VLM audit failed: {exc}") from exc

        try:
            if isinstance(payload, str):
                payload = json.loads(payload)
            result = TextAuditResult.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise TextAuditResponseError(f"Invalid VLM audit response: {exc}") from exc

        source_ids = {str(item.get("id", "")) for item in items}
        referenced_ids = set(result.raster_text_ids)
        referenced_ids.update(item.id for item in result.editable_texts)
        referenced_ids.update(item.source_text_id for item in result.stripped_symbols)
        unknown_ids = referenced_ids - source_ids
        if unknown_ids:
            raise TextAuditResponseError(
                f"VLM response references unknown text IDs: {sorted(unknown_ids)}"
            )
        overlap = set(result.raster_text_ids) & {item.id for item in result.editable_texts}
        if overlap:
            raise TextAuditResponseError(
                f"Text IDs cannot be both raster and editable: {sorted(overlap)}"
            )
        return result

    def filter_mask_and_inpaint(
        self,
        image: np.ndarray,
        raw_mask: np.ndarray,
        texts_json_path: Path,
        audit_result: TextAuditResult,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Protect raster/symbol pixels, then Telea-inpaint editable text.

        Args:
            image: Original RGB uint8 image with shape ``(H, W, 3)``.
            raw_mask: Full Stage A mask; nonzero pixels are removal candidates.
            texts_json_path: Stage A JSON containing source rectangles.
            audit_result: Validated visual-semantic classification.

        Returns:
            ``(cleaned_image, final_inpaint_mask)`` in RGB/uint8 and
            single-channel uint8 formats respectively.
        """
        if not isinstance(image, np.ndarray) or image.dtype != np.uint8:
            raise TextAuditInputError("image must be a uint8 NumPy array")
        if image.ndim != 3 or image.shape[2] != 3:
            raise TextAuditInputError("image must have shape (H, W, 3)")
        if not isinstance(raw_mask, np.ndarray) or raw_mask.ndim != 2:
            raise TextAuditInputError("raw_mask must be a single-channel NumPy array")
        if raw_mask.dtype != np.uint8:
            raise TextAuditInputError("raw_mask must have dtype uint8")
        if raw_mask.shape != image.shape[:2]:
            raise TextAuditInputError("raw_mask dimensions must match the image")

        height, width = image.shape[:2]
        _, items = _read_stage_a(Path(texts_json_path))
        item_by_id = {str(item.get("id", "")): item for item in items}
        final_mask = np.where(raw_mask > 0, 255, 0).astype(np.uint8)

        for text_id in audit_result.raster_text_ids:
            item = item_by_id.get(text_id)
            if item is None:
                raise TextAuditInputError(f"Unknown raster text ID: {text_id}")
            rect = _validate_rect(item, width, height)
            x, y = rect["x"], rect["y"]
            final_mask[y : y + rect["height"], x : x + rect["width"]] = 0

        for stripped in audit_result.stripped_symbols:
            item = item_by_id.get(stripped.source_text_id)
            if item is None:
                raise TextAuditInputError(
                    f"Unknown stripped-symbol source ID: {stripped.source_text_id}"
                )
            rect = _validate_rect(item, width, height)
            self._remove_symbol_component(
                final_mask,
                rect,
                str(item.get("text", "")),
                stripped.symbol,
            )

        if not np.any(final_mask):
            return image.copy(), final_mask
        image_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        cleaned_bgr = cv2.inpaint(
            image_bgr,
            final_mask,
            inpaintRadius=5,
            flags=cv2.INPAINT_TELEA,
        )
        cleaned_rgb = cv2.cvtColor(cleaned_bgr, cv2.COLOR_BGR2RGB)
        return cleaned_rgb, final_mask

    @staticmethod
    def _remove_symbol_component(
        mask: np.ndarray,
        rect: dict[str, int],
        source_text: str,
        symbol: str,
    ) -> None:
        """Remove the component nearest a symbol's estimated text position."""
        x, y = rect["x"], rect["y"]
        width, height = rect["width"], rect["height"]
        roi = mask[y : y + height, x : x + width]
        binary = (roi > 0).astype(np.uint8)
        component_count, labels, stats, centroids = cv2.connectedComponentsWithStats(
            binary,
            connectivity=8,
        )
        if component_count <= 1:
            return

        index = source_text.find(symbol) if symbol else -1
        if index >= 0 and source_text:
            expected_x = ((index + max(len(symbol), 1) / 2) / len(source_text)) * width
        else:
            expected_x = width / 2
        expected_y = height / 2

        best_label = min(
            range(1, component_count),
            key=lambda label: (
                ((float(centroids[label, 0]) - expected_x) / max(width, 1)) ** 2
                + ((float(centroids[label, 1]) - expected_y) / max(height, 1)) ** 2
            ),
        )
        component_width = int(stats[best_label, cv2.CC_STAT_WIDTH])
        estimated_character_width = width / max(len(source_text), 1)
        selected_pixels = labels == best_label
        if len(source_text) > 1 and component_width > 2.2 * estimated_character_width:
            # Stage A dilation can fuse neighbouring glyphs. In that case, keep
            # the connected-component decision but constrain protection to the
            # estimated symbol cell instead of preserving the entire OCR text.
            half_span = max(1.0, 0.75 * estimated_character_width)
            left = max(0, int(round(expected_x - half_span)))
            right = min(width, int(round(expected_x + half_span)))
            column_gate = np.zeros_like(selected_pixels)
            column_gate[:, left:right] = True
            selected_pixels &= column_gate
        roi[selected_pixels] = 0

    def build_filtered_texts(
        self,
        texts_json_path: Path | str,
        audit_result: TextAuditResult,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Keep only VLM-confirmed editable entries in Stage A format."""
        envelope, items = _read_stage_a(Path(texts_json_path))
        editable_by_id = {item.id: item for item in audit_result.editable_texts}
        filtered: list[dict[str, Any]] = []
        for source_item in items:
            source_id = str(source_item.get("id", ""))
            audit_item = editable_by_id.get(source_id)
            if audit_item is None:
                continue
            copied = copy.deepcopy(source_item)
            copied["text"] = audit_item.text
            filtered.append(copied)

        if envelope is None:
            return filtered
        result = copy.deepcopy(envelope)
        result["items"] = filtered
        result["count"] = len(filtered)
        return result

    def export_artifacts(
        self,
        result: TextAuditResult,
        final_inpaint_mask: np.ndarray,
        cleaned_image: np.ndarray,
        filtered_texts: dict[str, Any] | list[dict[str, Any]],
        output_dir: Path,
    ) -> None:
        """Export the four Stage 2.1.4 artifacts to ``output_dir``."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "audit_result.json").write_text(
            json.dumps(result.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "filtered_texts.json").write_text(
            json.dumps(filtered_texts, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _write_png(output_dir / "final_inpaint_mask.png", final_inpaint_mask)
        _write_png(
            output_dir / "cleaned_image.png",
            cv2.cvtColor(cleaned_image, cv2.COLOR_RGB2BGR),
        )

    def process(
        self,
        image_path: Path | str,
        texts_json_path: Path | str,
        raw_mask_path: Path | str,
        output_dir: Path | str,
    ) -> TextAuditResult:
        """Run auditing, mask filtering, inpainting, and artifact export."""
        image_path = Path(image_path)
        texts_json_path = Path(texts_json_path)
        raw_mask_path = Path(raw_mask_path)
        output_dir = Path(output_dir)
        audit_result = self.audit(image_path, texts_json_path)
        image = _load_rgb_image(image_path)
        raw_mask = _load_mask(raw_mask_path)
        cleaned, final_mask = self.filter_mask_and_inpaint(
            image,
            raw_mask,
            texts_json_path,
            audit_result,
        )
        filtered_texts = self.build_filtered_texts(texts_json_path, audit_result)
        self.export_artifacts(
            audit_result,
            final_mask,
            cleaned,
            filtered_texts,
            output_dir,
        )
        return audit_result


# Backward-compatible import name for callers of the earlier Stage B draft.
UITextAuditor = UIRasterTextProcessor
AuditClientError = TextAuditClientError
AuditResponseError = TextAuditResponseError


def build_argument_parser() -> argparse.ArgumentParser:
    """Create the Stage 2.1.4 command-line parser."""
    parser = argparse.ArgumentParser(
        description="Audit UI text and produce a raster-art-preserving clean image."
    )
    parser.add_argument("--image", type=Path, required=True, help="Original UI image")
    parser.add_argument(
        "--texts-json",
        type=Path,
        required=True,
        help="Stage A texts.json",
    )
    parser.add_argument(
        "--raw-mask",
        type=Path,
        required=True,
        help="Stage A raw_text_mask.png",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for Stage 2.1.4 artifacts",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="VLM model name")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the Stage 2.1.4 CLI and return a process exit code."""
    args = build_argument_parser().parse_args(argv)
    try:
        processor = UIRasterTextProcessor(model=args.model)
        result = processor.process(
            args.image,
            args.texts_json,
            args.raw_mask,
            args.output_dir,
        )
    except (TextAuditError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"Audit complete: {len(result.editable_texts)} editable, "
        f"{len(result.raster_text_ids)} raster, "
        f"{len(result.stripped_symbols)} protected symbols."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
