#!/usr/bin/env python3
"""Stage 2.1.4 visual-semantic text auditing and targeted inpainting."""

from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
from pydantic import ValidationError

from ui_audit_models import Rect, TextAuditResult, TextItem, TextStyle
from ui_text_extractor import UITextExtractor
from ui_text_models import Rect as StageATextRect


DEFAULT_MODEL = "gpt-5.6-terra"
DEFAULT_MAX_IMAGE_WIDTH = 1024
SLOT_COUNT_MAX_BOUNDARY_EXPANSIONS = 2
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
LOGGER = logging.getLogger(__name__)

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


SYSTEM_PROMPT = """你是一个专业的游戏 UI 视觉排版与美术资产审计专家。你的任务是对输入的原始 UI 截图及 Stage A 提取的 OCR 文本候选列表进行严格的语义审计与漏检补齐。

【核心分类准则：空间依附载体 + 艺术特征】

1. 栅格美术资产 (raster_text_ids - 强制 100% 保留原始像素):
   满足以下任一条件的文本 ID，必须全量放入 raster_text_ids：
   - 条件 A (道具与箱体贴图内嵌字): 凡是物理上直接绘制/依附在宝箱箱体、道具卡面、装备插画、战队队徽内部的修饰文字与状态标（例如宝箱正面的“英雄”、战令宝箱上的“战令”和“1级”、战队标“HERO”/“DYG”、道具角标“元流”/“皮肤”）。此类文字是道具插画的有机组成部分，擦除会破坏物品美术质感，必须保留！
   - 条件 B (强风格化艺术字): 具有手绘几何外轮廓、多色渐变填充、3D立体浮雕或镶嵌吉祥物的游戏主标题与结算大字。

2. 普通可编辑文本 (editable_texts - 允许擦除底图并重建):
   仅限于独立承载于通用 UI 容器、按钮底板、导航栏或空白背景上的文本（如界面标题“背包”、按钮文案“批量使用”/“查看畅玩池”、货币数值“176”/“638050”、卡片描述段落、以及已被 OCR 检出的卡槽右下角纯数字）。

3. 复合按键符号剥离 (stripped_symbols):
   若 OCR 将数值与相邻操作按钮合并识别（如 "176+"），将 "+" 剥离至 stripped_symbols (role="button")，数值 "176" 放入 editable_texts。

4. 重复道具网格角标补漏 (text_corrections - 核心重点):
   背包或道具网格的各个卡槽可能在一致的角落显示容易被 OCR 漏检的小型可编辑数量数字。必须从图像推断实际行数和列数，不得预设固定网格尺寸，并按“自左向右、自上而下”的顺序独立检查每个可见卡槽。
   仅当卡槽角落肉眼可见数量数字（如 7, 1, 2, 4 等）且未在 OCR 列表中出现时，才在 text_corrections 中补齐并输出其归一化紧凑包围盒 bbox_norm [x0, y0, x1, y1] (0.0~1.0)。不得为空卡槽或没有可见数字证据的卡槽编造数量。

请严格返回符合 JSON Schema 的纯 JSON，严禁输出任何 Markdown 标记或多余解释。"""


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


def _compact_candidates(
    items: list[TextItem],
    scale_x: float,
    scale_y: float,
) -> str:
    """Build a compact candidate list in analysis-image pixel coordinates."""

    if not items:
        return "(empty)"
    rows: list[str] = []
    for item in items:
        safe_text = item.text.replace("\\", "\\\\").replace("\n", "\\n")
        rect = item.rect
        analysis_x = round(rect.x * scale_x)
        analysis_y = round(rect.y * scale_y)
        analysis_width = max(1, round(rect.width * scale_x))
        analysis_height = max(1, round(rect.height * scale_y))
        rows.append(
            f'- [{item.id}, "{safe_text}", '
            f"({analysis_x},{analysis_y},{analysis_width},{analysis_height})]"
        )
    return "\n".join(rows)


