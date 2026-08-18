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
    NODE_STATUSES,
    NodeRecord,
    NodeStore,
    RecursiveRuntime,
    RuntimeAdapters,
    RuntimeConfig,
)
from runtime_geometry import (  # noqa: E402
    analysis_bbox_to_crop_bbox,
    create_child_node_images,
    read_image_size,
)


def route(role: str) -> dict:
    return {
        "node_role": role,
        "confidence": 0.99,
        "reason": "Deterministic fixture classification.",
    }


def structural(children: list[dict] | None = None) -> dict:
    children = children or []
    return {
        "no_useful_structural_split": not children,
        "children": children,
        "reason": "Deterministic fixture direct children.",
    }


def structural_child(
    child_id: str = "child_a", x: int = 0, y: int = 0
) -> dict:
    return {
        "id": child_id,
        "label": child_id,
        "bbox": {"x": x, "y": y, "width": 256, "height": 256},
        "confidence": 0.98,
    }


def instances(count: int) -> dict:
    return {
        "instance_type": "fixture card",
        "repeat_count": count,
        "instances": [
            {
                "id": f"instance_{index + 1:03d}",
                "bbox": {
                    "x": index * 200,
                    "y": 100,
                    "width": 180,
                    "height": 180,
                },
                "partial_instance": False,
                "confidence": 0.99,
            }
            for index in range(count)
        ],
        "reason": "Deterministic peer instances.",
    }


def semantic_decompose(node_id: str, *, height: int = 1024) -> dict:
    return {
        "node_id": node_id,
        "node_role": "component_instance",
        "task": "semantic_decompose",
        "bbox_constraint": "completeness",
        "analysis_image_size": {"width": 1024, "height": height},
        "decision": "decompose",
        "children": [
            {
                "id": "asset_001",
                "label": "fixture icon",
                "taxonomy": "icon",
                "bbox": {"x": 100, "y": 100, "width": 300, "height": 300},
                "partial": False,
                "confidence": 0.98,
            }
        ],
        "reason": "One direct visual asset.",
    }


def semantic_stop(node_id: str, *, height: int = 512) -> dict:
    return {
        "node_id": node_id,
        "node_role": "component_instance",
        "task": "semantic_decompose",
        "bbox_constraint": "completeness",
        "analysis_image_size": {"width": 1024, "height": height},
        "decision": "stop_as_asset",
        "asset_taxonomy": "illustration",
        "children": [],
        "reason": "The current node is already one complete asset.",
    }


class RecursiveRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = tempfile.TemporaryDirectory()
        self.addCleanup(self.context.cleanup)
        self.base = Path(self.context.name)
        self.root_crop = self.base / "source.png"
        Image.new("RGB", (400, 200), "navy").save(self.root_crop)

    def make_runtime(
        self,
        *,
        routes: dict | list | None = None,
        splits: dict | list | None = None,
        expands: dict | list | None = None,
        semantics: dict | list | None = None,
        limit: int | None = 2,
        name: str = "run",
    ) -> tuple[RecursiveRuntime, RuntimeAdapters]:
        adapters = RuntimeAdapters(
            router=FixtureRouterAdapter(routes or {}),
            structural_split=FixtureStructuralSplitAdapter(splits or {}),
            expand_instances=FixtureExpandInstancesAdapter(expands or {}),
            semantic_decompose=FixtureSemanticDecomposeAdapter(semantics or {}),
        )
        runtime = RecursiveRuntime.create(
            run_dir=self.base / name,
            root_node_crop=self.root_crop,
            adapters=adapters,
            config=RuntimeConfig(limit),
        )
        return runtime, adapters

    @staticmethod
    def set_root_role(
        runtime: RecursiveRuntime, role: str, action: str
    ) -> NodeRecord:
        root = runtime.store.get("root")
        root.node_role = role
        root.next_action = action
        root.requires_router = False
        runtime.store.update(root)
        return root

    def test_t01_unknown_root_requires_router_once(self):
        runtime, adapters = self.make_runtime(routes={"root": route("asset")})
        self.assertEqual("complete", runtime.run())
        root = runtime.store.get("root")
        self.assertEqual("asset", root.node_role)
        self.assertEqual("stop", root.next_action)
        self.assertEqual(1, len(adapters.router.calls))

    def test_t02_expand_provenance_shortcut_never_routes(self):
        runtime, adapters = self.make_runtime(
            semantics={"root": semantic_stop("root")}
        )
        root = runtime.store.get("root")
        root.produced_by = "expand_instances"
        root.requires_router = False
        runtime.store.update(root)
        self.assertEqual("complete", runtime.run())
        self.assertEqual([], adapters.router.calls)
        self.assertEqual(1, len(adapters.semantic_decompose.calls))

    def test_t03_structural_split_child_requires_router(self):
        runtime, _ = self.make_runtime(
            splits={"root": structural([structural_child()])}
        )
        self.set_root_role(runtime, "structural_group", "structural_split")
        runtime.process_node("root")
        child = runtime.store.get("root.child_a")
        self.assertTrue(child.requires_router)
        self.assertIsNone(child.node_role)

    def test_t04_semantic_asset_is_terminal_done_and_not_enqueued(self):
        runtime, _ = self.make_runtime(
            semantics={"root": semantic_decompose("root", height=512)}
        )
        self.set_root_role(runtime, "component_instance", "semantic_decompose")
        runtime.process_node("root")
        asset = runtime.store.get("root.asset_001")
        self.assertTrue(asset.terminal)
        self.assertEqual("stop", asset.next_action)
        self.assertEqual("done", asset.status)
        self.assertEqual([], runtime.state.next_level_queue)

    def test_t05_stop_as_asset_changes_parent_without_fake_child(self):
        runtime, _ = self.make_runtime(semantics={"root": semantic_stop("root")})
        self.set_root_role(runtime, "component_instance", "semantic_decompose")
        runtime.process_node("root")
        root = runtime.store.get("root")
        self.assertEqual("asset", root.node_role)
        self.assertTrue(root.terminal)
        self.assertEqual([], runtime.store.children_of("root"))

    def test_t06_coordinate_transform_maps_four_edges(self):
        self.assertEqual(
            {"x": 100, "y": 50, "width": 200, "height": 100},
            analysis_bbox_to_crop_bbox(
                {"x": 256, "y": 128, "width": 512, "height": 256},
                (1024, 512),
                (400, 200),
            ),
        )

    def test_t07_child_crop_matches_transformed_bbox(self):
        parent_analysis = self.base / "analysis.png"
        Image.new("RGB", (1024, 512), "black").save(parent_analysis)
        child_crop = self.base / "child" / "node-crop.png"
        bbox = create_child_node_images(
            parent_node_crop=self.root_crop,
            parent_analysis_image=parent_analysis,
            bbox_in_parent_analysis={
                "x": 256,
                "y": 128,
                "width": 512,
                "height": 256,
            },
            child_node_crop=child_crop,
            child_analysis_image=self.base / "child" / "analysis-image.png",
            child_analysis_metadata=self.base / "child" / "meta.json",
        )
        self.assertEqual((bbox["width"], bbox["height"]), read_image_size(child_crop))

    def test_t08_child_analysis_image_is_1024_wide_and_proportional(self):
        parent_analysis = self.base / "analysis.png"
        Image.new("RGB", (1024, 512), "black").save(parent_analysis)
        child_analysis = self.base / "child" / "analysis-image.png"
        create_child_node_images(
            parent_node_crop=self.root_crop,
            parent_analysis_image=parent_analysis,
            bbox_in_parent_analysis={
                "x": 0,
                "y": 0,
                "width": 512,
                "height": 128,
            },
            child_node_crop=self.base / "child" / "node-crop.png",
            child_analysis_image=child_analysis,
            child_analysis_metadata=self.base / "child" / "meta.json",
        )
        self.assertEqual((1024, 256), read_image_size(child_analysis))

    def test_t09_child_enters_next_level_and_is_not_processed_immediately(self):
        runtime, adapters = self.make_runtime(
            splits={"root": structural([structural_child()])},
            routes={"root.child_a": route("asset")},
        )
        self.set_root_role(runtime, "structural_group", "structural_split")
        runtime.process_node("root")
        self.assertEqual(["root.child_a"], runtime.state.next_level_queue)
        self.assertEqual("pending", runtime.store.get("root.child_a").status)
        self.assertEqual([], adapters.router.calls)

    def test_t10_level_advance_increments_depth(self):
        runtime, _ = self.make_runtime()
        runtime.state.current_level_queue = []
        runtime.state.next_level_queue = ["root"]
        self.assertTrue(runtime.advance_level())
        self.assertEqual(1, runtime.state.current_depth)
        self.assertEqual(["root"], runtime.state.current_level_queue)
        self.assertEqual([], runtime.state.next_level_queue)

    def test_t11_terminal_assets_never_enter_next_queue(self):
        runtime, _ = self.make_runtime(routes={"root": route("asset")})
        runtime.process_node("root")
        self.assertEqual([], runtime.state.next_level_queue)

    def test_t12_repeated_limit_schedules_two_and_defers_three(self):
        runtime, _ = self.make_runtime(expands={"root": instances(5)})
        self.set_root_role(runtime, "repeated_group", "expand_instances")
        runtime.process_node("root")
        self.assertEqual(
            ["root.instance_001", "root.instance_002"],
            runtime.state.next_level_queue,
        )
        self.assertEqual(3, len(runtime.state.deferred_nodes))

    def test_t13_repeated_limit_selection_is_stable(self):
        selected = []
        for index in range(2):
            runtime, _ = self.make_runtime(
                expands={"root": instances(5)}, name=f"stable-{index}"
            )
            self.set_root_role(runtime, "repeated_group", "expand_instances")
            runtime.process_node("root")
            selected.append(list(runtime.state.next_level_queue))
        self.assertEqual(selected[0], selected[1])

    def test_t14_count_at_limit_has_no_deferred_nodes(self):
        runtime, _ = self.make_runtime(expands={"root": instances(2)})
        self.set_root_role(runtime, "repeated_group", "expand_instances")
        runtime.process_node("root")
        self.assertEqual([], runtime.state.deferred_nodes)

    def test_t15_deferred_nodes_remain_in_tree_snapshot(self):
        runtime, _ = self.make_runtime(expands={"root": instances(5)})
        self.set_root_role(runtime, "repeated_group", "expand_instances")
        runtime.process_node("root")
        tree = json.loads((runtime.run_dir / "tree.json").read_text(encoding="utf-8"))
        ids = {node["node_id"] for node in tree["nodes"]}
        self.assertIn("root.instance_005", ids)
        self.assertEqual("root", runtime.store.get("root.instance_005").parent_id)

    def test_t16_deferred_node_can_restore_to_pending(self):
        runtime, _ = self.make_runtime(expands={"root": instances(3)})
        self.set_root_role(runtime, "repeated_group", "expand_instances")
        runtime.process_node("root")
        runtime.state.current_level_queue = []
        runtime.state.next_level_queue = []
        restored = runtime.restore_deferred("root.instance_003")
        self.assertEqual("pending", restored.status)
        self.assertEqual(["root.instance_003"], runtime.state.current_level_queue)

    def test_t17_idle_active_run_with_deferred_is_complete_with_deferred(self):
        runtime, _ = self.make_runtime(
            expands={"root": instances(3)},
            semantics={
                "root.instance_001": semantic_stop("root.instance_001", height=1024),
                "root.instance_002": semantic_stop("root.instance_002", height=1024),
            },
        )
        self.set_root_role(runtime, "repeated_group", "expand_instances")
        self.assertEqual("complete_with_deferred", runtime.run())
        manifest = json.loads(runtime.manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(manifest["active_execution_complete"])
        self.assertFalse(manifest["fully_decomposed"])

    def test_t18_invalid_adapter_output_marks_node_failed(self):
        runtime, _ = self.make_runtime(routes={"root": {}})
        self.assertEqual("failed", runtime.run())
        root = runtime.store.get("root")
        self.assertEqual("failed", root.status)
        self.assertIn("invalid Router adapter result", root.error)

    def test_t19_done_node_is_not_executed_again(self):
        runtime, adapters = self.make_runtime(routes={"root": route("asset")})
        runtime.process_node("root")
        runtime.process_node("root")
        self.assertEqual(1, len(adapters.router.calls))

    def test_t20_duplicate_node_id_is_rejected_without_overwrite(self):
        store = NodeStore(self.base / "tree.json", self.base / "nodes")
        original = NodeRecord("root", None, 0, None)
        store.add(original)
        with self.assertRaisesRegex(ValueError, "duplicate node_id"):
            store.add(NodeRecord("root", None, 0, None, label="replacement"))
        self.assertIs(original, store.get("root"))

    def test_node_status_contract_contains_all_required_states(self):
        self.assertEqual(
            {
                "pending",
                "running",
                "ready",
                "done",
                "deferred",
                "failed",
                "blocked",
            },
            NODE_STATUSES,
        )


if __name__ == "__main__":
    unittest.main()

