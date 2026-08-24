#!/usr/bin/env python3
"""Stage A game UI text extraction, hard-mask generation, and style inference."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

import cv2
import numpy as np

from ui_text_models import Rect, TextExtractionResult, TextItem, TextStyle


OCREngine = Callable[[np.ndarray], Any]
MaskMode = Literal["estimated_glyphs", "coarse"]
SUPPORTED_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


@dataclass(frozen=True)
class _OCRCandidate:
    text: str
    confidence: float
    rect: Rect
    polygon: np.ndarray


@dataclass(frozen=True)
class _GlyphExtraction:
    mask: np.ndarray
    mode: MaskMode
    background_rgb: np.ndarray


class UITextExtractor:
    """Extract editable UI copy and deterministic Stage A artifacts.

    Args:
        ocr_engine: Optional RapidOCR-compatible callable. If omitted,
            ``rapidocr_onnxruntime.RapidOCR`` is initialized lazily.
    """

    MIN_CONFIDENCE = 0.35
    LOW_CONFIDENCE = 0.85
    VERTICAL_ASPECT_RATIO = 2.4
    MIN_GLYPH_COVERAGE = 0.015
    MAX_GLYPH_COVERAGE = 0.68

    def __init__(self, ocr_engine: OCREngine | None = None) -> None:
        if ocr_engine is not None:
            self._ocr = ocr_engine
            return
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise RuntimeError(
                "rapidocr-onnxruntime is required for real OCR; inject a "
                "RapidOCR-compatible callable for tests."
            ) from exc
        self._ocr = RapidOCR()

    def extract(
        self,
        image_path: Path | str,
        output_json_path: Path | str,
        output_mask_path: Path | str,
        debug_path: Path | str | None = None,
    ) -> TextExtractionResult:
        """Run Stage A and export JSON, a full-image hard mask, and debug image."""

        source_path = Path(image_path)
        image_bgr = self._read_image(source_path)
        try:
            raw_output = self._ocr(image_bgr)
        except Exception as exc:
            raise RuntimeError(f"OCR failed for {source_path}: {exc}") from exc

        image_height, image_width = image_bgr.shape[:2]
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        hard_mask = np.zeros((image_height, image_width), dtype=np.uint8)
        items: list[TextItem] = []

        for raw_item in self._unwrap_ocr_output(raw_output):
            candidate = self._parse_candidate(raw_item, image_width, image_height)
            if candidate is None or not self._passes_filter(
                candidate.text,
                candidate.confidence,
                candidate.rect.width,
                candidate.rect.height,
            ):
                continue

            glyph = self._extract_candidate_glyphs(image_rgb, candidate)
            crop_rgb = self._crop(image_rgb, candidate.rect)
            style = self._estimate_typography(
                candidate.text,
                candidate.rect.height,
                glyph.mask,
                crop_rgb,
                glyph.background_rgb,
            )
            item = TextItem(
                id=f"text_{len(items):03d}",
                text=candidate.text,
                confidence=candidate.confidence,
                rect=candidate.rect,
                style=style,
                mask_mode=glyph.mode,
            )
            items.append(item)
            hard_mask = cv2.bitwise_or(
                hard_mask,
                self._build_dilated_full_mask(
                    glyph.mask,
                    candidate.rect,
                    candidate.text,
                    (image_height, image_width),
                ),
            )

        result = TextExtractionResult(
            image_width=image_width,
            image_height=image_height,
            count=len(items),
            items=items,
        )
        self._write_json(Path(output_json_path), result)
        self._write_image(Path(output_mask_path), hard_mask, force_png=True)
        if debug_path is not None:
            debug = self._draw_debug_visualization(image_bgr, result)
            self._write_image(Path(debug_path), debug)
        return result

    @staticmethod
    def _read_image(path: Path) -> np.ndarray:
        """Read a BGR image from a Unicode-safe path."""

        if not path.is_file():
            raise FileNotFoundError(f"Image does not exist: {path}")
        try:
            encoded = np.fromfile(str(path), dtype=np.uint8)
        except OSError as exc:
            raise ValueError(f"Unable to read image: {path}") from exc
        image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            raise ValueError(f"Unable to decode image: {path}")
        return image

    @staticmethod
    def _unwrap_ocr_output(raw_output: Any) -> list[Any]:
        """Normalize RapidOCR's ``(results, elapsed)`` return value."""

        if raw_output is None:
            return []
        results = raw_output[0] if isinstance(raw_output, tuple) else raw_output
        return list(results) if isinstance(results, (list, tuple)) else []

    @staticmethod
    def _parse_candidate(
        raw_item: Any,
        image_width: int,
        image_height: int,
    ) -> _OCRCandidate | None:
        """Validate one OCR row and clamp its quadrilateral bbox to the image."""

        if not isinstance(raw_item, (list, tuple)) or len(raw_item) < 3:
            return None
        box, raw_text, raw_confidence = raw_item[:3]
        text = str(raw_text).strip()
        if not text:
            return None
        try:
            confidence = float(raw_confidence)
            points = np.asarray(box, dtype=np.float64).reshape(-1, 2)
        except (TypeError, ValueError):
            return None
        if (
            not math.isfinite(confidence)
            or len(points) < 3
            or not np.isfinite(points).all()
        ):
            return None

        x1 = max(0, int(math.floor(float(points[:, 0].min()))))
        y1 = max(0, int(math.floor(float(points[:, 1].min()))))
        x2 = min(image_width, int(math.ceil(float(points[:, 0].max()))))
        y2 = min(image_height, int(math.ceil(float(points[:, 1].max()))))
        if x2 <= x1 or y2 <= y1:
            return None
        return _OCRCandidate(
            text=text,
            confidence=confidence,
            rect=Rect(x=x1, y=y1, width=x2 - x1, height=y2 - y1),
            polygon=points,
        )

    @classmethod
    def _passes_filter(
        cls,
        text: str,
        confidence: float,
        width: int,
        height: int,
    ) -> bool:
        """Apply the frozen Stage A OCR false-positive filters."""

        if confidence < cls.MIN_CONFIDENCE:
            return False
        single_latin = len(text) == 1 and text.isascii() and text.isalpha()
        if confidence < cls.LOW_CONFIDENCE and single_latin:
            return False
        return not (
            confidence < cls.LOW_CONFIDENCE
            and height / width >= cls.VERTICAL_ASPECT_RATIO
        )

    @staticmethod
    def _crop(image: np.ndarray, rect: Rect) -> np.ndarray:
        return image[rect.y : rect.y + rect.height, rect.x : rect.x + rect.width]

    @staticmethod
    def _build_allowed_mask(candidate: _OCRCandidate) -> np.ndarray:
        """Rasterize the OCR quadrilateral inside its axis-aligned local crop."""

        rect = candidate.rect
        local_points = candidate.polygon - np.array([rect.x, rect.y])
        local_points = np.rint(local_points).astype(np.int32)
        allowed = np.zeros((rect.height, rect.width), dtype=np.uint8)
        cv2.fillPoly(allowed, [local_points], 255)
        if not np.any(allowed):
            allowed.fill(255)
        return allowed

    @staticmethod
    def _estimate_background(
        crop_rgb: np.ndarray,
        allowed_mask: np.ndarray | None = None,
    ) -> np.ndarray:
        """Estimate RGB background from allowed pixels in a 1-3 px edge band."""

        height, width = crop_rgb.shape[:2]
        band_w = int(np.clip(min(height, width) // 4, 1, 3))
        border = np.zeros((height, width), dtype=bool)
        border[:band_w, :] = True
        border[-band_w:, :] = True
        border[:, :band_w] = True
        border[:, -band_w:] = True
        allowed = (
            np.ones((height, width), dtype=bool)
            if allowed_mask is None
            else allowed_mask > 0
        )
        samples = crop_rgb[border & allowed]
        if samples.size == 0:
            samples = crop_rgb[allowed]
        if samples.size == 0:
            samples = crop_rgb.reshape(-1, 3)
        return np.median(samples, axis=0).astype(np.float32)

    @classmethod
    def _extract_glyph_mask(
        cls,
        crop_rgb: np.ndarray,
        background_rgb: np.ndarray,
        allowed_mask: np.ndarray | None = None,
    ) -> tuple[np.ndarray, MaskMode]:
        """Separate glyphs by 3x RGB distance, Otsu, and rectangular closing."""

        allowed = (
            np.ones(crop_rgb.shape[:2], dtype=bool)
            if allowed_mask is None
            else allowed_mask > 0
        )
        distance = np.linalg.norm(
            crop_rgb.astype(np.float32) - background_rgb.reshape(1, 1, 3),
            axis=2,
        )
        scaled_distance = np.clip(3.0 * distance, 0, 255).astype(np.uint8)
        allowed_distances = scaled_distance[allowed]
        if allowed_distances.size == 0:
            return np.full(crop_rgb.shape[:2], 255, dtype=np.uint8), "coarse"
        otsu_threshold, _ = cv2.threshold(
            allowed_distances,
            0,
            255,
            cv2.THRESH_BINARY | cv2.THRESH_OTSU,
        )
        decision_threshold = max(12.0, 0.65 * float(otsu_threshold))
        mask = ((3.0 * distance >= decision_threshold) & allowed).astype(np.uint8)
        mask *= 255
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask[~allowed] = 0

        coverage = float(np.count_nonzero(mask)) / float(mask.size)
        if coverage < cls.MIN_GLYPH_COVERAGE or coverage > cls.MAX_GLYPH_COVERAGE:
            return np.full(mask.shape, 255, dtype=np.uint8), "coarse"
        return mask, "estimated_glyphs"

    @classmethod
    def _extract_candidate_glyphs(
        cls,
        image_rgb: np.ndarray,
        candidate: _OCRCandidate,
    ) -> _GlyphExtraction:
        crop_rgb = cls._crop(image_rgb, candidate.rect)
        allowed = cls._build_allowed_mask(candidate)
        background = cls._estimate_background(crop_rgb, allowed)
        mask, mode = cls._extract_glyph_mask(crop_rgb, background, allowed)
        return _GlyphExtraction(mask=mask, mode=mode, background_rgb=background)

    @classmethod
    def _build_dilated_full_mask(
        cls,
        glyph_mask: np.ndarray,
        rect: Rect,
        text: str,
        image_shape: tuple[int, int],
    ) -> np.ndarray:
        """Place local glyphs and dilate them with the prescribed ellipse radius."""

        single_chinese = len(text) == 1 and cls._is_chinese_ideograph(text)
        if single_chinese:
            radius = int(np.clip(round(0.16 * rect.height), 3, 8))
        else:
            radius = int(np.clip(round(0.09 * rect.height), 2, 6))
        full_mask = np.zeros(image_shape, dtype=np.uint8)
        full_mask[
            rect.y : rect.y + rect.height,
            rect.x : rect.x + rect.width,
        ] = glyph_mask
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (2 * radius + 1, 2 * radius + 1),
        )
        return cv2.dilate(full_mask, kernel)

    @classmethod
    def _estimate_typography(
        cls,
        text: str,
        bbox_height: int,
        glyph_mask: np.ndarray,
        crop_rgb: np.ndarray,
        background_rgb: np.ndarray,
    ) -> TextStyle:
        """Infer deterministic font, foreground, weight, and outline properties."""

        foreground_rgb = cls._estimate_foreground_color(
            crop_rgb,
            glyph_mask,
            background_rgb,
        )
        coverage = float(np.count_nonzero(glyph_mask)) / float(glyph_mask.size)
        single_chinese = len(text) == 1 and cls._is_chinese_ideograph(text)
        font_size = max(
            8,
            int(round((0.88 if single_chinese else 0.82) * bbox_height)),
        )
        foreground_luma = cls._rec709_luma(foreground_rgb)
        background_luma = cls._rec709_luma(background_rgb)
        stroke_color = (
            "#1e2322" if foreground_luma >= background_luma else "#f0f4f1"
        )
        stroke_scale = 0.045 if coverage > 0.12 else 0.03
        stroke_width = int(np.clip(round(stroke_scale * bbox_height), 0, 2))
        return TextStyle(
            color=cls._rgb_to_hex(foreground_rgb),
            fontFamily=(
                "Microsoft YaHei"
                if any(cls._is_cjk(character) for character in text)
                else "Arial"
            ),
            fontSize=font_size,
            fontWeight=700 if coverage >= 0.17 else 600,
            strokeColor=stroke_color,
            strokeWidth=stroke_width,
        )

    @staticmethod
    def _estimate_foreground_color(
        crop_rgb: np.ndarray,
        glyph_mask: np.ndarray,
        background_rgb: np.ndarray,
    ) -> np.ndarray:
        """Score 32-wide RGB buckets by population and background contrast."""

        pixels = crop_rgb[glyph_mask > 0]
        if pixels.size == 0:
            return np.array([16, 16, 16], dtype=np.uint8)
        bucket_centers = (pixels.astype(np.uint16) // 32) * 32 + 16
        colors, counts = np.unique(bucket_centers, axis=0, return_counts=True)
        contrast = np.linalg.norm(
            colors.astype(np.float32) - background_rgb.reshape(1, 3),
            axis=1,
        )
        scores = counts.astype(np.float64) * (24.0 + contrast)
        return colors[int(np.argmax(scores))].astype(np.uint8)

    @staticmethod
    def _is_cjk(character: str) -> bool:
        if len(character) != 1:
            return False
        codepoint = ord(character)
        return (
            UITextExtractor._is_chinese_ideograph(character)
            or 0x3000 <= codepoint <= 0x303F
            or 0x3040 <= codepoint <= 0x30FF
            or 0x31F0 <= codepoint <= 0x31FF
            or 0x1100 <= codepoint <= 0x11FF
            or 0x3130 <= codepoint <= 0x318F
            or 0xAC00 <= codepoint <= 0xD7AF
            or 0xFF66 <= codepoint <= 0xFF9D
        )

    @staticmethod
    def _is_chinese_ideograph(character: str) -> bool:
        if len(character) != 1:
            return False
        codepoint = ord(character)
        return (
            0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF
            or 0x20000 <= codepoint <= 0x2FA1F
        )

    @staticmethod
    def _rec709_luma(rgb: np.ndarray) -> float:
        return float(0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2])

    @staticmethod
    def _rgb_to_hex(rgb: np.ndarray) -> str:
        red, green, blue = (int(channel) for channel in rgb)
        return f"#{red:02X}{green:02X}{blue:02X}"

    @classmethod
    def _draw_debug_visualization(
        cls,
        image_bgr: np.ndarray,
        result: TextExtractionResult,
    ) -> np.ndarray:
        """Draw bbox plus id, OCR text, size, and foreground color."""

        debug = image_bgr.copy()
        font = cv2.FONT_HERSHEY_SIMPLEX
        for item in result.items:
            rect, style = item.rect, item.style
            rgb = cls._hex_to_rgb(style.color)
            box_color = (rgb[2], rgb[1], rgb[0])
            cv2.rectangle(
                debug,
                (rect.x, rect.y),
                (rect.x + rect.width - 1, rect.y + rect.height - 1),
                box_color,
                2,
            )
            clean_text = item.text.replace("\n", " ")[:40]
            debug_text = clean_text.encode("ascii", "backslashreplace").decode()
            label = f"{item.id} {debug_text} | {style.fontSize}px {style.color}"
            (label_width, label_height), baseline = cv2.getTextSize(
                label,
                font,
                0.42,
                1,
            )
            label_x = min(rect.x, max(0, debug.shape[1] - label_width - 4))
            label_y = rect.y - 3
            if label_y < label_height + baseline:
                label_y = min(
                    debug.shape[0] - baseline - 1,
                    rect.y + rect.height + label_height + 3,
                )
            top = max(0, label_y - label_height - baseline - 3)
            right = min(debug.shape[1] - 1, label_x + label_width + 4)
            cv2.rectangle(
                debug,
                (label_x, top),
                (right, min(debug.shape[0] - 1, label_y + baseline)),
                (20, 20, 20),
                cv2.FILLED,
            )
            cv2.putText(
                debug,
                label,
                (label_x + 2, max(label_height, label_y - baseline)),
                font,
                0.42,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
        return debug

    @staticmethod
    def _hex_to_rgb(color: str) -> tuple[int, int, int]:
        value = color.removeprefix("#")
        return int(value[:2], 16), int(value[2:4], 16), int(value[4:], 16)

    @staticmethod
    def _write_json(path: Path, result: TextExtractionResult) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise RuntimeError(f"Unable to write JSON output: {path}") from exc

    @staticmethod
    def _write_image(
        path: Path,
        image: np.ndarray,
        *,
        force_png: bool = False,
    ) -> None:
        suffix = ".png" if force_png else path.suffix or ".png"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            success, encoded = cv2.imencode(suffix, image)
            if not success:
                raise RuntimeError(f"OpenCV cannot encode output image as {suffix}")
            encoded.tofile(str(path))
        except (OSError, cv2.error) as exc:
            raise RuntimeError(f"Unable to write image: {path}") from exc


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract Stage A UI text, typography, and a hard text mask."
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        help="Compatibility input: one image or a directory of images",
    )
    parser.add_argument("--image", type=Path, help="Single input image")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-mask", type=Path)
    parser.add_argument("--output-debug", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Directory for automatically named single or batch outputs",
    )
    return parser.parse_args(argv)


def _collect_input_images(input_path: Path) -> tuple[list[Path], bool]:
    """Resolve one supported image or a sorted, non-recursive image directory."""

    if input_path.is_file():
        if input_path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
            supported = ", ".join(sorted(SUPPORTED_IMAGE_SUFFIXES))
            raise ValueError(
                f"Unsupported input extension; expected one of: {supported}"
            )
        return [input_path], False
    if input_path.is_dir():
        images = sorted(
            (
                path
                for path in input_path.iterdir()
                if path.is_file()
                and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
            ),
            key=lambda path: (path.name.casefold(), path.name),
        )
        if not images:
            raise ValueError(f"No supported images found in directory: {input_path}")
        return images, True
    raise FileNotFoundError(f"Input path does not exist: {input_path}")


def _automatic_output_paths(
    image_path: Path,
    output_dir: Path,
) -> tuple[Path, Path, Path]:
    """Build all Stage A artifact paths from one source stem."""

    stem = image_path.stem
    return (
        output_dir / f"{stem}_texts.json",
        output_dir / f"{stem}_raw_text_mask.png",
        output_dir / f"{stem}_debug.png",
    )


def main(argv: list[str] | None = None) -> int:
    """Run one Stage A extraction or a fault-tolerant directory batch."""

    args = _parse_args(argv)
    try:
        if args.input is not None and args.image is not None:
            raise ValueError("Use either positional input or --image, not both")
        input_path = args.image or args.input
        if input_path is None:
            raise ValueError("An input image or directory is required")
        images, is_batch = _collect_input_images(input_path)
        if is_batch and any(
            path is not None
            for path in (args.output_json, args.output_mask, args.output_debug)
        ):
            raise ValueError(
                "Explicit output paths can only be used with a single image"
            )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        extractor = UITextExtractor()
    except (FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    succeeded = 0
    failed = 0
    total_items = 0
    total_images = len(images)
    for index, image_path in enumerate(images, start=1):
        auto_json, auto_mask, auto_debug = _automatic_output_paths(
            image_path,
            args.output_dir,
        )
        output_json = args.output_json or auto_json
        output_mask = args.output_mask or auto_mask
        if args.output_debug is not None:
            output_debug: Path | None = args.output_debug
        elif is_batch or args.output_json is None or args.output_mask is None:
            output_debug = auto_debug
        else:
            output_debug = None

        if is_batch:
            print(f"[{index}/{total_images}] Processing {image_path}")
        try:
            result = extractor.extract(
                image_path,
                output_json,
                output_mask,
                output_debug,
            )
        except Exception as exc:
            failed += 1
            print(
                f"[{index}/{total_images}] ERROR {image_path}: {exc}",
                file=sys.stderr,
            )
            continue

        succeeded += 1
        total_items += result.count
        print(
            f"[{index}/{total_images}] Extracted {result.count} text item(s): "
            f"{output_json} | {output_mask}"
        )

    if is_batch:
        print(
            f"Summary: {succeeded} succeeded, {failed} failed, "
            f"{total_images} total, {total_items} text item(s)."
        )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
