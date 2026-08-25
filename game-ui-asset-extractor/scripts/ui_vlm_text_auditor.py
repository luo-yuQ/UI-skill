#!/usr/bin/env python3
"""Stage 2.1.4 visual-semantic text auditing and targeted inpainting."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
from pydantic import ValidationError

from ui_audit_models import Rect, TextAuditResult, TextItem, TextStyle


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
except ImportError:  # pragma: no cover - only relevant to incomplete deployments
    prepare_analysis_input = None
    ResponsesAPIVLMClient = None
    VLMClientConfig = None


SYSTEM_PROMPT = """你是一个专业的游戏 UI 视觉与排版审计专家。你的任务是对输入的原始 UI 截图及 Stage A 的 OCR 候选文本列表进行严格的语义审计与漏检补齐。

【审计规则】
1. 栅格美术资产 (raster_text_ids):
   - 仅包含具有专属艺术设计、手绘几何、战队品牌 Logo (如 HERO, DYG)、立体浮雕游戏标题 (如 "精灵大作战", "VICTORY")、以及道具角标上的专属图章 (如 "元流", "皮肤")。
   - 只有这些 ID 才能放入 raster_text_ids。系统将保护这些像素不被擦除。
   - 严禁将普通按钮文字 ($99.99, "确定", "重置") 或带描边的常规数值误判为美术资产！
2. 普通可编辑文本 (editable_texts):
   - 界面标题、说明文案、商品价格、按钮文字、动态计数必须放入 editable_texts。
3. 复合按键符号剥离 (stripped_symbols):
   - 当 OCR 将数值与相邻操作按钮识别为一体时（例如 "176+"），将 "+" 剥离为 stripped_symbols (role="button")，并将数值 "176" 放入 editable_texts。
4. 角标数量与单字符漏检补齐 (text_corrections - 核心重点):
   - 仔细逐个检查背包/网格中每个道具卡槽右下角的堆叠数量（如 "7", "1", "2", "4", "20", "27" 等）。
   - 如果 OCR 候选列表中遗漏了某个道具格子右下角的单字符数字，必须在 text_corrections 中输出：
     * text: 识别的真实数字内容
     * bbox_norm: 紧密包围该数字字符的归一化包围盒 [x0, y0, x1, y1] (0.0 ~ 1.0)
     * confidence: 0.95
     * estimated_role: "slot_count"

请直接返回严格符合 JSON Schema 的纯 JSON 结果，严禁附加任何解释性文字。"""


class VLMClient(Protocol):
    """Small injectable interface used by the text auditor."""

    def infer_json(
        self,
        *,
        image_path: Path,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
    ) -> Any:
        """Return one decoded JSON-compatible VLM response."""


class TextAuditError(RuntimeError):
    """Base exception raised by the Stage 2.1.4 processor."""


class TextAuditInputError(TextAuditError):
    """Raised when a local input or array is invalid."""


class TextAuditClientError(TextAuditError):
    """Raised when the VLM request cannot be completed."""


class TextAuditResponseError(TextAuditError):
    """Raised when the VLM response violates the audit contract."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TextAuditInputError(f"File does not exist: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise TextAuditInputError(f"Cannot read valid JSON from {path}: {exc}") from exc


def _read_stage_a(path: Path) -> tuple[dict[str, Any] | None, list[TextItem]]:
    document = _read_json(path)
    if isinstance(document, list):
        raw_items = document
        envelope = None
    elif isinstance(document, dict) and isinstance(document.get("items"), list):
        raw_items = document["items"]
        envelope = document
    else:
        raise TextAuditInputError(
            f"Stage A JSON must be a list or an object containing 'items': {path}"
        )
    try:
        items = [TextItem.model_validate(item) for item in raw_items]
    except (ValidationError, TypeError) as exc:
        raise TextAuditInputError(
            f"Invalid Stage A text item in {path}: {exc}"
        ) from exc
    if len({item.id for item in items}) != len(items):
        raise TextAuditInputError(f"Stage A text IDs must be unique: {path}")
    return envelope, items


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


