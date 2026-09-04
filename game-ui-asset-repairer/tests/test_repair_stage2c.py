"""Deterministic tests for Stage2-C C1 + C1.5.

No SAM checkpoint, no VLM, no network. All inputs are synthesized.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

REPAIRER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPAIRER_DIR))

build_repair_relations = importlib.import_module("build_repair_relations")
prepare_repair_inputs = importlib.import_module("prepare_repair_inputs")
repair_geometry = importlib.import_module("repair_geometry")


def bbox(x, y, w, h):
    return {"x": x, "y": y, "width": w, "height": h}


def reviewed_doc(assets):
    return {
        "schema_version": "direct-assets-reviewed-v0.1",
        "source_image": "source.png",
        "assets": assets,
    }


def asset(aid, b, taxonomy="icon"):
    return {
        "id": aid,
        "label": taxonomy,
        "taxonomy": taxonomy,
        "bbox_source": b,
    }


# ---------------------------------------------------------------------------
# C1 — relation builder
# ---------------------------------------------------------------------------


class TestC1Relations:
    def test_strict_containment_generates_relation(self):
        reviewed = reviewed_doc(
            [asset("asset_025", bbox(10, 10, 100, 50)), asset("asset_026", bbox(20, 30, 10, 10))]
        )
        doc = build_repair_relations.build_relations(reviewed)
        assert len(doc["relations"]) == 1
        rel = doc["relations"][0]
        assert rel["target_asset_id"] == "asset_025"
        assert rel["occluder_asset_ids"] == ["asset_026"]
        assert rel["relation"] == "reviewed_bbox_containment"
        assert rel["geometry"]["occluder_containment_ratio"] == 1.0
        assert rel["geometry"]["intersection_area"] == 100

    def test_partial_overlap_no_relation(self):
        reviewed = reviewed_doc(
            [asset("a", bbox(10, 10, 100, 50)), asset("b", bbox(80, 30, 60, 40))]
        )
        doc = build_repair_relations.build_relations(reviewed)
        assert doc["relations"] == []

    def test_no_overlap_no_relation(self):
        reviewed = reviewed_doc(
            [asset("a", bbox(0, 0, 10, 10)), asset("b", bbox(50, 50, 10, 10))]
        )
        doc = build_repair_relations.build_relations(reviewed)
        assert doc["relations"] == []

    def test_multiple_occluders_aggregate_to_one_target(self):
        reviewed = reviewed_doc(
            [
                asset("A", bbox(0, 0, 100, 100)),
                asset("B", bbox(5, 5, 10, 10)),
                asset("C", bbox(50, 50, 20, 20)),
                asset("D", bbox(80, 80, 5, 5)),
            ]
        )
        doc = build_repair_relations.build_relations(reviewed)
        assert len(doc["relations"]) == 1
        rel = doc["relations"][0]
        assert rel["target_asset_id"] == "A"
        assert rel["occluder_asset_ids"] == ["B", "C", "D"]
        assert len(rel["geometry"]["per_occluder"]) == 3

    def test_reviewed_json_not_modified(self, tmp_path):
        reviewed = reviewed_doc(
            [asset("asset_025", bbox(10, 10, 100, 50)), asset("asset_026", bbox(20, 30, 10, 10))]
        )
        original = json.dumps(reviewed, sort_keys=True)
        path = tmp_path / "reviewed.json"
        path.write_text(original, encoding="utf-8")

        build_repair_relations.build_relations(reviewed)  # runs against the object
        # builder writes only to its own output file
        output = tmp_path / "repair-relations.json"
        output.write_text("{}", encoding="utf-8")

        assert json.dumps(reviewed, sort_keys=True) == original
        assert path.read_text(encoding="utf-8") == original

    def test_relations_output_schema_valid(self, tmp_path):
        reviewed = reviewed_doc(
            [asset("asset_025", bbox(10, 10, 100, 50)), asset("asset_026", bbox(20, 30, 10, 10))]
        )
        doc = build_repair_relations.build_relations(reviewed)
        errors = build_repair_relations.validate_relations(doc)
        assert errors == []

    def test_geometry_stats_reported_without_thresholds(self):
        # 90% overlap: still NO relation in v0.1 (strict containment only),
        # but the geometry statistics remain available for future evaluation.
        large = bbox(0, 0, 100, 100)
        small = bbox(10, 0, 100, 90)  # sticks out on the right: only 90% inside
        assert not repair_geometry.contains_strict(large, small)
        stats = repair_geometry.intersection_area(large, small)
        assert stats == 8100  # observable, unused by v0.1 decisions


# ---------------------------------------------------------------------------
# C1.5 — repair input preparation
# ---------------------------------------------------------------------------


def make_extraction_record(aid, b, output_rel, mask_rel, status="success"):
    return {
        "asset_id": aid,
        "asset_type": "unknown",
        "extraction_mode": "foreground_extract",
        "status": status,
        "source_image": "source.png",
        "final_bbox": b,
        "extraction_roi": dict(b),  # tight crop: frame == bbox
        "roi_padding": 0,
        "final_bbox_offset": {"x": 0, "y": 0},
        "output_path": output_rel,
        "mask_path": mask_rel,
    }


def write_png(path, array, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array, mode=mode).save(path, format="PNG")


class TestC15Preparation:
    def build_world(
        self,
        tmp_path,
        target_bbox,
        target_mask,
        occluders,  # list of (aid, bbox, mask)
        target_status="success",
        target_bbox_override=None,
    ):
        """Create reviewed JSON + extraction-result.json + PNG files."""

        out_dir = tmp_path / "stage2b"
        out_dir.mkdir(parents=True, exist_ok=True)

        assets = [asset("asset_025", target_bbox)]
        records = []
        target_frame_bbox = target_bbox_override or target_bbox

        rgba = np.zeros((target_frame_bbox["height"], target_frame_bbox["width"], 4), dtype=np.uint8)
        rgba[..., :3] = 200
        rgba[..., 3] = np.where(target_mask, 255, 0)
        write_png(out_dir / "assets" / "asset_025.png", rgba, "RGBA")
        mask_img = np.where(target_mask, 255, 0).astype(np.uint8)
        write_png(out_dir / "masks" / "asset_025_mask.png", mask_img, "L")
        records.append(
            make_extraction_record(
                "asset_025",
                target_frame_bbox,
                "assets/asset_025.png",
                "masks/asset_025_mask.png",
                target_status,
            )
        )

        for aid, ob, omask in occluders:
            assets.append(asset(aid, ob))
            om = np.where(omask, 255, 0).astype(np.uint8)
            write_png(out_dir / "masks" / f"{aid}_mask.png", om, "L")
            write_png(
                out_dir / "assets" / f"{aid}.png",
                np.zeros((ob["height"], ob["width"], 4), dtype=np.uint8),
                "RGBA",
            )
            records.append(
                make_extraction_record(
                    aid,
                    ob,
                    f"assets/{aid}.png",
                    f"masks/{aid}_mask.png",
                )
            )

        extraction_result = {
            "schema_version": "0.1",
            "status": "success",
            "source_image": "source.png",
            "source_size": {"width": 1024, "height": 1536},
            "backend": "sam1_vit_b",
            "config": {},
            "assets": records,
        }
        result_path = out_dir / "extraction-result.json"
        result_path.write_text(json.dumps(extraction_result), encoding="utf-8")

        reviewed_path = tmp_path / "reviewed.json"
        reviewed_path.write_text(json.dumps(reviewed_doc(assets)), encoding="utf-8")

        return reviewed_path, result_path

    def run_prepare(self, tmp_path, reviewed_path, result_path):
        relations = {
            "schema_version": "repair-relations-v0.1",
            "source_image": "source.png",
            "relations": [
                {
                    "target_asset_id": "asset_025",
                    "occluder_asset_ids": ["asset_026"] if False else [],
                    "relation": "reviewed_bbox_containment",
                }
            ],
        }
        # build relations through the real builder for consistency
        rel_doc = build_repair_relations.build_relations(json.loads(reviewed_path.read_text()))
        repair_dir = tmp_path / "repair"
        doc = prepare_repair_inputs.prepare_all(
            json.loads(reviewed_path.read_text()),
            rel_doc,
            result_path,
            repair_dir,
        )
        return doc, repair_dir

    def test_single_occluder_projection_and_outputs(self, tmp_path):
        target_bbox = bbox(100, 200, 60, 40)
        target_mask = np.ones((40, 60), dtype=bool)
        occluder_bbox = bbox(110, 210, 20, 10)
        occluder_mask = np.ones((10, 20), dtype=bool)

        reviewed_path, result_path = self.build_world(
            tmp_path, target_bbox, target_mask, [("asset_026", occluder_bbox, occluder_mask)]
        )
        doc, repair_dir = self.run_prepare(tmp_path, reviewed_path, result_path)

        entry = doc["targets"][0]
        assert entry["status"] == "success", entry
        assert entry["reason_code"] == "ok"

        repair_mask = np.asarray(Image.open(repair_dir / "asset_025" / "repair-mask.png").convert("L"))
        # projected region: target-local (10..30, 10..20) => all white, rest black
        expected = np.zeros((40, 60), dtype=np.uint8)
        expected[10:20, 10:30] = 255
        assert np.array_equal(repair_mask, expected)

        working = np.asarray(Image.open(repair_dir / "asset_025" / "repair-working-image.png").convert("RGBA"))
        # inside repair mask: RGB == 0, alpha untouched (255)
        assert (working[10:20, 10:30, :3] == 0).all()
        assert (working[10:20, 10:30, 3] == 255).all()

        # outside repair mask: byte-identical to Stage2-B target output
        stage2b = np.asarray(Image.open(tmp_path / "stage2b" / "assets" / "asset_025.png").convert("RGBA"))
        outside = ~np.asarray(Image.open(repair_dir / "asset_025" / "repair-mask.png").convert("L")).astype(bool)
        assert np.array_equal(working[outside], stage2b[outside])

        # transparent region of target preserved as transparent
        target_mask_full = np.asarray(Image.open(tmp_path / "stage2b" / "masks" / "asset_025_mask.png").convert("L")) > 127
        assert (working[~target_mask_full, 3] == 0).all()

        repair_input = json.loads((repair_dir / "asset_025" / "repair-input.json").read_text())
        assert repair_input["schema_version"] == "repair-input-v0.2"
        assert repair_input["repair_pixel_count"] == 200
        assert repair_input["repair_ratio_of_target_mask"] == pytest.approx(200 / (40 * 60))
        assert repair_input["occluder_masks"][0]["asset_id"] == "asset_026"

    def test_multi_occluder_union(self, tmp_path):
        target_bbox = bbox(100, 200, 60, 40)
        target_mask = np.ones((40, 60), dtype=bool)
        occluders = [
            ("asset_026", bbox(100, 200, 10, 10), np.ones((10, 10), dtype=bool)),
            ("asset_027", bbox(130, 220, 10, 10), np.ones((10, 10), dtype=bool)),
        ]
        reviewed_path, result_path = self.build_world(tmp_path, target_bbox, target_mask, occluders)
        doc, repair_dir = self.run_prepare(tmp_path, reviewed_path, result_path)

        entry = doc["targets"][0]
        assert entry["status"] == "success"
        repair_mask = np.asarray(Image.open(repair_dir / "asset_025" / "repair-mask.png").convert("L"))
        expected = np.zeros((40, 60), dtype=np.uint8)
        expected[0:10, 0:10] = 255       # occluder A at target-local origin
        expected[20:30, 30:40] = 255     # occluder B
        assert np.array_equal(repair_mask, expected)

    def test_repair_mask_not_limited_by_target_mask(self, tmp_path):
        # v0.2: the target's own mask is no longer a repair ownership
        # boundary. An occluder over a transparent hole in the target mask
        # still owns its full projected area inside the target frame.
        target_bbox = bbox(100, 200, 60, 40)
        target_mask = np.ones((40, 60), dtype=bool)
        target_mask[10:20, 10:30] = False  # transparent hole exactly under occluder
        occluders = [("asset_026", bbox(110, 210, 20, 10), np.ones((10, 20), dtype=bool))]
        reviewed_path, result_path = self.build_world(tmp_path, target_bbox, target_mask, occluders)
        doc, repair_dir = self.run_prepare(tmp_path, reviewed_path, result_path)

        entry = doc["targets"][0]
        assert entry["status"] == "success"
        repair_mask = np.asarray(Image.open(repair_dir / "asset_025" / "repair-mask.png").convert("L"))
        expected = np.zeros((40, 60), dtype=np.uint8)
        expected[10:20, 10:30] = 255  # full projected area, hole or not
        assert np.array_equal(repair_mask, expected)
        assert entry["reason_code"] == "ok"

    def test_transparent_target_region_also_repair(self, tmp_path):
        # v0.2: partial overlap with a target-mask hole — the hole part is
        # also repair area now (occluder owns its whole projection).
        target_bbox = bbox(100, 200, 60, 40)
        target_mask = np.ones((40, 60), dtype=bool)
        target_mask[10:20, 20:30] = False  # hole at target-local x 20..30
        occluders = [("asset_026", bbox(110, 210, 20, 10), np.ones((10, 20), dtype=bool))]
        reviewed_path, result_path = self.build_world(tmp_path, target_bbox, target_mask, occluders)
        doc, repair_dir = self.run_prepare(tmp_path, reviewed_path, result_path)

        entry = doc["targets"][0]
        assert entry["status"] == "success"
        repair_mask = np.asarray(Image.open(repair_dir / "asset_025" / "repair-mask.png").convert("L"))
        expected = np.zeros((40, 60), dtype=np.uint8)
        expected[10:20, 10:30] = 255  # opaque half AND the hole half
        assert np.array_equal(repair_mask, expected)

    def test_white_black_semantics(self, tmp_path):
        target_bbox = bbox(0, 0, 30, 30)
        target_mask = np.ones((30, 30), dtype=bool)
        occluders = [("asset_026", bbox(5, 5, 10, 10), np.ones((10, 10), dtype=bool))]
        reviewed_path, result_path = self.build_world(tmp_path, target_bbox, target_mask, occluders)
        doc, repair_dir = self.run_prepare(tmp_path, reviewed_path, result_path)

        mask = np.asarray(Image.open(repair_dir / "asset_025" / "repair-mask.png").convert("L"))
        # binary: only 0 and 255
        assert set(np.unique(mask)).issubset({0, 255})
        # white == repair == placeholder region; black == preserve
        assert mask[5:15, 5:15].min() == 255
        assert mask[0:5, 0:5].max() == 0

    def test_working_image_identical_outside_mask(self, tmp_path):
        target_bbox = bbox(0, 0, 20, 20)
        target_mask = np.ones((20, 20), dtype=bool)
        occluders = [("asset_026", bbox(2, 2, 5, 5), np.ones((5, 5), dtype=bool))]
        reviewed_path, result_path = self.build_world(tmp_path, target_bbox, target_mask, occluders)
        doc, repair_dir = self.run_prepare(tmp_path, reviewed_path, result_path)

        working = np.asarray(Image.open(repair_dir / "asset_025" / "repair-working-image.png").convert("RGBA"))
        stage2b = np.asarray(Image.open(tmp_path / "stage2b" / "assets" / "asset_025.png").convert("RGBA"))
        repair_mask = np.asarray(Image.open(repair_dir / "asset_025" / "repair-mask.png").convert("L")).astype(bool)
        assert np.array_equal(working[~repair_mask], stage2b[~repair_mask])

    def test_mask_size_mismatch_fails(self, tmp_path):
        target_bbox = bbox(0, 0, 20, 20)
        target_mask = np.ones((20, 20), dtype=bool)
        occluders = [("asset_026", bbox(5, 5, 10, 10), np.ones((8, 12), dtype=bool))]  # wrong size
        reviewed_path, result_path = self.build_world(tmp_path, target_bbox, target_mask, occluders)
        doc, repair_dir = self.run_prepare(tmp_path, reviewed_path, result_path)
        entry = doc["targets"][0]
        assert entry["status"] == "failed"
        assert entry["reason_code"] == "mask_size_mismatch"

    def test_target_asset_size_mismatch_fails(self, tmp_path):
        target_bbox = bbox(0, 0, 20, 20)
        out_dir = tmp_path / "stage2b"
        out_dir.mkdir(parents=True, exist_ok=True)
        # write a wrong-size asset PNG (10x10 instead of 20x20)
        write_png(out_dir / "assets" / "asset_025.png", np.zeros((10, 10, 4), dtype=np.uint8), "RGBA")
        write_png(out_dir / "masks" / "asset_025_mask.png", np.ones((20, 20), dtype=np.uint8) * 255, "L")

        assets = [asset("asset_025", target_bbox), asset("asset_026", bbox(5, 5, 5, 5))]
        records = [
            make_extraction_record("asset_025", target_bbox, "assets/asset_025.png", "masks/asset_025_mask.png"),
            make_extraction_record("asset_026", bbox(5, 5, 5, 5), "assets/asset_026.png", "masks/asset_026_mask.png"),
        ]
        write_png(out_dir / "assets" / "asset_026.png", np.zeros((5, 5, 4), dtype=np.uint8), "RGBA")
        write_png(out_dir / "masks" / "asset_026_mask.png", np.ones((5, 5), dtype=np.uint8) * 255, "L")
        extraction_result = {
            "schema_version": "0.1", "status": "success", "source_image": "source.png",
            "source_size": {"width": 100, "height": 100}, "backend": "sam1_vit_b", "config": {},
            "assets": records,
        }
        result_path = out_dir / "extraction-result.json"
        result_path.write_text(json.dumps(extraction_result), encoding="utf-8")
        reviewed_path = tmp_path / "reviewed.json"
        reviewed_path.write_text(json.dumps(reviewed_doc(assets)), encoding="utf-8")

        relations = {
            "schema_version": "repair-relations-v0.1",
            "source_image": "source.png",
            "relations": [
                {
                    "target_asset_id": "asset_025",
                    "occluder_asset_ids": ["asset_026"],
                    "relation": "reviewed_bbox_containment",
                }
            ],
        }
        doc = prepare_repair_inputs.prepare_all(
            json.loads(reviewed_path.read_text()), relations, result_path, tmp_path / "repair"
        )
        entry = doc["targets"][0]
        assert entry["status"] == "failed"
        assert entry["reason_code"] == "asset_image_size_mismatch"

    def test_missing_extraction_result(self, tmp_path):
        target_bbox = bbox(0, 0, 20, 20)
        target_mask = np.ones((20, 20), dtype=bool)
        reviewed_path, result_path = self.build_world(tmp_path, target_bbox, target_mask, [])
        relations = {
            "schema_version": "repair-relations-v0.1",
            "source_image": "source.png",
            "relations": [
                {
                    "target_asset_id": "asset_099",
                    "occluder_asset_ids": ["asset_026"],
                    "relation": "reviewed_bbox_containment",
                }
            ],
        }
        doc = prepare_repair_inputs.prepare_all(
            json.loads(reviewed_path.read_text()), relations, result_path, tmp_path / "repair"
        )
        entry = doc["targets"][0]
        assert entry["status"] == "failed"
        assert entry["reason_code"] == "target_extraction_result_missing"

    def test_missing_occluder_mask_file(self, tmp_path):
        target_bbox = bbox(0, 0, 20, 20)
        target_mask = np.ones((20, 20), dtype=bool)
        reviewed_path, result_path = self.build_world(tmp_path, target_bbox, target_mask, [])
        # register occluder in extraction result but delete its mask file
        extraction_result = json.loads(result_path.read_text())
        records = extraction_result["assets"]
        records.append(
            make_extraction_record("asset_026", bbox(2, 2, 5, 5), "assets/asset_026.png", "masks/asset_026_mask.png")
        )
        extraction_result["assets"] = records
        result_path.write_text(json.dumps(extraction_result), encoding="utf-8")

        relations = {
            "schema_version": "repair-relations-v0.1",
            "source_image": "source.png",
            "relations": [
                {
                    "target_asset_id": "asset_025",
                    "occluder_asset_ids": ["asset_026"],
                    "relation": "reviewed_bbox_containment",
                }
            ],
        }
        doc = prepare_repair_inputs.prepare_all(
            json.loads(reviewed_path.read_text()), relations, result_path, tmp_path / "repair"
        )
        entry = doc["targets"][0]
        assert entry["status"] == "failed"
        assert entry["reason_code"] == "occluder_mask_file_missing"

    def test_no_overlap_after_mapping(self, tmp_path):
        # occluder bbox lies outside the target frame after mapping
        target_bbox = bbox(100, 100, 20, 20)
        target_mask = np.ones((20, 20), dtype=bool)
        # occluder recorded with a frame disjoint from target (data-level error)
        occluders = [("asset_026", bbox(500, 500, 5, 5), np.ones((5, 5), dtype=bool))]
        reviewed_path, result_path = self.build_world(tmp_path, target_bbox, target_mask, occluders)
        relations = {
            "schema_version": "repair-relations-v0.1",
            "source_image": "source.png",
            "relations": [
                {
                    "target_asset_id": "asset_025",
                    "occluder_asset_ids": ["asset_026"],
                    "relation": "reviewed_bbox_containment",
                }
            ],
        }
        doc = prepare_repair_inputs.prepare_all(
            json.loads(reviewed_path.read_text()), relations, result_path, tmp_path / "repair"
        )
        entry = doc["targets"][0]
        assert entry["status"] == "failed"
        assert entry["reason_code"] == "no_coordinate_overlap"

    def test_target_extraction_failed(self, tmp_path):
        target_bbox = bbox(0, 0, 20, 20)
        target_mask = np.ones((20, 20), dtype=bool)
        occluders = [("asset_026", bbox(2, 2, 5, 5), np.ones((5, 5), dtype=bool))]
        reviewed_path, result_path = self.build_world(
            tmp_path, target_bbox, target_mask, occluders, target_status="failed"
        )
        doc, _ = self.run_prepare(tmp_path, reviewed_path, result_path)
        entry = doc["targets"][0]
        assert entry["status"] == "failed"
        assert entry["reason_code"] == "target_extraction_failed"

    def test_invalid_bbox_rejected(self):
        with pytest.raises(repair_geometry.InvalidBboxError):
            repair_geometry.require_valid_bbox({"x": 0, "y": 0, "width": 0, "height": 10}, "t")
        with pytest.raises(repair_geometry.InvalidBboxError):
            repair_geometry.require_valid_bbox({"x": -1, "y": 0, "width": 5, "height": 5}, "t")

    def test_no_sam_checkpoint_required(self):
        # whole module imports and runs without torch / segment_anything
        assert "torch" not in sys.modules
        assert "segment_anything" not in sys.modules

    def test_padded_roi_projection(self, tmp_path):
        """Production extractor ROI shape: target frame = bbox + 10px padding."""

        target_bbox = bbox(50, 60, 40, 30)          # final_bbox (source pixels)
        roi = bbox(40, 50, 60, 50)                   # extraction_roi (padding 10)
        target_mask = np.zeros((50, 60), dtype=bool)
        target_mask[10:40, 10:50] = True             # core == final_bbox region
        occluder_bbox = bbox(60, 70, 10, 8)          # strictly inside final_bbox
        occluder_mask = np.ones((8, 10), dtype=bool)

        out_dir = tmp_path / "stage2b"
        out_dir.mkdir(parents=True, exist_ok=True)
        rgba = np.zeros((50, 60, 4), dtype=np.uint8)
        rgba[..., :3] = 100
        rgba[..., 3] = np.where(target_mask, 255, 0)
        write_png(out_dir / "assets" / "asset_025.png", rgba, "RGBA")
        write_png(out_dir / "masks" / "asset_025_mask.png", np.where(target_mask, 255, 0).astype(np.uint8), "L")
        write_png(out_dir / "masks" / "asset_026_mask.png", np.ones((8, 10), dtype=np.uint8) * 255, "L")

        records = [
            make_extraction_record("asset_025", target_bbox, "assets/asset_025.png", "masks/asset_025_mask.png"),
            make_extraction_record("asset_026", occluder_bbox, "assets/asset_026.png", "masks/asset_026_mask.png"),
        ]
        # production ROI: record.extraction_roi differs from final_bbox
        records[0]["extraction_roi"] = dict(roi)
        extraction_result = {
            "schema_version": "0.1", "status": "success", "source_image": "source.png",
            "source_size": {"width": 200, "height": 200}, "backend": "sam1_vit_b", "config": {},
            "assets": records,
        }
        result_path = out_dir / "extraction-result.json"
        result_path.write_text(json.dumps(extraction_result), encoding="utf-8")
        reviewed_path = tmp_path / "reviewed.json"
        reviewed_path.write_text(
            json.dumps(reviewed_doc([asset("asset_025", target_bbox), asset("asset_026", occluder_bbox)])),
            encoding="utf-8",
        )

        rel_doc = build_repair_relations.build_relations(json.loads(reviewed_path.read_text()))
        doc = prepare_repair_inputs.prepare_all(
            json.loads(reviewed_path.read_text()), rel_doc, result_path, tmp_path / "repair"
        )
        entry = doc["targets"][0]
        assert entry["status"] == "success", entry

        repair_mask = np.asarray(Image.open(tmp_path / "repair" / "asset_025" / "repair-mask.png").convert("L"))
        # occluder source (60..70, 70..78) -> target-local (minus roi origin 40,50)
        # = (20..30, 20..28)
        expected = np.zeros((50, 60), dtype=np.uint8)
        expected[20:28, 20:30] = 255
        assert np.array_equal(repair_mask, expected)
