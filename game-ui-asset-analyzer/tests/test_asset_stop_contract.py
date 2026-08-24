from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import resolve_terminal_state as resolver  # noqa: E402
import validate_node_route as router  # noqa: E402


class AssetStopContractTests(unittest.TestCase):
    def test_t01_router_asset_stops(self):
        self.assertEqual(
            {
                "node_role": "asset",
                "terminal": True,
                "next_action": "stop",
                "requires_router": False,
            },
            resolver.resolve_terminal_state(node_role="asset"),
        )

    def test_t02_router_structural_group_continues(self):
        self.assertEqual(
            {
                "node_role": "structural_group",
                "terminal": False,
                "next_action": "structural_split",
                "requires_router": False,
            },
            resolver.resolve_terminal_state(node_role="structural_group"),
        )

    def test_t03_router_repeated_group_continues(self):
        self.assertEqual(
            {
                "node_role": "repeated_group",
                "terminal": False,
                "next_action": "expand_instances",
                "requires_router": False,
            },
            resolver.resolve_terminal_state(node_role="repeated_group"),
        )

    def test_t04_router_component_instance_continues(self):
        self.assertEqual(
            {
                "node_role": "component_instance",
                "terminal": False,
                "next_action": "semantic_decompose",
                "requires_router": False,
            },
            resolver.resolve_terminal_state(node_role="component_instance"),
        )

    def test_t05_semantic_illustration_child_stops_without_router(self):
        self.assertEqual(
            {
                "node_role": "asset",
                "terminal": True,
                "next_action": "stop",
                "requires_router": False,
            },
            resolver.resolve_terminal_state(
                produced_by="semantic_decompose",
                taxonomy="illustration",
            ),
        )

    def test_t06_semantic_text_child_stops_without_router(self):
        self.assertEqual(
            {
                "node_role": "asset",
                "terminal": True,
                "next_action": "stop",
                "requires_router": False,
            },
            resolver.resolve_terminal_state(
                produced_by="semantic_decompose",
                taxonomy="text",
            ),
        )

    def test_t07_invalid_semantic_taxonomy_fails(self):
        with self.assertRaisesRegex(ValueError, "invalid frozen taxonomy"):
            resolver.resolve_terminal_state(
                produced_by="semantic_decompose",
                taxonomy="invalid_taxonomy",
            )

    def test_t08_expand_instances_child_shortcuts_to_semantic_decompose(self):
        self.assertEqual(
            {
                "node_role": "component_instance",
                "terminal": False,
                "next_action": "semantic_decompose",
                "requires_router": False,
            },
            resolver.resolve_terminal_state(produced_by="expand_instances"),
        )

    def test_t09_structural_split_child_requires_router(self):
        self.assertEqual(
            {"terminal": False, "requires_router": True},
            resolver.resolve_terminal_state(produced_by="structural_split"),
        )

    def test_t10_conflicting_role_and_semantic_provenance_fails(self):
        with self.assertRaisesRegex(ValueError, "conflicting inputs"):
            resolver.resolve_terminal_state(
                node_role="structural_group",
                produced_by="semantic_decompose",
                taxonomy="illustration",
            )

    def test_reuses_router_mapping_and_semantic_schema_taxonomy(self):
        semantic_schema = json.loads(
            resolver.SEMANTIC_SCHEMA_PATH.read_text(encoding="utf-8")
        )
        self.assertEqual(
            frozenset(semantic_schema["$defs"]["taxonomy"]["enum"]),
            resolver.load_frozen_taxonomy(),
        )
        self.assertEqual(
            frozenset(router.ROLE_ACTION_MAP.values()) - {"stop"},
            resolver.PRODUCER_ACTIONS,
        )

    def test_result_schema_and_mapping_validation(self):
        schema = resolver.load_result_schema()
        Draft202012Validator.check_schema(schema)
        valid = resolver.resolve_terminal_state(node_role="asset")
        self.assertEqual([], resolver.validate_result(valid))

        invalid = dict(valid)
        invalid["next_action"] = "semantic_decompose"
        errors = resolver.validate_result(invalid)
        self.assertTrue(any("does not match frozen route" in error for error in errors))

    def test_cli_outputs_deterministic_json_and_never_needs_an_image(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "resolve_terminal_state.py"),
                "--produced-by",
                "semantic_decompose",
                "--taxonomy",
                "icon",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            {
                "node_role": "asset",
                "terminal": True,
                "next_action": "stop",
                "requires_router": False,
            },
            json.loads(result.stdout),
        )


if __name__ == "__main__":
    unittest.main()
