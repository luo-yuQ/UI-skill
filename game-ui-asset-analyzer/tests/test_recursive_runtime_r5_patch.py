from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from fake_runtime_adapters import (  # noqa: E402
    FixtureExpandInstancesAdapter,
    FixtureRouterAdapter,
    FixtureSemanticDecomposeAdapter,
    FixtureStructuralSplitAdapter,
)
from recursive_runtime import (  # noqa: E402
    RecursiveRuntime,
    RuntimeAdapters,
    RuntimeConfig,
)
from test_recursive_runtime import instances, route, semantic_stop  # noqa: E402


class ContractTestRouterAdapter:
    """Non-provider test adapter that satisfies the Router interface."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = copy.deepcopy(result)
        self.calls = 0

    def route(self, analysis_image: Path) -> dict[str, Any]:
        self.calls += 1
        return copy.deepcopy(self.result)


class RecursiveRuntimeR5PatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = tempfile.TemporaryDirectory()
        self.addCleanup(self.context.cleanup)
        self.base = Path(self.context.name)
        self.source = self.base / "source.png"
        Image.new("RGB", (400, 200), "navy").save(self.source)

    def make_runtime(
        self,
        *,
        router: Any | None = None,
        expands: dict | None = None,
        semantics: dict | None = None,
        name: str = "run",
    ) -> RecursiveRuntime:
        return RecursiveRuntime.create(
            run_dir=self.base / name,
            root_node_crop=self.source,
            adapters=RuntimeAdapters(
                router=router,
                structural_split=FixtureStructuralSplitAdapter({}),
                expand_instances=FixtureExpandInstancesAdapter(expands or {}),
                semantic_decompose=FixtureSemanticDecomposeAdapter(semantics or {}),
            ),
            config=RuntimeConfig(repeated_instance_semantic_limit=2),
        )

    def test_t01_fake_adapter_executes_without_a_production_provider(self):
        adapter = FixtureRouterAdapter({"root": route("asset")})
        runtime = self.make_runtime(router=adapter)
        self.assertEqual("complete", runtime.run())
        self.assertEqual(1, len(adapter.calls))

    def test_t02_contract_test_adapter_does_not_make_runtime_blocked(self):
        adapter = ContractTestRouterAdapter(route("asset"))
        runtime = self.make_runtime(router=adapter)
        self.assertEqual("complete", runtime.run())
        self.assertEqual(1, adapter.calls)
        self.assertEqual([], runtime.store.snapshot()["children"]["root"])

    def test_t03_current_asset_state_overrides_expand_creation_provenance(self):
        runtime = self.make_runtime(router=FixtureRouterAdapter({}))
        root = runtime.store.get("root")
        root.produced_by = "expand_instances"
        root.node_role = "asset"
        root.terminal = True
        root.next_action = "stop"
        root.requires_router = False
        runtime.store.update(root)

        self.assertEqual("complete", runtime.run())
        root = runtime.store.get("root")
        self.assertEqual(("asset", True, "stop"), (
            root.node_role,
            root.terminal,
            root.next_action,
        ))

    def test_t04_unresolved_expand_child_uses_provenance_shortcut(self):
        runtime = self.make_runtime(router=FixtureRouterAdapter({}))
        root = runtime.store.get("root")
        root.produced_by = "expand_instances"
        root.node_role = None
        root.next_action = None
        root.requires_router = False
        runtime._deterministic_resolve(root)
        self.assertEqual(("component_instance", False, "semantic_decompose"), (
            root.node_role,
            root.terminal,
            root.next_action,
        ))

    def test_t05_unresolved_semantic_child_uses_taxonomy_shortcut(self):
        runtime = self.make_runtime(router=FixtureRouterAdapter({}))
        root = runtime.store.get("root")
        root.produced_by = "semantic_decompose"
        root.taxonomy = "illustration"
        root.node_role = None
        root.next_action = None
        root.requires_router = False
        runtime._deterministic_resolve(root)
        self.assertEqual(("asset", True, "stop"), (
            root.node_role,
            root.terminal,
            root.next_action,
        ))

    def test_t06_complete_semantic_asset_state_is_preserved(self):
        runtime = self.make_runtime(router=FixtureRouterAdapter({}))
        root = runtime.store.get("root")
        root.produced_by = "semantic_decompose"
        root.taxonomy = "illustration"
        root.node_role = "asset"
        root.terminal = True
        root.next_action = "stop"
        root.requires_router = False
        before = (root.node_role, root.terminal, root.next_action)
        runtime._deterministic_resolve(root)
        self.assertEqual(before, (root.node_role, root.terminal, root.next_action))

    def test_t07_conflicting_current_state_is_a_validation_failure(self):
        runtime = self.make_runtime(router=FixtureRouterAdapter({}))
        root = runtime.store.get("root")
        root.produced_by = "expand_instances"
        root.node_role = "asset"
        root.terminal = False
        root.next_action = "structural_split"
        root.requires_router = False
        runtime.store.update(root)

        self.assertEqual("failed", runtime.run())
        self.assertIn("current node state contract conflict", root.error)

    def test_t08_semantic_warning_keeps_complete_with_deferred_result(self):
        runtime = self.make_runtime(
            router=FixtureRouterAdapter({"root": route("repeated_group")}),
            expands={"root": instances(3)},
            semantics={
                "root.instance_001": semantic_stop("root.instance_001", height=1024),
                "root.instance_002": semantic_stop("root.instance_002", height=1024),
            },
        )
        runtime.add_semantic_warning(
            node_id="root",
            source="manual_review",
            warning_type="possible_under_detection",
            message="expand_instances may have missed repeated instances",
        )
        self.assertEqual("complete_with_deferred", runtime.run())
        manifest = json.loads(runtime.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("complete_with_deferred", manifest["result"])
        self.assertEqual(1, len(manifest["semantic_warnings"]))

    def test_t09_semantic_warning_does_not_modify_node_record(self):
        adapter = FixtureRouterAdapter({"root": route("asset")})
        runtime = self.make_runtime(router=adapter)
        before = copy.deepcopy(runtime.store.get("root").to_dict())
        queue_before = list(runtime.state.current_level_queue)
        runtime.add_semantic_warning(
            node_id="root",
            source="manual_review",
            warning_type="questionable_router_role",
            message="Router role needs human review",
        )
        self.assertEqual(before, runtime.store.get("root").to_dict())
        self.assertEqual(queue_before, runtime.state.current_level_queue)
        self.assertEqual([], adapter.calls)

    def test_t10_runtime_failures_and_semantic_warnings_are_separate(self):
        runtime = self.make_runtime(router=FixtureRouterAdapter({"root": {}}))
        runtime.add_semantic_warning(
            node_id="root",
            source="manual_review",
            warning_type="visual_disagreement",
            message="Review disagrees with the visual classification",
        )
        self.assertEqual("failed", runtime.run())
        manifest = json.loads(runtime.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(1, len(manifest["runtime_failures"]))
        self.assertEqual(1, len(manifest["semantic_warnings"]))
        self.assertNotEqual(
            manifest["runtime_failures"][0], manifest["semantic_warnings"][0]
        )

    def test_missing_required_adapter_is_node_failure_not_global_blocked(self):
        runtime = self.make_runtime(router=None)
        self.assertEqual("failed", runtime.run())
        root = runtime.store.get("root")
        self.assertEqual("failed", root.status)
        self.assertIn("adapter_unavailable: router", root.error)


if __name__ == "__main__":
    unittest.main()
