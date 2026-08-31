from __future__ import annotations

import json
from pathlib import Path
from typing import Tuple, Optional

from PIL import Image

PRESETS = {
    "square": (1024, 1024),
    "portrait": (1024, 1536),
    "landscape": (1536, 1024),
}


def compute_fit_transform(
    source_size: Tuple[int, int],
    target_size: Tuple[int, int],
) -> dict:
    src_w, src_h = source_size
    tgt_w, tgt_h = target_size

    scale = min(tgt_w / src_w, tgt_h / src_h)

    new_w = round(src_w * scale)
    new_h = round(src_h * scale)

    pad_left = (tgt_w - new_w) // 2
    pad_top = (tgt_h - new_h) // 2
    pad_right = tgt_w - new_w - pad_left
    pad_bottom = tgt_h - new_h - pad_top

    return {
        "source_width": src_w,
        "source_height": src_h,
        "target_width": tgt_w,
        "target_height": tgt_h,
        "scale": scale,
        "scaled_width": new_w,
        "scaled_height": new_h,
        "pad_left": pad_left,
        "pad_top": pad_top,
        "pad_right": pad_right,
        "pad_bottom": pad_bottom,
    }


def apply_transform(
    image: Image.Image,
    transform: dict,
    *,
    pad_color,
    resample,
) -> Image.Image:
    target_w = transform["target_width"]
    target_h = transform["target_height"]
    new_w = transform["scaled_width"]
    new_h = transform["scaled_height"]
    pad_left = transform["pad_left"]
    pad_top = transform["pad_top"]

    resized = image.resize((new_w, new_h), resample=resample)

    canvas = Image.new(image.mode, (target_w, target_h), color=pad_color)
    canvas.paste(resized, (pad_left, pad_top))
    return canvas


def open_image(path: Path) -> Image.Image:
    image = Image.open(path)
    image.load()
    return image


def prepare_images(
    source_path: Path,
    overlay_path: Optional[Path],
    output_dir: Path,
    target_size: Tuple[int, int],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    source = open_image(source_path)
    source_rgba = source.convert("RGBA")

    transform = compute_fit_transform(source_rgba.size, target_size)

    # source 用高质量缩放，padding 黑色
    prepared_source = apply_transform(
        source_rgba,
        transform,
        pad_color=(0, 0, 0, 255),
        resample=Image.Resampling.LANCZOS,
    )
    prepared_source_path = output_dir / "source_prepared.png"
    prepared_source.save(prepared_source_path)

    prepared_overlay_path = None
    if overlay_path is not None:
        overlay = open_image(overlay_path).convert("RGBA")
        if overlay.size != source_rgba.size:
            raise ValueError(
                f"overlay 尺寸必须和 source 一致，"
                f"当前 overlay={overlay.size}, source={source_rgba.size}"
            )

        # overlay 更适合 NEAREST，避免边缘糊掉
        prepared_overlay = apply_transform(
            overlay,
            transform,
            pad_color=(0, 0, 0, 0),  # overlay padding 透明
            resample=Image.Resampling.NEAREST,
        )
        prepared_overlay_path = output_dir / "overlay_prepared.png"
        prepared_overlay.save(prepared_overlay_path)

    metadata = {
        "mode": (
            "square"
            if target_size == PRESETS["square"]
            else "portrait"
            if target_size == PRESETS["portrait"]
            else "landscape"
            if target_size == PRESETS["landscape"]
            else "custom"
        ),
        "source_image": str(source_path),
        "overlay_image": str(overlay_path) if overlay_path else None,
        "prepared_source_image": str(prepared_source_path),
        "prepared_overlay_image": str(prepared_overlay_path) if prepared_overlay_path else None,
        "transform": transform,
    }

    metadata_path = output_dir / "transform.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    print("完成。")
    print(f"prepared source : {prepared_source_path}")
    if prepared_overlay_path:
        print(f"prepared overlay: {prepared_overlay_path}")
    print(f"metadata        : {metadata_path}")
    print()
    print("transform =")
    print(json.dumps(transform, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    # ===== 这里直接改 =====
    source_path = Path(r"D:\Third_Test_1\UI-skill\runs\20260824_stage0_text_extract_003\inputs\analysis-image.png")
    overlay_path = Path(r"D:\Third_Test_1\UI-skill\runs\20260824_stage0_text_extract_003\outputs\b_6\region-mask-overlay.png")
    output_dir = Path(r"D:\Third_Test_1\UI-skill\runs\20260824_stage0_text_extract_003\outputs\prepared_for_image2_01")

    # 竖屏档：1024x1536
    target_size = PRESETS["portrait"]

    prepare_images(
        source_path=source_path,
        overlay_path=overlay_path,
        output_dir=output_dir,
        target_size=target_size,
    )