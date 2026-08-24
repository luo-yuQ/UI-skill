import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import validate_semantic_decomposition as validator


def make_decomposition():
    return {
        "node_id": "instance_001",
        "node_role": "component_instance",
        "task": "semantic_decompose",
        "bbox_constraint": "completeness",
        "analysis_image_size": {"width": 1024, "height": 1019},
        "decision": "decompose",
        "children": [
            {
                "id": "asset_001",
                "label": "item artwork",
                "taxonomy": "illustration",
                "bbox": {"x": 100, "y": 100, "width": 700, "height": 700},
                "partial": False,
                "confidence": 0.98,
            },
            {
                "id": "asset_002",
                "label": "runtime quantity",
                "taxonomy": "text",
                "bbox": {"x": 690, "y": 680, "width": 150, "height": 90},
                "partial": False,
                "confidence": 0.96,
            },
        ],
        "reason": "Artwork and runtime quantity are independent direct assets.",
    }


def make_panel_icon_decomposition():
    data = make_decomposition()
    data["children"] = [
        {
            "id": "panel_001",
            "label": "green rounded button base",
            "taxonomy": "panel",
            "bbox": {"x": 0, "y": 0, "width": 1024, "height": 1019},
            "partial": False,
            "confidence": 0.98,
        },
        {
            "id": "icon_001",
            "label": "potion bottle icon",
            "taxonomy": "icon",
            "bbox": {"x": 390, "y": 210, "width": 244, "height": 500},
            "partial": False,
            "confidence": 0.97,
        },
    ]
    data["reason"] = (
        "The panel base and potion icon are distinguishable UI components."
    )
    return data


def make_atomic_stop(taxonomy):
    data = make_decomposition()
    data["decision"] = "stop_as_asset"
    data["asset_taxonomy"] = taxonomy
    data["children"] = []
    data["reason"] = (
        f"The current node is one atomic {taxonomy} with no component-level split."
    )
    return data


