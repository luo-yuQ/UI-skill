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
from interactive_file_adapter import InteractiveFileAdapter  # noqa: E402
from production_visual_adapter import ProductionVisualAdapter  # noqa: E402
from recursive_runtime import (  # noqa: E402
    RecursiveRuntime,
    RootInput,
    RuntimeAdapters,
    RuntimeConfig,
)


def route(role: str) -> dict:
    return {
        "node_role": role,
        "confidence": 0.99,
        "reason": "Deterministic fixture route.",
    }


def structural(children: list[dict] | None = None) -> dict:
    children = list(children or [])
    return {
        "no_useful_structural_split": not children,
        "children": children,
        "reason": "Deterministic structural fixture.",
    }


def structural_child(child_id: str, x: int, y: int) -> dict:
    return {
        "id": child_id,
        "label": child_id,
        "bbox": {"x": x, "y": y, "width": 260, "height": 180},
        "confidence": 0.98,
    }


def instances(count: int) -> dict:
    values = []
    for index in range(count):
        column = index % 4
        row = index // 4
        values.append(
            {
                "id": f"instance_{index + 1:03d}",
                "bbox": {
                    "x": 20 + column * 240,
                    "y": 20 + row * 220,
                    "width": 190 + (index % 2) * 5,
                    "height": 180 + (index % 3) * 4,
                },
                "partial_instance": False,
                "confidence": 0.98,
            }
        )
    return {
        "instance_type": (
            "reward item card" if count else "no valid repeated instances"
        ),
        "repeat_count": count,
        "instances": values,
        "reason": "Deterministic repeated-instance fixture.",
    }


def interactive_adapters(run_dir: Path) -> RuntimeAdapters:
    return RuntimeAdapters(
        router=InteractiveFileAdapter(run_dir, "router"),
        structural_split=InteractiveFileAdapter(run_dir, "structural_split"),
        expand_instances=InteractiveFileAdapter(run_dir, "expand_instances"),
        semantic_decompose=InteractiveFileAdapter(run_dir, "semantic_decompose"),
    )