def _first_environment_value(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _create_default_client(model: str) -> VLMClient:
    if ResponsesAPIVLMClient is None or VLMClientConfig is None:
        raise TextAuditClientError(
            "Repository VLM client is unavailable under game-ui-asset-analyzer/scripts"
        )
    base_url = _first_environment_value(
        "OPENAI_BASE_URL", "BASE_URL", "STAGE2A_VLM_BASE_URL"
    )
    api_key = _first_environment_value(
        "OPENAI_API_KEY", "API_KEY", "STAGE2A_VLM_API_KEY"
    )
    if not base_url or not api_key:
        raise TextAuditClientError(
            "VLM configuration missing: set OPENAI_BASE_URL and OPENAI_API_KEY"
        )
    timeout_text = _first_environment_value("VLM_TIMEOUT", "STAGE2A_VLM_TIMEOUT")
    try:
        timeout = float(timeout_text) if timeout_text else 60.0
    except ValueError as exc:
        raise TextAuditClientError(f"Invalid VLM timeout: {timeout_text!r}") from exc
    config = VLMClientConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
    )
    return ResponsesAPIVLMClient(config)


def _compact_candidates(items: list[TextItem]) -> str:
    """Build a compact Markdown candidate list without redundant style data."""

    if not items:
        return "(empty)"
    rows: list[str] = []
    for item in items:
        safe_text = item.text.replace("\\", "\\\\").replace("\n", "\\n")
        rect = item.rect
        rows.append(
            f'- [{item.id}, "{safe_text}", '
            f"({rect.x},{rect.y},{rect.width},{rect.height})]"
        )
    return "\n".join(rows)


def _validate_image_and_mask(original_img: np.ndarray, raw_mask: np.ndarray) -> None:
    if not isinstance(original_img, np.ndarray) or original_img.dtype != np.uint8:
        raise TextAuditInputError("original_img must be a uint8 NumPy array")
    if original_img.ndim != 3 or original_img.shape[2] != 3:
        raise TextAuditInputError("original_img must have shape (H, W, 3)")
    if not isinstance(raw_mask, np.ndarray) or raw_mask.dtype != np.uint8:
        raise TextAuditInputError("raw_mask must be a uint8 NumPy array")
    if raw_mask.ndim != 2:
        raise TextAuditInputError("raw_mask must be single-channel")
    if raw_mask.shape != original_img.shape[:2]:
        raise TextAuditInputError("raw_mask dimensions must match original_img")


def _correction_pixel_rect(
    bbox_norm: list[float],
    image_width: int,
    image_height: int,
    *,
    dilate: bool,
) -> tuple[int, int, int, int]:
    x0 = int(round(bbox_norm[0] * image_width))
    y0 = int(round(bbox_norm[1] * image_height))
    x1 = int(round(bbox_norm[2] * image_width))
    y1 = int(round(bbox_norm[3] * image_height))
    if dilate:
        x0 -= 1
        y0 -= 1
        x1 += 1
        y1 += 1
    x0 = min(image_width - 1, max(0, x0))
    y0 = min(image_height - 1, max(0, y0))
    x1 = min(image_width, max(x0 + 1, x1))
    y1 = min(image_height, max(y0 + 1, y1))
    return x0, y0, x1, y1


