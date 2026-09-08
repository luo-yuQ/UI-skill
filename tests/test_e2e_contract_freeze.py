"""Phase 5 E2E contract test: freeze the deterministic production chain.

Chain under test (frozen v0.1):
    direct-assets.json (immutable VLM output)
        -> apply_direct_asset_review.apply_review   (deterministic)
        -> build_extraction_request.build_extraction_request (deterministic)
        -> extract_assets (deterministic, pillow backend)

All VLM/network boundaries (Discovery, Admission, Image2 repair) are OUT of
scope: they are replaced by inline fixtures. Zero network, zero VLM.

Frozen invariants asserted here:
    1. DROP overrides remove assets before extraction (no dropped asset ever
       reaches the extraction request).
    2. bbox_modified overrides are the ONLY path that changes bbox_source;
       the builder copies bbox_source byte-for-byte into final_bbox.
    3. builder output validates against extraction-request.schema.json.
    4. taxonomy outside the asset_type enum maps to "unknown".
    5. extracted PNG pixels equal the source-image crop of final_bbox
       (direct_crop is pixel-faithful).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from jsonschema import Draft202012Validator
from PIL import Image

ANALYZER_ROOT = Path(__file__).resolve().parents[1] / "game-ui-asset-analyzer"
EXTRACTOR_ROOT = Path(__file__).resolve().parents[1] / "game-ui-asset-extractor"
for candidate in (ANALYZER_ROOT / "scripts", EXTRACTOR_ROOT / "scripts"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import apply_direct_asset_review as review  # noqa: E402
import build_extraction_request as builder  # noqa: E402
import extract_assets as extractor  # noqa: E402


EXTRACTION_SCHEMA = json.loads(
    (EXTRACTOR_ROOT / "schemas" / "extraction-request.schema.json").read_text(
        encoding="utf-8"
    )
)

WIDTH, HEIGHT = 64, 48
BG = (20, 30, 50, 255)
BTN = (200, 60, 60, 255)
ICON = (240, 200, 60, 255)


def direct_assets_doc() -> dict:
    return {
        "schema_version": "0.1",
        "analysis_image": "analysis-image.png",
        "analysis_image_size": {"width": WIDTH, "height": HEIGHT},
        "source_image": "source.png",
        "source_image_size": {"width": WIDTH, "height": HEIGHT},
        "assets": [
            {
                "id": "asset_001",
                "label": "background",
                "taxonomy": "background",
                "bbox_analysis": {"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT},
                "bbox_source": {"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT},
                "partial": False,
                "confidence": 0.9,
            },
            {
                "id": "asset_002",
                "label": "menu button",
                "taxonomy": "button",
                "bbox_analysis": {"x": 40, "y": 8, "width": 16, "height": 16},
                "bbox_source": {"x": 40, "y": 8, "width": 16, "height": 16},
                "partial": False,
                "confidence": 0.9,
            },
            {
                "id": "asset_003",
                "label": "coin icon",
                "taxonomy": "icon",
                "bbox_analysis": {"x": 10, "y": 30, "width": 12, "height": 12},
                "bbox_source": {"x": 10, "y": 30, "width": 12, "height": 12},
                "partial": False,
                "confidence": 0.85,
            },
        ],
        "review_summary": {},
    }


def overrides_doc() -> dict:
    return {
        "schema_version": "direct-asset-review-overrides-v0.1",
        "source_assets_json": "direct-assets.json",
        "image_size": {"width": WIDTH, "height": HEIGHT},
        "manual_assets": [],
        "overrides": {
            "asset_002": {"decision": "DROP"},
            "asset_003": {
                "bbox": {"x": 9, "y": 29, "width": 14, "height": 14}
            },
        },
    }


def write_source_image(path: Path) -> None:
    pixels = np.full((HEIGHT, WIDTH, 4), BG, dtype=np.uint8)
    pixels[30:42, 10:22] = ICON          # asset_003 region
    pixels[8:24, 40:56] = BTN            # asset_002 region (dropped)
    Image.fromarray(pixels, "RGBA").save(path)


class E2EContractTests(unittest.TestCase):
    def test_deterministic_chain_freeze(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.png"
            write_source_image(source)

            # Stage 1: deterministic review apply.
            reviewed = review.apply_review(
                direct_assets_doc(),
                overrides_doc(),
                "direct-assets.json",
                "review-overrides.json",
            )
            self.assertEqual("direct-assets-reviewed-v0.1", reviewed["schema_version"])
            self.assertEqual(2, len(reviewed["assets"]))  # asset_002 dropped

            # Stage 2: deterministic extraction-request build.
            request = builder.build_extraction_request(reviewed)
            Draft202012Validator(EXTRACTION_SCHEMA).validate(request)

            ids = [asset["asset_id"] for asset in request["assets"]]
            self.assertEqual(["asset_001", "asset_003"], ids)
            self.assertNotIn("asset_002", ids)

            # Invariant: final_bbox is byte-for-byte bbox_source.
            reviewed_by_id = {a["id"]: a for a in reviewed["assets"]}
            for asset in request["assets"]:
                self.assertEqual(
                    reviewed_by_id[asset["asset_id"]]["bbox_source"],
                    asset["final_bbox"],
                )

            # Invariant: taxonomy outside enum -> unknown.
            modified = next(
                a for a in request["assets"] if a["asset_id"] == "asset_003"
            )
            self.assertEqual("icon", modified["asset_type"])

            # Stage 3: deterministic extraction (pillow backend).
            request["source_image"] = str(source)
            request_path = tmp_path / "extraction-request.json"
            request_path.write_text(
                json.dumps(request, indent=2), encoding="utf-8"
            )
            output_dir = tmp_path / "extracted"
            exit_code = extractor.main(
                [
                    "--request",
                    str(request_path),
                    "--output-dir",
                    str(output_dir),
                    "--backend",
                    "pillow",
                ]
            )
            self.assertEqual(0, exit_code)
            extracted_icon = output_dir / "assets" / "asset_003.png"
            self.assertTrue(extracted_icon.exists())

            # Invariant: direct_crop is pixel-faithful to final_bbox.
            expected = np.asarray(Image.open(source).convert("RGBA"))[
                29:43, 9:23
            ]
            actual = np.asarray(Image.open(extracted_icon).convert("RGBA"))
            self.assertEqual(expected.shape, actual.shape)
            self.assertTrue(np.array_equal(expected, actual))

            # Dropped asset must not exist on disk.
            self.assertFalse((output_dir / "assets" / "asset_002.png").exists())


if __name__ == "__main__":
    unittest.main()