def write_interactive_response(
    run_dir: Path, pending: dict[str, str], result: dict
) -> None:
    response = {
        "schema_version": "0.1",
        "request_id": pending["request_id"],
        "adapter_kind": pending["adapter_kind"],
        "result": result,
    }
    path = run_dir / pending["response_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(response), encoding="utf-8")


class CapturingVLMClient:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[dict] = []

    def infer_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class RecursiveRuntimeRouteFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = tempfile.TemporaryDirectory()
        self.addCleanup(self.context.cleanup)
        self.base = Path(self.context.name)
        self.root_crop = self.base / "source.png"
        Image.new("RGB", (400, 200), "navy").save(self.root_crop)

    def make_runtime(
        self,
        *,
        route_result: dict,
        split_result: dict | None = None,
        expand_result: dict | None = None,
        name: str,
    ) -> tuple[RecursiveRuntime, RuntimeAdapters]:
        adapters = RuntimeAdapters(
            router=FixtureRouterAdapter({"root": route_result}),
            structural_split=FixtureStructuralSplitAdapter(
                {} if split_result is None else {"root": split_result}
            ),
            expand_instances=FixtureExpandInstancesAdapter(
                {} if expand_result is None else {"root": expand_result}
            ),
            semantic_decompose=FixtureSemanticDecomposeAdapter({}),
        )
        runtime = RecursiveRuntime.create(
            run_dir=self.base / name,
            root_node_crop=self.root_crop,
            adapters=adapters,
            config=RuntimeConfig(repeated_instance_semantic_limit=None),
        )
        return runtime, adapters

    def test_lucky_wheel_structural_misroute_falls_back_once_to_instances(self):
        runtime, adapters = self.make_runtime(
            route_result=route("structural_group"),
            split_result=structural(),
            expand_result=instances(7),
            name="lucky-wheel",
        )

        self.assertEqual("done", runtime.process_node("root"))

        root = runtime.store.get("root")
        self.assertEqual("structural_group", root.router_role)
        self.assertEqual("repeated_group", root.effective_role)
        self.assertEqual("repeated_group", root.node_role)
        self.assertTrue(root.route_override)
        self.assertEqual("resolved", root.route_resolution)
        self.assertEqual(2, len(root.route_attempts or []))
        self.assertTrue(root.route_attempts[0]["contract_valid"])
        self.assertFalse(root.route_attempts[0]["effectiveness_valid"])
        self.assertTrue(root.route_attempts[1]["effectiveness_valid"])
        self.assertEqual(7, len(runtime.store.children_of("root")))
        self.assertEqual(1, len(adapters.structural_split.calls))
        self.assertEqual(1, len(adapters.expand_instances.calls))
        persisted = json.loads(
            (
                runtime.store.node_directory("root") / "node.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual("structural_group", persisted["router_role"])
        self.assertEqual("repeated_group", persisted["effective_role"])

    def test_correct_repeated_route_does_not_trigger_fallback(self):
        runtime, adapters = self.make_runtime(
            route_result=route("repeated_group"),
            expand_result=instances(3),
            name="correct-repeated",
        )

        self.assertEqual("done", runtime.process_node("root"))

        root = runtime.store.get("root")
        self.assertEqual("repeated_group", root.router_role)
        self.assertEqual("repeated_group", root.effective_role)
        self.assertFalse(root.route_override)
        self.assertEqual(1, len(root.route_attempts or []))
        self.assertEqual([], adapters.structural_split.calls)
        self.assertEqual(1, len(adapters.expand_instances.calls))

    def test_genuine_structural_group_does_not_probe_instances(self):
        runtime, adapters = self.make_runtime(
            route_result=route("structural_group"),
            split_result=structural(
                [
                    structural_child("child_001", 20, 20),
                    structural_child("child_002", 340, 20),
                ]
            ),
            name="correct-structural",
        )

        self.assertEqual("done", runtime.process_node("root"))

        root = runtime.store.get("root")
        self.assertEqual("structural_group", root.effective_role)
        self.assertFalse(root.route_override)
        self.assertEqual(2, len(runtime.store.children_of("root")))
        self.assertEqual([], adapters.expand_instances.calls)

    def test_both_routes_ineffective_stop_unresolved_without_third_action(self):
        runtime, adapters = self.make_runtime(
            route_result=route("structural_group"),
            split_result=structural(),
            expand_result=instances(0),
            name="both-invalid",
        )

        self.assertEqual("failed", runtime.process_node("root"))

        root = runtime.store.get("root")
        self.assertEqual("structural_group", root.router_role)
        self.assertIsNone(root.effective_role)
        self.assertIsNone(root.node_role)
        self.assertIsNone(root.next_action)
        self.assertEqual("unresolved", root.route_resolution)
        self.assertEqual(2, len(root.route_attempts or []))
        self.assertTrue(all(a["contract_valid"] for a in root.route_attempts))
        self.assertTrue(
            all(not a["effectiveness_valid"] for a in root.route_attempts)
        )
        self.assertEqual([], runtime.store.children_of("root"))
        self.assertEqual(1, len(adapters.structural_split.calls))
        self.assertEqual(1, len(adapters.expand_instances.calls))
        self.assertEqual([], adapters.semantic_decompose.calls)
        persisted = json.loads(
            (
                runtime.store.node_directory("root") / "node.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual("structural_group", persisted["router_role"])
        self.assertNotIn("effective_role", persisted)
        self.assertNotIn("node_role", persisted)

    def test_repeated_misroute_falls_back_once_to_structural_children(self):
        runtime, adapters = self.make_runtime(
            route_result=route("repeated_group"),
            expand_result=instances(1),
            split_result=structural(
                [
                    structural_child("child_001", 20, 20),
                    structural_child("child_002", 340, 20),
                ]
            ),
            name="repeated-misroute",
        )

        self.assertEqual("done", runtime.process_node("root"))

        root = runtime.store.get("root")
        self.assertEqual("repeated_group", root.router_role)
        self.assertEqual("structural_group", root.effective_role)
        self.assertTrue(root.route_override)
        self.assertEqual(2, len(runtime.store.children_of("root")))
        self.assertEqual(1, len(adapters.expand_instances.calls))
        self.assertEqual(1, len(adapters.structural_split.calls))

    def test_schema_invalid_initial_result_uses_existing_failure_not_fallback(self):
        runtime, adapters = self.make_runtime(
            route_result=route("structural_group"),
            split_result={"children": []},
            expand_result=instances(2),
            name="schema-invalid",
        )

        self.assertEqual("failed", runtime.process_node("root"))

        root = runtime.store.get("root")
        self.assertEqual("structural_group", root.router_role)
        self.assertEqual([], root.route_attempts)
        self.assertIn("invalid structural_split adapter result", root.error)
        self.assertEqual([], adapters.expand_instances.calls)

    def test_concurrent_workers_use_the_same_route_resolution_policy(self):
        crop_b = self.base / "source-b.png"
        Image.new("RGB", (400, 200), "green").save(crop_b)
        adapters = RuntimeAdapters(
            router=FixtureRouterAdapter(
                {"A": route("structural_group"), "B": route("repeated_group")}
            ),
            structural_split=FixtureStructuralSplitAdapter({"A": structural()}),
            expand_instances=FixtureExpandInstancesAdapter(
                {"A": instances(2), "B": instances(2)}
            ),
            semantic_decompose=FixtureSemanticDecomposeAdapter({}),
        )
        runtime = RecursiveRuntime.create_multi(
            run_dir=self.base / "concurrent",
            roots=[RootInput("A", self.root_crop), RootInput("B", crop_b)],
            adapters=adapters,
            config=RuntimeConfig(
                repeated_instance_semantic_limit=0, max_concurrency=2
            ),
        )

        self.assertIsNone(runtime._process_current_level_concurrently())

        self.assertEqual("repeated_group", runtime.store.get("A").effective_role)
        self.assertTrue(runtime.store.get("A").route_override)
        self.assertEqual("repeated_group", runtime.store.get("B").effective_role)
        self.assertFalse(runtime.store.get("B").route_override)
        self.assertEqual(2, len(runtime.store.children_of("A")))
        self.assertEqual(2, len(runtime.store.children_of("B")))

    def test_interactive_resume_continues_pending_fallback_without_rerun(self):
        run_dir = self.base / "interactive"
        runtime = RecursiveRuntime.create(
            run_dir=run_dir,
            root_node_crop=self.root_crop,
            adapters=interactive_adapters(run_dir),
            config=RuntimeConfig(repeated_instance_semantic_limit=0),
        )

        self.assertEqual("waiting_for_adapter", runtime.run())
        write_interactive_response(
            run_dir, runtime.state.pending_adapter_request, route("structural_group")
        )
        self.assertEqual("waiting_for_adapter", runtime.run())
        write_interactive_response(
            run_dir, runtime.state.pending_adapter_request, structural()
        )
        self.assertEqual("waiting_for_adapter", runtime.run())

        fallback_pending = runtime.state.pending_adapter_request
        self.assertEqual("expand_instances", fallback_pending["adapter_kind"])
        self.assertEqual("probe", fallback_pending["execution_mode"])
        request = json.loads(
            (run_dir / fallback_pending["request_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual("structural_split", request["previous_action"])
        self.assertEqual("NO_USEFUL_STRUCTURAL_SPLIT", request["previous_reason_code"])
        self.assertEqual(1, len(runtime.store.get("root").route_attempts or []))

        write_interactive_response(run_dir, fallback_pending, instances(2))
        resumed = RecursiveRuntime.load(
            run_dir=run_dir, adapters=interactive_adapters(run_dir)
        )
        self.assertEqual("complete_with_deferred", resumed.run())

        root = resumed.store.get("root")
        self.assertEqual("structural_group", root.router_role)
        self.assertEqual("repeated_group", root.effective_role)
        self.assertEqual(2, len(root.route_attempts or []))
        self.assertEqual(2, len(resumed.store.children_of("root")))
        requests = list((run_dir / "adapter-requests").glob("*.json"))
        self.assertEqual(3, len(requests))

    def test_production_probe_prompt_does_not_assert_fallback_role(self):
        analysis_image = self.base / "analysis.png"
        Image.new("RGB", (1024, 512), "black").save(analysis_image)
        client = CapturingVLMClient(instances(0))
        adapter = ProductionVisualAdapter(client)
        adapter.bind_request(
            request_id="req_000001",
            node_id="root",
            node_role=None,
            adapter_kind="expand_instances",
            analysis_image="nodes/root/analysis-image.png",
            execution_mode="probe",
            previous_action="structural_split",
            previous_reason_code="NO_USEFUL_STRUCTURAL_SPLIT",
        )

        adapter.expand_instances(analysis_image)

        prompt = client.calls[0]["user_prompt"]
        self.assertIn("Execution mode: fallback probe", prompt)
        self.assertIn("not asserted to be a repeated_group", prompt)
        self.assertIn("repeat_count=0", prompt)
        self.assertIn("Previous action: structural_split", prompt)


if __name__ == "__main__":
    unittest.main()
