from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_route_action_result as validator  # noqa: E402


PARENT_SIZE = (1024, 512)


def structural_child(
    child_id: str, x: int, y: int, width: int = 240, height: int = 180
) -> dict:
    return {
        "id": child_id,
        "label": child_id,
        "bbox": {"x": x, "y": y, "width": width, "height": height},
        "confidence": 0.95,
    }


def instance(
    instance_id: str, x: int, y: int, width: int = 150, height: int = 140
) -> dict:
    return {
        "id": instance_id,
        "bbox": {"x": x, "y": y, "width": width, "height": height},
        "partial_instance": False,
        "confidence": 0.95,
    }


class RouteActionEffectivenessTests(unittest.TestCase):
    def test_no_useful_structural_split_is_contract_valid_but_ineffective(self):
        result = validator.validate_structural_split_result(
            {
                "no_useful_structural_split": True,
                "children": [],
                "reason": "No direct structural children.",
            },
            PARENT_SIZE,
        )
        self.assertFalse(result["valid"])
        self.assertEqual("NO_USEFUL_STRUCTURAL_SPLIT", result["reason_code"])

    def test_multiple_reduced_structural_children_are_effective(self):
        result = validator.validate_structural_split_result(
            {
                "no_useful_structural_split": False,
                "children": [
                    structural_child("child_001", 20, 20),
                    structural_child("child_002", 300, 20),
                ],
                "reason": "Two direct regions.",
            },
            PARENT_SIZE,
        )
        self.assertTrue(result["valid"])
        self.assertEqual("VALID_STRUCTURAL_SPLIT", result["reason_code"])

    def test_duplicate_structural_bbox_is_ineffective(self):
        child_a = structural_child("child_001", 20, 20)
        child_b = structural_child("child_002", 20, 20)
        result = validator.validate_structural_split_result(
            {
                "no_useful_structural_split": False,
                "children": [child_a, child_b],
                "reason": "Duplicate geometry.",
            },
            PARENT_SIZE,
        )
        self.assertEqual("DUPLICATE_STRUCTURAL_CHILD_BBOX", result["reason_code"])

    def test_all_parent_sized_structural_children_are_ineffective(self):
        result = validator.validate_structural_split_result(
            {
                "no_useful_structural_split": False,
                "children": [
                    structural_child("child_001", 0, 0, 1024, 512),
                    structural_child("child_002", 5, 5, 1010, 500),
                ],
                "reason": "No material visual reduction.",
            },
            PARENT_SIZE,
        )
        self.assertEqual("INEFFECTIVE_PARENT_SIZED_CHILDREN", result["reason_code"])

    def test_zero_instances_are_contract_valid_but_ineffective(self):
        result = validator.validate_expand_instances_result(
            {
                "instance_type": "no valid repeated instances",
                "repeat_count": 0,
                "instances": [],
                "reason": "No genuine peer collection.",
            },
            PARENT_SIZE,
        )
        self.assertFalse(result["valid"])
        self.assertEqual("INSUFFICIENT_REPEATED_INSTANCES", result["reason_code"])

    def test_variable_instance_sizes_are_effective(self):
        instances = [
            instance("instance_001", 20, 30, 140, 130),
            instance("instance_002", 190, 25, 155, 145),
            instance("instance_003", 370, 35, 148, 138),
        ]
        result = validator.validate_expand_instances_result(
            {
                "instance_type": "reward item card",
                "repeat_count": len(instances),
                "instances": instances,
                "reason": "Three peer cards.",
            },
            PARENT_SIZE,
        )
        self.assertTrue(result["valid"])
        self.assertEqual("VALID_REPEATED_INSTANCES", result["reason_code"])

    def test_mass_duplicate_instance_bboxes_are_ineffective(self):
        instances = [
            instance("instance_001", 20, 30),
            instance("instance_002", 20, 30),
            instance("instance_003", 20, 30),
            instance("instance_004", 400, 30),
        ]
        result = validator.validate_expand_instances_result(
            {
                "instance_type": "reward item card",
                "repeat_count": len(instances),
                "instances": instances,
                "reason": "Duplicate geometry.",
            },
            PARENT_SIZE,
        )
        self.assertEqual("MASS_DUPLICATE_INSTANCE_BBOX", result["reason_code"])


if __name__ == "__main__":
    unittest.main()
