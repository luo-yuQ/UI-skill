#!/usr/bin/env python3
"""Extract editable text and inferred typography from a flat game UI image.

The module intentionally performs no background removal or inpainting.  OCR
locates the text, while deterministic local image statistics estimate the
properties needed to recreate each text layer.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np


OCREngine = Callable[[np.ndarray], Any]


class UITextExtractor:
    """Extract OCR text boxes and infer their basic typography.

    Args:
        ocr_engine: Optional RapidOCR-compatible callable.  Supplying one is
            useful for tests or for sharing a preconfigured OCR session.  If
            omitted, :class:`rapidocr_onnxruntime.RapidOCR` is initialized.
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
                "rapidocr-onnxruntime is required; install it with "
                "`pip install rapidocr-onnxruntime`."
            ) from exc
        self._ocr = RapidOCR()

    def extract(
        self,
        image_path: str,
        output_json_path: str,
        debug_vis_path: str,
    ) -> list[dict[str, Any]]:
        """Extract text layers and write JSON plus a debug visualization.

        Args:
            image_path: Path to the source UI screenshot.
            output_json_path: Destination for the UTF-8 JSON array.
            debug_vis_path: Destination for the annotated image.

        Returns:
            The same list of text-layer dictionaries written to JSON.

        Raises:
            FileNotFoundError: If ``image_path`` does not exist.
            ValueError: If the source cannot be decoded as an image.
            RuntimeError: If OCR execution or output writing fails.
        """

        source_path = Path(image_path)
        image = self._read_image(source_path)

        try:
            raw_output = self._ocr(image)
        except Exception as exc:  # OCR providers expose several exception types.
            raise RuntimeError(f"OCR failed for {source_path}: {exc}") from exc

        raw_results = self._unwrap_ocr_output(raw_output)
        image_height, image_width = image.shape[:2]
        layers: list[dict[str, Any]] = []

        for raw_item in raw_results:
            candidate = self._parse_candidate(raw_item, image_width, image_height)
            if candidate is None:
                continue
            text, confidence, x, y, width, height = candidate
            if not self._passes_filter(text, confidence, width, height):
                continue

            crop_bgr = image[y : y + height, x : x + width]
            if crop_bgr.size == 0:
                continue
            crop_rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
            background_rgb = self._estimate_background(crop_rgb)
            glyph_mask = self._extract_glyph_mask(crop_rgb, background_rgb)
            style = self._estimate_typography(
                text,
                height,
                glyph_mask,
                crop_rgb,
                background_rgb,
            )

            layers.append(
                {
                    "id": f"text_{len(layers):03d}",
                    "text": text,
                    "confidence": float(confidence),
                    "rect": {
                        "x": x,
                        "y": y,
                        "width": width,
                        "height": height,
                    },
                    "style": style,
                }
            )

        self._write_json(Path(output_json_path), layers)
        visualization = self._draw_debug_visualization(image, layers)
        self._write_image(Path(debug_vis_path), visualization)
        return layers

    @staticmethod
    def _read_image(path: Path) -> np.ndarray:
        """Read an image in BGR form, including paths containing Unicode."""

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
        """Normalize the ``(result, elapsed)`` response returned by RapidOCR."""

        if raw_output is None:
            return []
        results = raw_output[0] if isinstance(raw_output, tuple) else raw_output
        if results is None:
            return []
        if isinstance(results, (list, tuple)):
            return list(results)
        return []

    @staticmethod
    def _parse_candidate(
        raw_item: Any,
        image_width: int,
        image_height: int,
    ) -> tuple[str, float, int, int, int, int] | None:
        """Validate an OCR row and convert its quadrilateral to a clamped rect."""

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
            or len(points) < 2
            or not np.isfinite(points).all()
        ):
            return None

        x1 = max(0, int(math.floor(float(points[:, 0].min()))))
        y1 = max(0, int(math.floor(float(points[:, 1].min()))))
        x2 = min(image_width, int(math.ceil(float(points[:, 0].max()))))
        y2 = min(image_height, int(math.ceil(float(points[:, 1].max()))))
        width, height = x2 - x1, y2 - y1
        if width <= 0 or height <= 0:
            return None
        return text, confidence, x1, y1, width, height

    @classmethod
    def _passes_filter(
        cls,
        text: str,
        confidence: float,
        width: int,
        height: int,
    ) -> bool:
        """Apply confidence, icon-like letter, and vertical-bar filters."""

        if confidence < cls.MIN_CONFIDENCE:
            return False
        is_single_latin = len(text) == 1 and text.isascii() and text.isalpha()
        if confidence < cls.LOW_CONFIDENCE and is_single_latin:
            return False
        aspect_ratio = height / width
        if confidence < cls.LOW_CONFIDENCE and aspect_ratio >= cls.VERTICAL_ASPECT_RATIO:
            return False
        return True

    @staticmethod
    def _estimate_background(crop_rgb: np.ndarray) -> np.ndarray:
        """Estimate local RGB background from a one-to-three-pixel edge band."""

        height, width = crop_rgb.shape[:2]
        band = int(round(min(height, width) / 4.0))
        band = max(1, min(3, band))
        border = np.zeros((height, width), dtype=bool)
        border[:band, :] = True
        border[-band:, :] = True
        border[:, :band] = True
        border[:, -band:] = True
        return np.median(crop_rgb[border], axis=0).astype(np.float32)

    @classmethod
    def _extract_glyph_mask(
        cls,
        crop_rgb: np.ndarray,
        background_rgb: np.ndarray,
    ) -> np.ndarray:
        """Build a closed binary glyph mask using local color distance."""

        distance = np.linalg.norm(
            crop_rgb.astype(np.float32) - background_rgb.reshape(1, 1, 3),
            axis=2,
        )
        distance_u8 = np.clip(distance, 0, 255).astype(np.uint8)
        otsu_threshold, _ = cv2.threshold(
            distance_u8,
            0,
            255,
            cv2.THRESH_BINARY | cv2.THRESH_OTSU,
        )
        threshold = max(12.0, 0.65 * float(otsu_threshold))
        mask = (3.0 * distance >= threshold).astype(np.uint8) * 255
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        coverage = float(np.count_nonzero(mask)) / float(mask.size)
        if coverage < cls.MIN_GLYPH_COVERAGE or coverage > cls.MAX_GLYPH_COVERAGE:
            return np.full(mask.shape, 255, dtype=np.uint8)
        return mask

    @classmethod
    def _estimate_typography(
        cls,
        text: str,
        bbox_height: int,
        glyph_mask: np.ndarray,
        crop_rgb: np.ndarray,
        background_rgb: np.ndarray,
    ) -> dict[str, Any]:
        """Infer color, font family, size, weight, and outline properties."""

        foreground_rgb = cls._estimate_foreground_color(
            crop_rgb,
            glyph_mask,
            background_rgb,
        )
        coverage = float(np.count_nonzero(glyph_mask)) / float(glyph_mask.size)
        contains_cjk = any(cls._is_cjk(character) for character in text)
        single_chinese = len(text) == 1 and cls._is_chinese_ideograph(text)
        font_size_scale = 0.88 if single_chinese else 0.82
        font_size = max(8, int(round(font_size_scale * bbox_height)))

        foreground_luma = cls._rec709_luma(foreground_rgb)
        background_luma = cls._rec709_luma(background_rgb)
        stroke_color = "#1E2322" if foreground_luma >= background_luma else "#F0F4F1"
        stroke_scale = 0.045 if coverage > 0.12 else 0.03
        stroke_width = max(0, min(2, int(round(stroke_scale * bbox_height))))

        return {
            "fontFamily": "Microsoft YaHei" if contains_cjk else "Arial",
            "fontSize": font_size,
            "color": cls._rgb_to_hex(foreground_rgb),
            "fontWeight": 700 if coverage >= 0.17 else 600,
            "strokeColor": stroke_color,
            "strokeWidth": stroke_width,
        }

    @staticmethod
    def _estimate_foreground_color(
        crop_rgb: np.ndarray,
        glyph_mask: np.ndarray,
        background_rgb: np.ndarray,
    ) -> np.ndarray:
        """Select the highest-scoring 32-level RGB histogram bucket."""

        pixels = crop_rgb[glyph_mask > 0]
        if pixels.size == 0:  # Defensive; normal and fallback masks are nonempty.
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
        """Return whether a character belongs to a common CJK script/block."""

        if len(character) != 1:
            return False
        codepoint = ord(character)
        return (
            UITextExtractor._is_chinese_ideograph(character)
            or 0x3000 <= codepoint <= 0x303F  # CJK symbols and punctuation
            or 0x3040 <= codepoint <= 0x30FF  # Hiragana and Katakana
            or 0x31F0 <= codepoint <= 0x31FF  # Katakana phonetic extensions
            or 0x1100 <= codepoint <= 0x11FF  # Hangul Jamo
            or 0x3130 <= codepoint <= 0x318F  # Hangul compatibility Jamo
            or 0xAC00 <= codepoint <= 0xD7AF  # Hangul syllables
            or 0xFF66 <= codepoint <= 0xFF9D  # Halfwidth Katakana
        )

    @staticmethod
    def _is_chinese_ideograph(character: str) -> bool:
        """Return whether one character is a CJK unified ideograph."""

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
        image: np.ndarray,
        layers: list[dict[str, Any]],
    ) -> np.ndarray:
        """Draw each bbox and a compact OCR/style annotation on the source."""

        debug = image.copy()
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.42
        font_thickness = 1
        for layer in layers:
            rect = layer["rect"]
            style = layer["style"]
            x, y = int(rect["x"]), int(rect["y"])
            width, height = int(rect["width"]), int(rect["height"])
            rgb = cls._hex_to_rgb(style["color"])
            box_color = (rgb[2], rgb[1], rgb[0])
            cv2.rectangle(
                debug,
                (x, y),
                (x + width - 1, y + height - 1),
                box_color,
                2,
            )

            clean_text = str(layer["text"]).replace("\n", " ")[:40]
            # Hershey fonts are ASCII-only. Escaping non-ASCII keeps CJK labels
            # unambiguous without introducing a font-file/Pillow dependency.
            debug_text = clean_text.encode("ascii", "backslashreplace").decode("ascii")
            label = (
                f"{layer['id']} {debug_text} | {style['fontSize']}px "
                f"{style['color']} w{style['fontWeight']} s{style['strokeWidth']}"
            )
            (label_width, label_height), baseline = cv2.getTextSize(
                label,
                font,
                font_scale,
                font_thickness,
            )
            label_x = max(0, min(x, max(0, debug.shape[1] - label_width - 4)))
            if y >= label_height + baseline + 6:
                label_bottom = y - 2
            else:
                label_bottom = min(debug.shape[0] - baseline - 2, y + height + label_height + 4)
            label_top = max(0, label_bottom - label_height - baseline - 4)
            label_right = min(debug.shape[1] - 1, label_x + label_width + 4)
            cv2.rectangle(
                debug,
                (label_x, label_top),
                (label_right, min(debug.shape[0] - 1, label_bottom + baseline)),
                (20, 20, 20),
                cv2.FILLED,
            )
            cv2.putText(
                debug,
                label,
                (label_x + 2, max(label_height, label_bottom - baseline)),
                font,
                font_scale,
                (255, 255, 255),
                font_thickness,
                cv2.LINE_AA,
            )
        return debug

    @staticmethod
    def _hex_to_rgb(color: str) -> tuple[int, int, int]:
        value = color.lstrip("#")
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)

    @staticmethod
    def _write_json(path: Path, layers: list[dict[str, Any]]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(layers, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise RuntimeError(f"Unable to write JSON output: {path}") from exc

    @staticmethod
    def _write_image(path: Path, image: np.ndarray) -> None:
        suffix = path.suffix or ".png"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            success, encoded = cv2.imencode(suffix, image)
            if not success:
                raise RuntimeError(f"OpenCV cannot encode debug image as {suffix}")
            encoded.tofile(str(path))
        except (OSError, cv2.error) as exc:
            raise RuntimeError(f"Unable to write debug visualization: {path}") from exc


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract game UI text and inferred typography."
    )
    parser.add_argument("image", help="Input game UI screenshot")
    parser.add_argument("output_json", help="Output JSON path")
    parser.add_argument("debug_visualization", help="Output annotated image path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run a local extraction from the command line."""

    args = _parse_args(argv)
    try:
        extractor = UITextExtractor()
        layers = extractor.extract(
            args.image,
            args.output_json,
            args.debug_visualization,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Extracted {len(layers)} text layer(s) to {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