class SemanticDecompositionTests(unittest.TestCase):
    def make_image(self, size=(1024, 1019)):
        context = tempfile.TemporaryDirectory()
        image_path = Path(context.name) / "analysis-image.png"
        Image.new("RGB", size, "navy").save(image_path)
        self.addCleanup(context.cleanup)
        return image_path

    def test_case_a_panel_plus_icon_is_a_valid_decomposition(self):
        data = make_panel_icon_decomposition()
        self.assertEqual("decompose", data["decision"])
        self.assertGreaterEqual(len(data["children"]), 2)
        self.assertEqual(
            {"panel", "icon"},
            {child["taxonomy"] for child in data["children"]},
        )
        self.assertNotIn("asset_taxonomy", data)
        self.assertEqual([], validator.validate_document(data, self.make_image()))

    def test_cases_b_and_c_atomic_icon_or_panel_are_valid_stops(self):
        for taxonomy in ("icon", "panel"):
            with self.subTest(taxonomy=taxonomy):
                data = make_atomic_stop(taxonomy)
                self.assertEqual("stop_as_asset", data["decision"])
                self.assertEqual(taxonomy, data["asset_taxonomy"])
                self.assertEqual([], data["children"])
                self.assertEqual(
                    [], validator.validate_document(data, self.make_image())
                )

    def test_case_d_panel_icon_and_text_is_a_valid_decomposition(self):
        data = make_panel_icon_decomposition()
        data["children"].append(
            {
                "id": "text_001",
                "label": "potion action label",
                "taxonomy": "text",
                "bbox": {"x": 320, "y": 820, "width": 384, "height": 96},
                "partial": False,
                "confidence": 0.96,
            }
        )
        data["reason"] = (
            "The panel base, potion icon, and text are distinguishable UI components."
        )
        self.assertEqual("decompose", data["decision"])
        self.assertEqual(
            {"panel", "icon", "text"},
            {child["taxonomy"] for child in data["children"]},
        )
        self.assertEqual([], validator.validate_document(data, self.make_image()))

    def test_case_e_full_parent_panel_and_nested_icon_overlap_is_valid(self):
        data = make_panel_icon_decomposition()
        by_taxonomy = {child["taxonomy"]: child for child in data["children"]}
        panel_bbox = by_taxonomy["panel"]["bbox"]
        icon_bbox = by_taxonomy["icon"]["bbox"]

        self.assertEqual(
            {
                "x": 0,
                "y": 0,
                "width": data["analysis_image_size"]["width"],
                "height": data["analysis_image_size"]["height"],
            },
            panel_bbox,
        )
        self.assertGreaterEqual(icon_bbox["x"], panel_bbox["x"])
        self.assertGreaterEqual(icon_bbox["y"], panel_bbox["y"])
        self.assertLessEqual(
            icon_bbox["x"] + icon_bbox["width"],
            panel_bbox["x"] + panel_bbox["width"],
        )
        self.assertLessEqual(
            icon_bbox["y"] + icon_bbox["height"],
            panel_bbox["y"] + panel_bbox["height"],
        )
        self.assertEqual("decompose", data["decision"])
        self.assertEqual([], validator.validate_document(data, self.make_image()))

    def test_stop_as_asset_requires_empty_children_and_taxonomy(self):
        data = make_decomposition()
        data["decision"] = "stop_as_asset"
        data["children"] = []
        data["asset_taxonomy"] = "icon"
        self.assertEqual([], validator.validate_document(data, self.make_image()))

        invalid = copy.deepcopy(data)
        invalid.pop("asset_taxonomy")
        errors = validator.validate_document(invalid, self.make_image())
        self.assertTrue(any("asset_taxonomy" in error for error in errors))

    def test_decompose_rejects_empty_children_and_asset_taxonomy(self):
        data = make_decomposition()
        data["children"] = []
        errors = validator.validate_document(data, self.make_image())
        self.assertTrue(any("non-empty" in error for error in errors))

        data = make_decomposition()
        data["asset_taxonomy"] = "illustration"
        errors = validator.validate_document(data, self.make_image())
        self.assertTrue(any("asset_taxonomy" in error for error in errors))

    def test_bbox_outside_actual_analysis_image_is_not_corrected(self):
        data = make_decomposition()
        data["children"][0]["bbox"] = {
            "x": 900,
            "y": 900,
            "width": 200,
            "height": 200,
        }
        errors = validator.validate_document(data, self.make_image())
        self.assertTrue(any("right edge 1100" in error for error in errors))
        self.assertTrue(any("bottom edge 1100" in error for error in errors))
        self.assertEqual(200, data["children"][0]["bbox"]["width"])

    def test_actual_analysis_image_size_must_match_document(self):
        errors = validator.validate_document(
            make_decomposition(), self.make_image((1024, 1000))
        )
        self.assertTrue(any("does not match actual" in error for error in errors))

    def test_bbox_bounds_still_use_real_image_when_declared_size_is_invalid(self):
        data = make_decomposition()
        data["analysis_image_size"]["height"] = "1019"
        data["children"][0]["bbox"] = {
            "x": 900,
            "y": 900,
            "width": 200,
            "height": 200,
        }
        errors = validator.validate_document(data, self.make_image())
        self.assertTrue(any("is not of type 'integer'" in error for error in errors))
        self.assertTrue(any("right edge 1100" in error for error in errors))
        self.assertTrue(any("bottom edge 1100" in error for error in errors))

    def test_taxonomy_confidence_and_node_role_are_frozen(self):
        for field, value in (
            ("taxonomy", "logo"),
            ("confidence", 1.01),
        ):
            with self.subTest(field=field):
                data = make_decomposition()
                data["children"][0][field] = value
                self.assertNotEqual(
                    [], validator.validate_document(data, self.make_image())
                )
        data = make_decomposition()
        data["node_role"] = "asset"
        self.assertNotEqual([], validator.validate_document(data, self.make_image()))

    def test_duplicate_child_ids_fail(self):
        data = make_decomposition()
        data["children"][1]["id"] = data["children"][0]["id"]
        errors = validator.validate_document(data, self.make_image())
        self.assertTrue(any("duplicate child id" in error for error in errors))

    def test_cli_validates_against_real_image(self):
        image_path = self.make_image()
        document = image_path.parent / "semantic-decomposition.json"
        document.write_text(
            json.dumps(make_decomposition(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "validate_semantic_decomposition.py"),
                str(document),
                "--analysis-image",
                str(image_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Valid semantic decomposition v0.1", result.stdout)


if __name__ == "__main__":
    unittest.main()