def _validate_analysis_input(
    analysis_image_path: Path,
    metadata: Any,
    source_width: int,
    source_height: int,
) -> tuple[int, int, float, float]:
    """Verify the shared Stage 2 preparation result and return its transform."""

    if not isinstance(metadata, dict):
        raise TextAuditInputError("Analysis-image metadata must be an object")
    source_size = metadata.get("source_size")
    analysis_size = metadata.get("analysis_size")
    if not isinstance(source_size, dict) or not isinstance(analysis_size, dict):
        raise TextAuditInputError("Analysis-image metadata has no size records")
    try:
        metadata_source_width = int(source_size["width"])
        metadata_source_height = int(source_size["height"])
        analysis_width = int(analysis_size["width"])
        analysis_height = int(analysis_size["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TextAuditInputError("Analysis-image metadata sizes are invalid") from exc
    if (metadata_source_width, metadata_source_height) != (
        source_width,
        source_height,
    ):
        raise TextAuditInputError("Analysis metadata does not match the source image")
    if analysis_width != DEFAULT_MAX_IMAGE_WIDTH or analysis_height <= 0:
        raise TextAuditInputError(
            "VLM analysis image must be proportionally resized to width 1024"
        )
    prepared_image = _load_rgb_image(analysis_image_path)
    if prepared_image.shape[:2] != (analysis_height, analysis_width):
        raise TextAuditInputError(
            "Prepared VLM image dimensions do not match its metadata"
        )
    return (
        analysis_width,
        analysis_height,
        analysis_width / source_width,
        analysis_height / source_height,
    )


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
) -> tuple[int, int, int, int]:
    x0 = int(round(bbox_norm[0] * image_width))
    y0 = int(round(bbox_norm[1] * image_height))
    x1 = int(round(bbox_norm[2] * image_width))
    y1 = int(round(bbox_norm[3] * image_height))

    pad_x = max(4, int(round((x1 - x0) * 0.35)))
    pad_y = max(4, int(round((y1 - y0) * 0.35)))
    x0 = max(0, x0 - pad_x)
    y0 = max(0, y0 - pad_y)
    x1 = min(image_width, x1 + pad_x)
    y1 = min(image_height, y1 + pad_y)
    return x0, y0, x1, y1


def _tight_correction_pixel_rect(
    bbox_norm: list[float],
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    """Convert a normalized correction box to a valid tight pixel rectangle."""

    x0 = min(image_width - 1, max(0, int(round(bbox_norm[0] * image_width))))
    y0 = min(image_height - 1, max(0, int(round(bbox_norm[1] * image_height))))
    x1 = min(
        image_width,
        max(x0 + 1, int(round(bbox_norm[2] * image_width))),
    )
    y1 = min(
        image_height,
        max(y0 + 1, int(round(bbox_norm[3] * image_height))),
    )
    return x0, y0, x1, y1


def _slot_count_search_rect(
    tight_rect: tuple[int, int, int, int],
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    """Expand a rough slot-count box into a small, clamped search ROI.

    Quantity boxes returned by the VLM can cover only a narrow stroke. Use the
    glyph height, rather than that unreliable width, to search for the rest of
    the digit. The expanded rectangle is only a search window; it is never
    filled directly into the inpaint mask.
    """

    x0, y0, x1, y1 = tight_rect
    glyph_height = max(1, y1 - y0)
    horizontal_pad = max(4, int(round(glyph_height * 0.55)))
    vertical_pad = max(3, int(round(glyph_height * 0.30)))
    return (
        max(0, x0 - horizontal_pad),
        max(0, y0 - vertical_pad),
        min(image_width, x1 + horizontal_pad),
        min(image_height, y1 + vertical_pad),
    )


def _expand_slot_count_search_rect(
    search_rect: tuple[int, int, int, int],
    boundary_hits: set[str],
    extra: int,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    """Expand only truncated sides of a slot-count search rectangle."""

    x0, y0, x1, y1 = search_rect
    return (
        max(0, x0 - extra) if "left" in boundary_hits else x0,
        max(0, y0 - extra) if "top" in boundary_hits else y0,
        min(image_width, x1 + extra) if "right" in boundary_hits else x1,
        min(image_height, y1 + extra) if "bottom" in boundary_hits else y1,
    )


def _normalize_feature(feature: np.ndarray) -> np.ndarray:
    """Robustly scale a non-negative local image feature to uint8."""

    high = float(np.percentile(feature, 95))
    if high < 1.0:
        return np.zeros(feature.shape, dtype=np.uint8)
    return np.clip(feature * (255.0 / high), 0, 255).astype(np.uint8)


def _build_correction_glyph_mask(
    original_img: np.ndarray,
    tight_rect: tuple[int, int, int, int],
    padded_rect: tuple[int, int, int, int],
    *,
    association_iterations: int = 3,
    boundary_hits: set[str] | None = None,
) -> np.ndarray:
    """Extract correction glyphs and their halo inside a padded local ROI."""

    image_height, image_width = original_img.shape[:2]
    px0, py0, px1, py1 = padded_rect
    tx0, ty0, tx1, ty1 = tight_rect
    roi = original_img[py0:py1, px0:px1]
    roi_height, roi_width = roi.shape[:2]
    local_core = np.zeros((roi_height, roi_width), dtype=bool)
    local_core[ty0 - py0 : ty1 - py0, tx0 - px0 : tx1 - px0] = True

    border_width = max(1, min(3, min(roi_height, roi_width) // 5))
    border = np.zeros((roi_height, roi_width), dtype=bool)
    border[:border_width, :] = True
    border[-border_width:, :] = True
    border[:, :border_width] = True
    border[:, -border_width:] = True

    lab = cv2.cvtColor(roi, cv2.COLOR_RGB2LAB).astype(np.float32)
    background_lab = np.median(lab[border], axis=0)
    color_distance = np.linalg.norm(
        lab - background_lab.reshape(1, 1, 3),
        axis=2,
    )
    gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
    blur_size = max(3, min(9, int(round(min(roi_height, roi_width) * 0.25))))
    if blur_size % 2 == 0:
        blur_size += 1
    blurred = cv2.GaussianBlur(gray, (blur_size, blur_size), 0)
    local_contrast = cv2.absdiff(gray, blurred).astype(np.float32)
    score = np.maximum(
        _normalize_feature(color_distance),
        _normalize_feature(local_contrast * 3.0),
    )

    otsu_threshold, _ = cv2.threshold(
        score.reshape(-1),
        0,
        255,
        cv2.THRESH_BINARY | cv2.THRESH_OTSU,
    )
    threshold = max(20, int(round(float(otsu_threshold) * 0.80)))
    candidate = np.where(score >= threshold, 255, 0).astype(np.uint8)
    close_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    candidate = cv2.morphologyEx(candidate, cv2.MORPH_CLOSE, close_kernel)

    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(
        (candidate > 0).astype(np.uint8),
        connectivity=8,
    )
    selected = np.zeros((roi_height, roi_width), dtype=np.uint8)
    association_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    associated_core = cv2.dilate(
        local_core.astype(np.uint8),
        association_kernel,
        iterations=association_iterations,
    ).astype(bool)
    roi_area = roi_height * roi_width
    boundary_margin = max(1, min(2, int(round((ty1 - ty0) * 0.10))))
    for label in range(1, component_count):
        component = labels == label
        if not np.any(component & associated_core):
            continue
        left = int(stats[label, cv2.CC_STAT_LEFT])
        top = int(stats[label, cv2.CC_STAT_TOP])
        width = int(stats[label, cv2.CC_STAT_WIDTH])
        height = int(stats[label, cv2.CC_STAT_HEIGHT])
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area > 0.60 * roi_area:
            continue
        component_boundary_hits: set[str] = set()
        if left <= boundary_margin:
            component_boundary_hits.add("left")
        if top <= boundary_margin:
            component_boundary_hits.add("top")
        if roi_width - (left + width) <= boundary_margin:
            component_boundary_hits.add("right")
        if roi_height - (top + height) <= boundary_margin:
            component_boundary_hits.add("bottom")
        if boundary_hits is not None:
            boundary_hits.update(component_boundary_hits)
        touches_border = (
            left == 0
            or top == 0
            or left + width >= roi_width
            or top + height >= roi_height
        )
        if touches_border:
            continue
        selected[component] = 255

    tight_width = tx1 - tx0
    tight_height = ty1 - ty0
    if np.count_nonzero(selected) < max(2, round(0.005 * roi_area)):
        # The VLM correction is authoritative. If local segmentation has no
        # trustworthy component, use its tight box rather than the padded ROI.
        selected[local_core] = 255

    # Repeated 3x3 elliptical dilation grows by one pixel per iteration. A
    # 2-3px adaptive halo is enough to consume antialiasing, dark outlines,
    # and short drop shadows without turning the padded ROI into a solid box.
    halo_radius = 2 if min(tight_width, tight_height) <= 8 else 3
    halo_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    selected = cv2.dilate(selected, halo_kernel, iterations=halo_radius)
    if boundary_hits is not None:
        edge_band = boundary_margin + 1
        if np.any(selected[:, :edge_band]):
            boundary_hits.add("left")
        if np.any(selected[:edge_band, :]):
            boundary_hits.add("top")
        if np.any(selected[:, -edge_band:]):
            boundary_hits.add("right")
        if np.any(selected[-edge_band:, :]):
            boundary_hits.add("bottom")
    full_mask = np.zeros((image_height, image_width), dtype=np.uint8)
    full_mask[py0:py1, px0:px1] = selected
    return full_mask


def _build_slot_count_glyph_mask(
    original_img: np.ndarray,
    tight_rect: tuple[int, int, int, int],
    bbox_norm: list[float],
) -> np.ndarray:
    """Refine one roughly located inventory quantity into a glyph-only mask."""

    image_height, image_width = original_img.shape[:2]
    search_rect = _slot_count_search_rect(
        tight_rect,
        image_width,
        image_height,
    )
    glyph_height = max(1, tight_rect[3] - tight_rect[1])
    association_iterations = max(3, min(8, int(round(glyph_height * 0.40))))
    expansion_extra = max(2, int(round(glyph_height * 0.10)))
    expansion_count = 0
    detected_boundaries: set[str] = set()
    while True:
        current_boundary_hits: set[str] = set()
        refined_mask = _build_correction_glyph_mask(
            original_img,
            tight_rect,
            search_rect,
            association_iterations=association_iterations,
            boundary_hits=current_boundary_hits,
        )
        detected_boundaries.update(current_boundary_hits)
        if (
            not current_boundary_hits
            or expansion_count >= SLOT_COUNT_MAX_BOUNDARY_EXPANSIONS
        ):
            break
        expanded_rect = _expand_slot_count_search_rect(
            search_rect,
            current_boundary_hits,
            expansion_extra,
            image_width,
            image_height,
        )
        if expanded_rect == search_rect:
            break
        search_rect = expanded_rect
        expansion_count += 1

    # Preserve the previous, conservative correction path as a fallback near
    # image/slot edges where a larger ROI can change connected components.
    conservative_mask = _build_correction_glyph_mask(
        original_img,
        tight_rect,
        _correction_pixel_rect(bbox_norm, image_width, image_height),
    )
    final_mask = cv2.bitwise_or(refined_mask, conservative_mask)
    mask_y, mask_x = np.nonzero(final_mask)
    final_mask_rect = (
        None
        if mask_x.size == 0
        else (
            int(mask_x.min()),
            int(mask_y.min()),
            int(mask_x.max()) + 1,
            int(mask_y.max()) + 1,
        )
    )
    LOGGER.debug(
        "slot_count mask refinement tight_bbox=%s search_roi=%s "
        "boundary_hits=%s expansions=%d final_mask_bbox=%s",
        tight_rect,
        search_rect,
        sorted(detected_boundaries),
        expansion_count,
        final_mask_rect,
    )
    return final_mask


def _rebuild_unsafe_stage_a_masks(
    original_img: np.ndarray,
    raw_mask: np.ndarray,
    texts: list[TextItem],
    audit_result: TextAuditResult,
) -> np.ndarray:
    """Replace stale coarse or over-dilated long-text masks from Stage A."""

    image_height, image_width = original_img.shape[:2]
    editable_by_id = {item.id: item for item in audit_result.editable_texts}
    rebuilt_mask = raw_mask.copy()
    for source_item in texts:
        audited = editable_by_id.get(source_item.id)
        if audited is None:
            continue
        is_long_text = len(audited.text) >= 5
        if source_item.mask_mode != "coarse" and not is_long_text:
            continue

        rect = source_item.rect
        # Older Stage A versions dilated ordinary text by up to 6px. Clear that
        # stale footprint before inserting the current glyph-aware mask.
        clear_radius = 8
        clear_x0 = max(0, rect.x - clear_radius)
        clear_y0 = max(0, rect.y - clear_radius)
        clear_x1 = min(image_width, rect.x + rect.width + clear_radius)
        clear_y1 = min(image_height, rect.y + rect.height + clear_radius)
        rebuilt_mask[clear_y0:clear_y1, clear_x0:clear_x1] = 0

        stage_a_rect = StageATextRect(
            x=rect.x,
            y=rect.y,
            width=rect.width,
            height=rect.height,
        )
        local_mask = UITextExtractor.rebuild_text_mask(
            original_img,
            stage_a_rect,
            source_item.text,
        )
        rebuilt_mask = cv2.bitwise_or(rebuilt_mask, local_mask)
    return rebuilt_mask


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
        client = self.client or _create_default_client(self.model)
        if prepare_analysis_input is None:
            raise TextAuditClientError(
                "Repository image preparation helper is unavailable"
            )
        try:
            with tempfile.TemporaryDirectory(prefix="ui-text-audit-") as temp_dir:
                analysis_image = Path(temp_dir) / "analysis.png"
                metadata_path = Path(temp_dir) / "analysis.metadata.json"
                metadata = prepare_analysis_input(
                    image_path,
                    analysis_image,
                    metadata_path,
                    max_width=DEFAULT_MAX_IMAGE_WIDTH,
                    force_width=True,
                )
                (
                    analysis_width,
                    analysis_height,
                    scale_x,
                    scale_y,
                ) = _validate_analysis_input(
                    analysis_image,
                    metadata,
                    image_width,
                    image_height,
                )
                user_prompt = (
                    f"原图尺寸：{image_width}x{image_height}。\n"
                    f"VLM 分析图尺寸：{analysis_width}x{analysis_height}。\n"
                    "Stage A OCR 候选格式：[ID, Text, "
                    "BBox(x,y,width,height)]。BBox 已换算为分析图像素坐标。\n"
                    "text_corrections.bbox_norm 必须使用原图归一化坐标。\n\n"
                    "候选列表：\n"
                    f"{_compact_candidates(texts, scale_x, scale_y)}\n\n"
                    "请完成语义审计。先从图像推断重复道具网格的实际行列数，"
                    "不得假设固定尺寸；再逐个检查每个可见卡槽角落是否存在"
                    "有明确视觉证据但被 OCR 漏检的数量数字。\n"
                    "Required JSON Schema:\n"
                    f"{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
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
        final_mask = _rebuild_unsafe_stage_a_masks(
            original_img,
            final_mask,
            texts,
            audit_result,
        )
        protected_mask = np.zeros_like(final_mask)

        for text_id in audit_result.raster_text_ids:
            item = item_by_id.get(text_id)
            if item is None:
                raise TextAuditInputError(f"Unknown raster text ID: {text_id}")
            rect = item.rect
            x0 = min(image_width, max(0, rect.x))
            y0 = min(image_height, max(0, rect.y))
            x1 = min(image_width, max(x0, rect.x + rect.width))
            y1 = min(image_height, max(y0, rect.y + rect.height))
            if x1 <= x0 or y1 <= y0:
                continue
            cv2.rectangle(final_mask, (x0, y0), (x1 - 1, y1 - 1), 0, -1)
            cv2.rectangle(protected_mask, (x0, y0), (x1 - 1, y1 - 1), 255, -1)

        for stripped in audit_result.stripped_symbols:
            item = item_by_id.get(stripped.source_text_id)
            if item is None:
                raise TextAuditInputError(
                    f"Unknown stripped-symbol source ID: {stripped.source_text_id}"
                )
            symbol_mask = self._protect_symbol_component(
                final_mask,
                item,
                stripped.symbol,
                stripped.estimated_bbox_norm,
                image_width,
                image_height,
            )
            protected_mask = cv2.bitwise_or(protected_mask, symbol_mask)

        # Stage A masks describe glyph cores. Grow the remaining ordinary text
        # by 2px so Telea also consumes antialiasing, outlines, and shadows.
        ordinary_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        final_mask = cv2.dilate(final_mask, ordinary_kernel, iterations=2)
        final_mask[protected_mask > 0] = 0

        correction_rects: list[tuple[int, int, int, int]] = []
        for correction in audit_result.text_corrections:
            tight_rect = _tight_correction_pixel_rect(
                correction.bbox_norm,
                image_width,
                image_height,
            )
            if correction.estimated_role.strip().casefold() == "slot_count":
                correction_mask = _build_slot_count_glyph_mask(
                    original_img,
                    tight_rect,
                    correction.bbox_norm,
                )
            else:
                padded_rect = _correction_pixel_rect(
                    correction.bbox_norm,
                    image_width,
                    image_height,
                )
                correction_mask = _build_correction_glyph_mask(
                    original_img,
                    tight_rect,
                    padded_rect,
                )
            final_mask = cv2.bitwise_or(final_mask, correction_mask)
            correction_rects.append(tight_rect)

        # A correction can overlap a raster item or stripped control symbol.
        # Protection always wins because those pixels must remain byte-identical.
        final_mask[protected_mask > 0] = 0
        final_mask = np.where(final_mask > 0, 255, 0).astype(np.uint8)

        if np.any(final_mask):
            image_bgr = cv2.cvtColor(original_img, cv2.COLOR_RGB2BGR)
            cleaned_bgr = cv2.inpaint(
                image_bgr,
                final_mask,
                inpaintRadius=3,
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
    ) -> np.ndarray:
        """Clear the connected mask component nearest the stripped symbol."""

        protected = np.zeros_like(mask)
        rect = source_item.rect
        x0 = min(image_width, max(0, rect.x))
        y0 = min(image_height, max(0, rect.y))
        x1 = min(image_width, max(x0, rect.x + rect.width))
        y1 = min(image_height, max(y0, rect.y + rect.height))
        if x1 <= x0 or y1 <= y0:
            return protected
        roi = mask[y0:y1, x0:x1]
        binary = (roi > 0).astype(np.uint8)
        component_count, labels, stats, centroids = cv2.connectedComponentsWithStats(
            binary,
            connectivity=8,
        )
        if component_count <= 1:
            return protected

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
        protected_roi = protected[y0:y1, x0:x1]
        protected_roi[selected_pixels] = 255
        return protected

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
