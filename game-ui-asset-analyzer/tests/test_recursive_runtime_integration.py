from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

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
from test_recursive_runtime import (  # noqa: E402
    instances,
    route,
    semantic_decompose,
    structural,
    structural_child,
)


def two_asset_semantic(node_id: str, *, height: int = 1024) -> dict:
    document = semantic_decompose(node_id, height=height)
    document["children"].append(
        {
            "id": "asset_002",
            "label": "fixture text",
            "taxonomy": "text",
            "bbox": {"x": 500, "y": 500, "width": 300, "height": 200},
            "partial": False,
            "confidence": 0.97,
        }
    )
    return document


class RecursiveRuntimeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = tempfile.TemporaryDirectory()
        self.addCleanup(self.context.cleanup)
        self.base = Path(self.context.name)
        self.source = self.base / "source.png"
        Image.new("RGB", (400, 200), "navy").save(self.source)

    def create_runtime(
        self,
        name: str,
        *,
        routes: dict,
        splits: dict | None = None,
        expands: dict | None = None,
        semantics: dict | None = None,
    ) -> tuple[RecursiveRuntime, RuntimeAdapters]:
        adapters = RuntimeAdapters(
            router=FixtureRouterAdapter(routes),
            structural_split=FixtureStructuralSplitAdapter(splits or {}),
            expand_instances=FixtureExpandInstancesAdapter(expands or {}),
            semantic_decompose=FixtureSemanticDecomposeAdapter(semantics or {}),
        )
        runtime = RecursiveRuntime.create(
            run_dir=self.base / name,
            root_node_crop=self.source,
            adapters=adapters,
            config=RuntimeConfig(repeated_instance_semantic_limit=2),
        )
        return runtime, adapters

    def test_case_a_repeated_group_builds_assets_and_preserves_deferred_branches(self):
        runtime, adapters = self.create_runtime(
            "case-a",
            routes={"root": route("repeated_group")},
            expands={"root": instances(5)},
            semantics={
                "root.instance_001": two_asset_semantic("root.instance_001"),
                "root.instance_002": two_asset_semantic("root.instance_002"),
            },
        )

        self.assertEqual("complete_with_deferred", runtime.run())
        self.assertEqual(1, len(adapters.router.calls))
        self.assertEqual(2, len(adapters.semantic_decompose.calls))
        self.assertEqual(5, len(runtime.store.children_of("root")))
        for instance_id in ("root.instance_001", "root.instance_002"):
            children = runtime.store.children_of(instance_id)
            self.assertEqual(["icon", "text"], [child.taxonomy for child in children])
            self.assertTrue(all(child.terminal for child in children))
            self.assertTrue(all(child.status == "done" for child in children))
            self.assertTrue(all(child.node_crop is None for child in children))
            self.assertTrue(all(child.analysis_image is None for child in children))
            for child in children:
                asset_dir = runtime.store.node_directory(child.node_id)
                self.assertFalse((asset_dir / "node-crop.png").exists())
                self.assertFalse((asset_dir / "analysis-image.png").exists())
        for suffix in ("003", "004", "005"):
            deferred = runtime.store.get(f"root.instance_{suffix}")
            self.assertEqual("deferred", deferred.status)
            self.assertEqual(
                "repeated_instance_semantic_limit", deferred.deferred_reason
            )
            self.assertEqual("semantic_decompose", deferred.next_action)
        tree = json.loads((runtime.run_dir / "tree.json").read_text(encoding="utf-8"))
        self.assertEqual(10, len(tree["nodes"]))

    def test_case_b_structural_children_reroute_to_asset_and_component(self):
        runtime, adapters = self.create_runtime(
            "case-b",
            routes={
                "root": route("structural_group"),
                "root.child_a": route("asset"),
                "root.child_b": route("component_instance"),
            },
            splits={
                "root": structural(
                    [
                        structural_child("child_a", 100, 100),
                        structural_child("child_b", 500, 100),
                    ]
                )
            },
            semantics={
                "root.child_b": semantic_decompose("root.child_b", height=1024)
            },
        )

        self.assertEqual("complete", runtime.run())
        self.assertEqual(3, len(adapters.router.calls))
        child_a = runtime.store.get("root.child_a")
        child_b = runtime.store.get("root.child_b")
        self.assertEqual(("asset", "stop", "done"), (
            child_a.node_role,
            child_a.next_action,
            child_a.status,
        ))
        self.assertEqual("component_instance", child_b.node_role)
        assets = runtime.store.children_of("root.child_b")
        self.assertEqual(1, len(assets))
        self.assertTrue(assets[0].terminal)
        self.assertEqual("stop", assets[0].next_action)
        self.assertEqual(1, runtime.state.current_depth)
        self.assertEqual(2, assets[0].depth)


if __name__ == "__main__":
    unittest.main()