class UIVLMTextAuditor:
    """Audit OCR semantics, compensate misses, and rebuild a clean UI image."""

    def __init__(
        self,
        client: VLMClient | None = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self.client = client
        self.model = model

    def audit(
        self,
        original_image_path: Path | str,
        texts_json_path: Path | str,
    ) -> TextAuditResult:
        """Ask the VLM to classify Stage A candidates and recover missed text."""

        image_path = Path(original_image_path)
        texts_path = Path(texts_json_path)
        image = _load_rgb_image(image_path)
        image_height, image_width = image.shape[:2]
        envelope, texts = _read_stage_a(texts_path)
        if envelope is not None:
            declared_width = envelope.get("image_width")
            declared_height = envelope.get("image_height")
            if declared_width not in (None, image_width) or declared_height not in (
                None,
                image_height,
            ):
                raise TextAuditInputError(
                    "Stage A image dimensions do not match the supplied screenshot"
                )

        schema = _strict_response_schema()
        user_prompt = (
            f"原图尺寸：{image_width}x{image_height}。\n"
            "Stage A OCR 候选格式：[ID, Text, BBox(x,y,width,height)]。\n\n"
            f"候选列表：\n{_compact_candidates(texts)}\n\n"
            "请完成语义审计，并逐个检查所有道具卡槽右下角的数量角标。\n"
            "Required JSON Schema:\n"
            f"{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
        )
        client = self.client or _create_default_client(self.model)
        if prepare_analysis_input is None:
            raise TextAuditClientError(
                "Repository image preparation helper is unavailable"
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
                    response_schema=schema,
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

        source_ids = {item.id for item in texts}
        referenced_ids = set(result.raster_text_ids)
        referenced_ids.update(item.id for item in result.editable_texts)
        referenced_ids.update(item.source_text_id for item in result.stripped_symbols)
        unknown_ids = referenced_ids - source_ids
        if unknown_ids:
            raise TextAuditResponseError(
                f"VLM response references unknown text IDs: {sorted(unknown_ids)}"
            )
        duplicate_raster_ids = len(result.raster_text_ids) != len(
            set(result.raster_text_ids)
        )
        if duplicate_raster_ids:
            raise TextAuditResponseError("raster_text_ids must not contain duplicates")
        editable_ids = [item.id for item in result.editable_texts]
        if len(editable_ids) != len(set(editable_ids)):
            raise TextAuditResponseError(
                "editable_texts must not contain duplicate IDs"
            )
        overlap = set(result.raster_text_ids) & set(editable_ids)
        if overlap:
            raise TextAuditResponseError(
                f"Text IDs cannot be both raster and editable: {sorted(overlap)}"
            )
        return result

    def filter_mask_and_inpaint(
        self,
        original_img: np.ndarray,
        raw_mask: np.ndarray,
        texts: list[TextItem],
        audit_result: TextAuditResult,
    ) -> tuple[np.ndarray, np.ndarray, list[TextItem]]:
        """Rebuild the mask, apply Telea, and return unified editable metadata."""

        _validate_image_and_mask(original_img, raw_mask)
        image_height, image_width = original_img.shape[:2]
        item_by_id = {item.id: item for item in texts}
        if len(item_by_id) != len(texts):
            raise TextAuditInputError("texts must contain unique IDs")
        final_mask = raw_mask.copy()

        for text_id in audit_result.raster_text_ids:
            item = item_by_id.get(text_id)
            if item is None:
                raise TextAuditInputError(f"Unknown raster text ID: {text_id}")
            rect = item.rect
            cv2.rectangle(
                final_mask,
                (rect.x, rect.y),
                (min(image_width, rect.x + rect.width),
                 min(image_height, rect.y + rect.height)),
                0,
                -1,
            )

        for stripped in audit_result.stripped_symbols:
            item = item_by_id.get(stripped.source_text_id)
            if item is None:
                raise TextAuditInputError(
                    f"Unknown stripped-symbol source ID: {stripped.source_text_id}"
                )
            self._protect_symbol_component(
                final_mask,
                item,
                stripped.symbol,
                stripped.estimated_bbox_norm,
                image_width,
                image_height,
            )

        correction_rects: list[tuple[int, int, int, int]] = []
        for correction in audit_result.text_corrections:
            x0, y0, x1, y1 = _correction_pixel_rect(
                correction.bbox_norm,
                image_width,
                image_height,
                dilate=True,
            )
            cv2.rectangle(final_mask, (x0, y0), (x1, y1), 255, -1)
            correction_rects.append((x0, y0, x1, y1))

        if np.any(final_mask):
            image_bgr = cv2.cvtColor(original_img, cv2.COLOR_RGB2BGR)
            cleaned_bgr = cv2.inpaint(
                image_bgr,
                final_mask,
                inpaintRadius=5,
                flags=cv2.INPAINT_TELEA,
            )
            cleaned_image = cv2.cvtColor(cleaned_bgr, cv2.COLOR_BGR2RGB)
        else:
            cleaned_image = original_img.copy()

        unified_texts = self._merge_text_metadata(
            texts,
            audit_result,
            correction_rects,
        )
        return cleaned_image, final_mask, unified_texts

    @staticmethod
    def _protect_symbol_component(
        mask: np.ndarray,
        source_item: TextItem,
        symbol: str,
        estimated_bbox_norm: list[float] | None,
        image_width: int,
        image_height: int,
    ) -> None:
        """Clear the connected mask component nearest the stripped symbol."""

        rect = source_item.rect
        x0 = min(image_width, max(0, rect.x))
        y0 = min(image_height, max(0, rect.y))
        x1 = min(image_width, max(x0, rect.x + rect.width))
        y1 = min(image_height, max(y0, rect.y + rect.height))
        if x1 <= x0 or y1 <= y0:
            return
        roi = mask[y0:y1, x0:x1]
        binary = (roi > 0).astype(np.uint8)
        component_count, labels, stats, centroids = cv2.connectedComponentsWithStats(
            binary,
            connectivity=8,
        )
        if component_count <= 1:
            return

        if estimated_bbox_norm is not None:
            target_x = (
                (estimated_bbox_norm[0] + estimated_bbox_norm[2])
                * image_width
                / 2
                - x0
            )
            target_y = (
                (estimated_bbox_norm[1] + estimated_bbox_norm[3])
                * image_height
                / 2
                - y0
            )
        else:
            symbol_index = source_item.text.rfind(symbol) if symbol else -1
            if symbol_index < 0:
                symbol_index = max(0, len(source_item.text) - 1)
            target_x = (
                (symbol_index + max(1, len(symbol)) / 2)
                / max(1, len(source_item.text))
                * (x1 - x0)
            )
            target_y = (y1 - y0) / 2

        best_label = min(
            range(1, component_count),
            key=lambda label: (
                ((float(centroids[label, 0]) - target_x) / max(1, x1 - x0)) ** 2
                + ((float(centroids[label, 1]) - target_y) / max(1, y1 - y0)) ** 2
            ),
        )
        selected_pixels = labels == best_label
        component_width = int(stats[best_label, cv2.CC_STAT_WIDTH])
        character_width = (x1 - x0) / max(1, len(source_item.text))
        if len(source_item.text) > 1 and component_width > 2.2 * character_width:
            half_span = max(1.0, 0.75 * character_width)
            left = max(0, int(round(target_x - half_span)))
            right = min(x1 - x0, int(round(target_x + half_span)))
            column_gate = np.zeros_like(selected_pixels)
            column_gate[:, left:right] = True
            selected_pixels &= column_gate
        roi[selected_pixels] = 0

    @staticmethod
    def _merge_text_metadata(
        texts: list[TextItem],
        audit_result: TextAuditResult,
        correction_rects: list[tuple[int, int, int, int]],
    ) -> list[TextItem]:
        editable_by_id = {item.id: item for item in audit_result.editable_texts}
        unified: list[TextItem] = []
        for source_item in texts:
            audited = editable_by_id.get(source_item.id)
            if audited is None:
                continue
            unified.append(source_item.model_copy(update={"text": audited.text}))

        for index, (correction, pixel_rect) in enumerate(
            zip(audit_result.text_corrections, correction_rects, strict=True),
            start=1,
        ):
            x0, y0, x1, y1 = pixel_rect
            height = y1 - y0
            unified.append(
                TextItem(
                    id=f"text_corr_{index:03d}",
                    text=correction.text,
                    confidence=correction.confidence,
                    rect=Rect(x=x0, y=y0, width=x1 - x0, height=height),
                    style=TextStyle(
                        fontFamily="Microsoft YaHei",
                        fontSize=max(8, round(0.82 * height)),
                        color="#FFFFFF",
                        fontWeight=700,
                        strokeColor="#1e2322",
                        strokeWidth=1,
                    ),
                    mask_mode="estimated_glyphs",
                )
            )
        return unified

    @staticmethod
    def _filtered_document(
        envelope: dict[str, Any] | None,
        unified_texts: list[TextItem],
        image_width: int,
        image_height: int,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        dumped = [item.model_dump(mode="json") for item in unified_texts]
        if envelope is None:
            return dumped
        result = copy.deepcopy(envelope)
        result["image_width"] = image_width
        result["image_height"] = image_height
        result["items"] = dumped
        result["count"] = len(dumped)
        return result

    def export_artifacts(
        self,
        audit_result: TextAuditResult,
        final_inpaint_mask: np.ndarray,
        cleaned_image: np.ndarray,
        unified_texts: list[TextItem],
        output_dir: Path | str,
        *,
        source_envelope: dict[str, Any] | None = None,
    ) -> None:
        """Write the four standard Stage 2.1.4 artifacts."""

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        image_height, image_width = cleaned_image.shape[:2]
        filtered_document = self._filtered_document(
            source_envelope,
            unified_texts,
            image_width,
            image_height,
        )
        try:
            (output_path / "audit_result.json").write_text(
                json.dumps(
                    audit_result.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (output_path / "filtered_texts.json").write_text(
                json.dumps(filtered_document, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise TextAuditInputError(
                f"Cannot write artifacts to {output_path}"
            ) from exc
        _write_png(output_path / "final_inpaint_mask.png", final_inpaint_mask)
        _write_png(
            output_path / "cleaned_image.png",
            cv2.cvtColor(cleaned_image, cv2.COLOR_RGB2BGR),
        )

    def process(
        self,
        original_image_path: Path | str,
        texts_json_path: Path | str,
        raw_mask_path: Path | str,
        output_dir: Path | str,
    ) -> TextAuditResult:
        """Run visual auditing, local mask reconstruction, and export."""

        image_path = Path(original_image_path)
        texts_path = Path(texts_json_path)
        mask_path = Path(raw_mask_path)
        output_path = Path(output_dir)
        audit_result = self.audit(image_path, texts_path)
        image = _load_rgb_image(image_path)
        raw_mask = _load_mask(mask_path)
        envelope, texts = _read_stage_a(texts_path)
        cleaned, final_mask, unified_texts = self.filter_mask_and_inpaint(
            image,
            raw_mask,
            texts,
            audit_result,
        )
        self.export_artifacts(
            audit_result,
            final_mask,
            cleaned,
            unified_texts,
            output_path,
            source_envelope=envelope,
        )
        return audit_result


# Compatibility aliases retained for earlier Stage B callers.
UIRasterTextProcessor = UIVLMTextAuditor
UITextAuditor = UIVLMTextAuditor
AuditClientError = TextAuditClientError
AuditResponseError = TextAuditResponseError


def build_argument_parser() -> argparse.ArgumentParser:
    """Create the Stage 2.1.4 command-line parser."""

    parser = argparse.ArgumentParser(
        description="Audit UI text and produce a raster-art-preserving clean image."
    )
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--texts-json", type=Path, required=True)
    parser.add_argument("--raw-mask", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the Stage 2.1.4 CLI and return a process exit code."""

    args = build_argument_parser().parse_args(argv)
    try:
        result = UIVLMTextAuditor(model=args.model).process(
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
        f"{len(result.stripped_symbols)} protected symbols, "
        f"{len(result.text_corrections)} corrections."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
