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
    RootInput,
    RuntimeAdapters,
    RuntimeConfig,
)
from test_recursive_runtime import (  # noqa: E402
    route,
    structural,
    structural_child,
)


class RecursiveRuntimeMultiRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = tempfile.TemporaryDirectory()
        self.addCleanup(self.context.cleanup)
        self.base = Path(self.context.name)
        self.source_a = self.base / "source-a.png"
        self.source_b = self.base / "source-b.png"
        Image.new("RGB", (400, 200), "navy").save(self.source_a)
        Image.new("RGB", (320, 240), "teal").save(self.source_b)

    @staticmethod
    def adapters(
        *,
        routes: dict | None = None,
        splits: dict | None = None,
    ) -> RuntimeAdapters:
        return RuntimeAdapters(
            router=FixtureRouterAdapter(routes or {}),
            structural_split=FixtureStructuralSplitAdapter(splits or {}),
            expand_instances=FixtureExpandInstancesAdapter({}),
            semantic_decompose=FixtureSemanticDecomposeAdapter({}),
        )

    def roots(self) -> list[RootInput]:
        return [
            RootInput(root_id="A", root_node_crop=self.source_a),
            RootInput(root_id="B", root_node_crop=self.source_b),
        ]

    def test_create_two_roots_preserves_level_zero_input_order(self):
        runtime = RecursiveRuntime.create_multi(
            run_dir=self.base / "two-roots",
            roots=self.roots(),
            adapters=self.adapters(),
        )

        root_a = runtime.store.get("A")
        root_b = runtime.store.get("B")
        self.assertEqual((0, None), (root_a.depth, root_a.parent_id))
        self.assertEqual((0, None), (root_b.depth, root_b.parent_id))
        self.assertEqual(["A", "B"], runtime.state.current_level_queue)
        self.assertEqual([], runtime.state.next_level_queue)
        self.assertEqual(0, runtime.state.current_depth)

    def test_rejects_empty_duplicate_and_blank_root_ids(self):
        with self.assertRaisesRegex(ValueError, "at least one root"):
            RecursiveRuntime.create_multi(
                run_dir=self.base / "empty",
                roots=[],
                adapters=self.adapters(),
            )

        with self.assertRaisesRegex(ValueError, "duplicate root_id"):
            RecursiveRuntime.create_multi(
                run_dir=self.base / "duplicate",
                roots=[
                    RootInput("A", self.source_a),
                    RootInput("A", self.source_b),
                ],
                adapters=self.adapters(),
            )

        with self.assertRaisesRegex(ValueError, "non-empty"):
            RootInput("", self.source_a)

    def test_run_keeps_one_level_barrier_across_all_roots(self):
        adapters = self.adapters(
            routes={
                "A": route("structural_group"),
                "B": route("structural_group"),
                "A.A1": route("asset"),
                "B.B1": route("asset"),
            },
            splits={
                "A": structural([structural_child("A1")]),
                "B": structural([structural_child("B1")]),
            },
        )
        runtime = RecursiveRuntime.create_multi(
            run_dir=self.base / "barrier",
            roots=self.roots(),
            adapters=adapters,
        )

        self.assertEqual("complete", runtime.run())
        router_order = [Path(path).parent.name for path in adapters.router.calls]
        self.assertEqual(["A", "B", "A.A1", "B.B1"], router_order)
        self.assertEqual(1, runtime.state.current_depth)

    def test_direct_children_have_deterministic_next_level_order(self):
        adapters = self.adapters(
            routes={
                "A": route("structural_group"),
                "B": route("structural_group"),
            },
            splits={
                "A": structural(
                    [structural_child("A1"), structural_child("A2", x=300)]
                ),
                "B": structural(
                    [structural_child("B1"), structural_child("B2", x=300)]
                ),
            },
        )
        runtime = RecursiveRuntime.create_multi(
            run_dir=self.base / "ordering",
            roots=self.roots(),
            adapters=adapters,
        )

        while runtime.state.current_level_queue:
            runtime.process_node(runtime.state.current_level_queue.pop(0))

        self.assertEqual(
            ["A.A1", "A.A2", "B.B1", "B.B2"],
            runtime.state.next_level_queue,
        )

    def test_roots_share_one_tree_state_and_manifest(self):
        runtime = RecursiveRuntime.create_multi(
            run_dir=self.base / "shared",
            roots=self.roots(),
            adapters=self.adapters(routes={"A": route("asset"), "B": route("asset")}),
        )

        tree = json.loads((runtime.run_dir / "tree.json").read_text(encoding="utf-8"))
        state = json.loads(
            (runtime.run_dir / "runtime-state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(["A", "B"], [node["node_id"] for node in tree["nodes"]])
        self.assertEqual(["A", "B"], state["current_level_queue"])
        self.assertEqual("complete", runtime.run())
        manifest = json.loads(runtime.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(2, manifest["root_count"])
        self.assertEqual(["A", "B"], manifest["root_ids"])

    def test_load_preserves_roots_queue_depth_and_can_continue(self):
        run_dir = self.base / "load"
        adapters = self.adapters(routes={"A": route("asset"), "B": route("asset")})
        RecursiveRuntime.create_multi(
            run_dir=run_dir,
            roots=self.roots(),
            adapters=adapters,
        )

        loaded = RecursiveRuntime.load(
            run_dir=run_dir,
            adapters=adapters,
            config=RuntimeConfig(),
        )
        self.assertTrue(loaded.store.contains("A"))
        self.assertTrue(loaded.store.contains("B"))
        self.assertEqual(["A", "B"], loaded.state.current_level_queue)
        self.assertEqual(0, loaded.state.current_depth)
        self.assertEqual("complete", loaded.run())

    def test_legacy_single_root_create_and_default_id_remain_compatible(self):
        runtime = RecursiveRuntime.create(
            run_dir=self.base / "single",
            root_node_crop=self.source_a,
            adapters=self.adapters(routes={"root": route("asset")}),
        )

        root = runtime.store.get("root")
        self.assertEqual((0, None), (root.depth, root.parent_id))
        self.assertEqual(["root"], runtime.state.current_level_queue)
        self.assertEqual("complete", runtime.run())
        manifest = json.loads(runtime.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(1, manifest["root_count"])
        self.assertEqual(["root"], manifest["root_ids"])


if __name__ == "__main__":
    unittest.main()
