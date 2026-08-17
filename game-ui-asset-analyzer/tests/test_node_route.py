import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SCHEMA_PATH = ROOT / "schemas" / "node-route.schema.json"
sys.path.insert(0, str(SCRIPTS))

import validate_node_route as validator


ROLE_ACTIONS = {
    "structural_group": "structural_split",
    "repeated_group": "expand_instances",
    "component_instance": "semantic_decompose",
    "asset": "stop",
}


def make_route(role="structural_group"):
    return {
        "node_role": role,
        "confidence": 0.95,
        "reason": "The next direct-child organization matches this role.",
    }


class NodeRouteTests(unittest.TestCase):
    def test_schema_is_valid_draft_2020_12(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)

    def test_all_frozen_roles_validate_and_map_deterministically(self):
        self.assertEqual(ROLE_ACTIONS, validator.ROLE_ACTION_MAP)
        for role, action in ROLE_ACTIONS.items():
            with self.subTest(role=role):
                self.assertEqual([], validator.validate_document(make_route(role)))
                self.assertEqual(action, validator.resolve_node_action(role))
                self.assertEqual(
                    {"node_role": role, "next_action": action},
                    validator.build_route_result(role),
                )

    def test_invalid_role_is_rejected_and_cannot_resolve_action(self):
        errors = validator.validate_document(make_route("visual_group"))
        self.assertTrue(any("node_role" in error for error in errors))
        self.assertTrue(any("deterministic route unavailable" in error for error in errors))
        with self.assertRaises(ValueError):
            validator.resolve_node_action("visual_group")
        with self.assertRaises(ValueError):
            validator.build_route_result("visual_group")

    def test_confidence_below_zero_is_rejected(self):
        data = make_route()
        data["confidence"] = -0.01
        self.assertNotEqual([], validator.validate_document(data))

    def test_confidence_above_one_is_rejected(self):
        data = make_route()
        data["confidence"] = 1.01
        self.assertNotEqual([], validator.validate_document(data))

    def test_empty_or_whitespace_reason_is_rejected(self):
        for reason in ("", "   "):
            with self.subTest(reason=reason):
                data = make_route()
                data["reason"] = reason
                self.assertNotEqual([], validator.validate_document(data))

    def test_missing_required_fields_are_rejected(self):
        for field in ("node_role", "confidence", "reason"):
            with self.subTest(field=field):
                data = make_route()
                data.pop(field)
                self.assertNotEqual([], validator.validate_document(data))

    def test_vlm_cannot_supply_next_action_or_other_extra_fields(self):
        data = make_route()
        data["next_action"] = "structural_split"
        self.assertNotEqual([], validator.validate_document(data))

    def test_validation_does_not_mutate_model_output(self):
        data = make_route("repeated_group")
        original = copy.deepcopy(data)
        self.assertEqual([], validator.validate_document(data))
        self.assertEqual(original, data)

    def test_cli_validates_and_reports_engineering_action(self):
        with tempfile.TemporaryDirectory() as directory:
            document = Path(directory) / "node-route.json"
            document.write_text(
                json.dumps(make_route("asset"), indent=2) + "\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "validate_node_route.py"), str(document)],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("asset -> stop", result.stdout)


if __name__ == "__main__":
    unittest.main()
