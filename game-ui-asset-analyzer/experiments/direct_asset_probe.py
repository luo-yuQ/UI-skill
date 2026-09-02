#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_asset_analysis import map_bbox_to_source
from prepare_analysis_input import prepare_analysis_input
from production_visual_adapter import ProductionVisualAdapter
from runtime_geometry import read_image_size
from vlm_client import VLMClientConfig, VLMError, create_configured_vlm_client


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def draw_semantic_overlay(analysis_image: Path, result: dict[str, Any], output: Path) -> None:
    with Image.open(analysis_image) as opened:
        image = opened.convert("RGBA")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    for child in result.get("children", []):
        bbox = child["bbox"]
        x1 = bbox["x"]
        y1 = bbox["y"]
        x2 = x1 + bbox["width"]
        y2 = y1 + bbox["height"]
        label = f"{child['id']} {child['taxonomy']}"
        draw.rectangle((x1, y1, x2, y2), outline=(255, 64, 64, 255), width=3)
        text_box = draw.textbbox((0, 0), label, font=font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        label_y = max(0, y1 - text_height - 4)
        draw.rectangle(
            (x1, label_y, x1 + text_width + 6, label_y + text_height + 4),
            fill=(0, 0, 0, 210),
        )
        draw.text((x1 + 3, label_y + 2), label, fill=(255, 255, 0, 255), font=font)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output, format="PNG")


def build_probe_result(
    semantic_result: dict[str, Any],
    source_size: tuple[int, int],
    analysis_size: tuple[int, int],
) -> dict[str, Any]:
    children = []
    for child in semantic_result.get("children", []):
        children.append(
            {
                **child,
                "bbox_analysis": dict(child["bbox"]),
                "bbox_source": map_bbox_to_source(
                    child["bbox"], analysis_size, source_size
                ),
            }
        )
    return {
        "semantic_result": semantic_result,
        "source_size": {"width": source_size[0], "height": source_size[1]},
        "analysis_image_size": {
            "width": analysis_size[0],
            "height": analysis_size[1],
        },
        "children": children,
    }


def run_probe(image: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    source_output = output_dir / "source.png"
    analysis_output = output_dir / "analysis-image.png"
    metadata_output = output_dir / "analysis-image-meta.json"
    semantic_output = output_dir / "semantic-result.json"
    probe_output = output_dir / "probe-result.json"
    overlay_output = output_dir / "semantic-overlay.png"

    with Image.open(image) as source:
        source.convert("RGBA" if "A" in source.getbands() else "RGB").save(
            source_output, format="PNG"
        )
    prepare_analysis_input(
        image,
        analysis_output,
        metadata_output,
        max_width=1024,
        force_width=True,
    )
    config = VLMClientConfig.from_env()
    adapter = ProductionVisualAdapter(create_configured_vlm_client(config))
    adapter.bind_request(
        request_id="root_probe",
        node_id="root_probe",
        node_role="component_instance",
        adapter_kind="semantic_decompose",
        analysis_image=analysis_output.name,
    )
    semantic_result = adapter.semantic_decompose(
        analysis_output,
        node_id="root_probe",
    )
    write_json(semantic_output, semantic_result)
    source_size = read_image_size(image)
    analysis_size = read_image_size(analysis_output)
    probe_result = build_probe_result(semantic_result, source_size, analysis_size)
    write_json(probe_output, probe_result)
    draw_semantic_overlay(analysis_output, semantic_result, overlay_output)
    return semantic_result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe direct Stage2-A semantic asset decomposition on a clean UI image."
    )
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_probe(args.image, args.output_dir)
    except (OSError, UnicodeError, ValueError, VLMError) as exc:
        print(f"Direct asset probe failed: {exc}", file=sys.stderr)
        return 1
    print(f"Decision: {result['decision']}")
    print(f"Children: {len(result['children'])}")
    print(f"Wrote probe outputs to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
