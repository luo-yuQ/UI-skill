from __future__ import annotations

import copy
import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import vlm_client  # noqa: E402
from production_visual_adapter import (  # noqa: E402
    ProductionVisualAdapter,
    build_production_runtime_adapters,
)
from recursive_runtime import (  # noqa: E402
    DEFAULT_MAX_CONCURRENCY,
    RecursiveRuntime,
    RootInput,
    RuntimeAdapters,
    RuntimeConfig,
)
from vlm_client import (  # noqa: E402
    ResponsesAPIVLMClient,
    VLMClientConfig,
    encode_image_as_data_url,
)
from runtime_geometry import read_image_size  # noqa: E402


def route_result(role: str = "asset") -> dict[str, Any]:
    return {
        "node_role": role,
        "confidence": 0.99,
        "reason": "Deterministic concurrency fixture.",
    }


def structural_result(*child_ids: str) -> dict[str, Any]:
    children = [
        {
            "id": child_id,
            "label": child_id,
            "bbox": {"x": index * 256, "y": 0, "width": 256, "height": 256},
            "confidence": 0.99,
        }
        for index, child_id in enumerate(child_ids)
    ]
    return {
        "no_useful_structural_split": not children,
        "children": children,
        "reason": "Deterministic direct children.",
    }


def instances_result(count: int) -> dict[str, Any]:
    return {
        "instance_type": "fixture card",
        "repeat_count": count,
        "instances": [
            {
                "id": f"instance_{index + 1:03d}",
                "bbox": {
                    "x": (index % 5) * 200,
                    "y": (index // 5) * 180,
                    "width": 180,
                    "height": 160,
                },
                "partial_instance": False,
                "confidence": 0.99,
            }
            for index in range(count)
        ],
        "reason": "Deterministic repeated instances.",
    }


def semantic_stop(node_id: str) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "node_role": "component_instance",
        "task": "semantic_decompose",
        "bbox_constraint": "completeness",
        "analysis_image_size": {"width": 1024, "height": 1024},
        "decision": "stop_as_asset",
        "asset_taxonomy": "illustration",
        "children": [],
        "reason": "The current node is one complete asset.",
    }


def responses_body(result: dict[str, Any]) -> str:
    return json.dumps(
        {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": json.dumps(result)}
                    ],
                }
            ]
        }
    )


class UnusedAdapter:
    def run(self, analysis_image: Path) -> dict[str, Any]:
        raise AssertionError(f"unexpected adapter call for {analysis_image}")


class MappingRouter:
    def __init__(self, results: dict[str, dict[str, Any]]) -> None:
        self.results = copy.deepcopy(results)
        self.calls: list[str] = []
        self.lock = threading.Lock()

    def route(self, analysis_image: Path) -> dict[str, Any]:
        node_id = Path(analysis_image).parent.name
        with self.lock:
            self.calls.append(node_id)
        return copy.deepcopy(self.results[node_id])


class MappingStrategy:
    def __init__(self, results: dict[str, dict[str, Any]]) -> None:
        self.results = copy.deepcopy(results)

    def run(self, analysis_image: Path) -> dict[str, Any]:
        return copy.deepcopy(self.results[Path(analysis_image).parent.name])


class SemanticStopAdapter:
    def run(self, analysis_image: Path) -> dict[str, Any]:
        node_id = Path(analysis_image).parent.name
        result = semantic_stop(node_id)
        width, height = read_image_size(analysis_image)
        result["analysis_image_size"] = {"width": width, "height": height}
        return result


class TrackingRouter:
    def __init__(self, expected_overlap: int) -> None:
        self.barrier = threading.Barrier(expected_overlap)
        self.lock = threading.Lock()
        self.active_calls = 0
        self.max_observed_active_calls = 0

    def route(self, analysis_image: Path) -> dict[str, Any]:
        del analysis_image
        with self.lock:
            self.active_calls += 1
            self.max_observed_active_calls = max(
                self.max_observed_active_calls, self.active_calls
            )
        try:
            self.barrier.wait(timeout=5)
            return route_result()
        finally:
            with self.lock:
                self.active_calls -= 1


class OrderedStructuralAdapter:
    def __init__(self, completion_order: list[str]) -> None:
        self.barrier = threading.Barrier(len(completion_order))
        self.condition = threading.Condition()
        self.target_order = list(completion_order)
        self.completion_order: list[str] = []

    def run(self, analysis_image: Path) -> dict[str, Any]:
        node_id = Path(analysis_image).parent.name
        self.barrier.wait(timeout=5)
        with self.condition:
            self.condition.wait_for(
                lambda: self.target_order[len(self.completion_order)] == node_id,
                timeout=5,
            )
            if self.target_order[len(self.completion_order)] != node_id:
                raise TimeoutError("unable to force adapter completion order")
            self.completion_order.append(node_id)
            self.condition.notify_all()
        return structural_result("child_001")


class LevelBarrierStructuralAdapter:
    def __init__(self, events: list[str], lock: threading.Lock) -> None:
        self.barrier = threading.Barrier(2)
        self.a_finished = threading.Event()
        self.events = events
        self.lock = lock

    def run(self, analysis_image: Path) -> dict[str, Any]:
        node_id = Path(analysis_image).parent.name
        self.barrier.wait(timeout=5)
        if node_id == "A":
            with self.lock:
                self.events.append("depth0_end:A")
            self.a_finished.set()
        else:
            if not self.a_finished.wait(timeout=5):
                raise TimeoutError("A did not finish")
            with self.lock:
                self.events.append("depth0_end:B")
        return structural_result("child_001")


class ChildLoggingRouter(MappingRouter):
    def __init__(
        self,
        results: dict[str, dict[str, Any]],
        events: list[str],
        lock: threading.Lock,
    ) -> None:
        super().__init__(results)
        self.events = events
        self.event_lock = lock

    def route(self, analysis_image: Path) -> dict[str, Any]:
        node_id = Path(analysis_image).parent.name
        with self.event_lock:
            self.events.append(f"depth1_start:{node_id}")
        return super().route(analysis_image)


class FailingRouter:
    def __init__(self) -> None:
        self.barrier = threading.Barrier(4)

    def route(self, analysis_image: Path) -> dict[str, Any]:
        node_id = Path(analysis_image).parent.name
        self.barrier.wait(timeout=5)
        if node_id == "B":
            raise RuntimeError("B transport failed")
        return route_result()


class FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class RetryConcurrencySession:
    def __init__(self, a_image_url: str = "") -> None:
        self.a_image_url = a_image_url
        self.sleep_started = threading.Event()
        self.other_call_during_retry = threading.Event()
        self.lock = threading.Lock()
        self.call_counts: dict[str, int] = {}

    def post(self, endpoint: str, **kwargs: Any) -> FakeResponse:
        del endpoint
        image_url = kwargs["json"]["input"][0]["content"][1]["image_url"]
        with self.lock:
            attempt = self.call_counts.get(image_url, 0) + 1
            self.call_counts[image_url] = attempt
        if image_url == self.a_image_url and attempt == 1:
            return FakeResponse(502, "temporary provider failure")
        if image_url != self.a_image_url:
            if not self.sleep_started.wait(timeout=5):
                raise TimeoutError("retry wait did not begin")
            self.other_call_during_retry.set()
        return FakeResponse(200, responses_body(route_result()))


class BarrierSemanticClient:
    def __init__(self) -> None:
        self.barrier = threading.Barrier(2)

    def infer_json(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        self.barrier.wait(timeout=5)
        return semantic_stop("wrong-model-node")


class RuntimeConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = tempfile.TemporaryDirectory()
        self.addCleanup(self.context.cleanup)
        self.base = Path(self.context.name)

    def source(self, name: str, color: str = "navy") -> Path:
        path = self.base / f"{name}.png"
        Image.new("RGB", (1000, 900), color).save(path)
        return path

    def create_multi(
        self,
        *,
        name: str,
        node_ids: list[str],
        adapters: RuntimeAdapters,
        max_concurrency: int,
        validation_mode: str = "mechanics",
        colors: list[str] | None = None,
    ) -> RecursiveRuntime:
        colors = colors or ["navy"] * len(node_ids)
        roots = [
            RootInput(node_id, self.source(f"{name}-{node_id}", colors[index]))
            for index, node_id in enumerate(node_ids)
        ]
        return RecursiveRuntime.create_multi(
            run_dir=self.base / name,
            roots=roots,
            adapters=adapters,
            config=RuntimeConfig(
                validation_mode=validation_mode,
                max_concurrency=max_concurrency,
            ),
        )

    @staticmethod
    def set_action(runtime: RecursiveRuntime, node_id: str, role: str, action: str) -> None:
        node = runtime.store.get(node_id)
        node.node_role = role
        node.next_action = action
        node.requires_router = False
        runtime.store.update(node)

    def test_config_default_and_validation(self):
        self.assertEqual(4, DEFAULT_MAX_CONCURRENCY)
        self.assertEqual(4, RuntimeConfig().max_concurrency)
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "max_concurrency"
            ):
                RuntimeConfig(max_concurrency=value)  # type: ignore[arg-type]

    def test_concurrency_limit_and_serial_compatibility(self):
        observed: dict[int, int] = {}
        for max_concurrency in (4, 1):
            router = TrackingRouter(max_concurrency)
            runtime = self.create_multi(
                name=f"limit-{max_concurrency}",
                node_ids=[chr(ord("A") + index) for index in range(8)],
                adapters=RuntimeAdapters(
                    router=router,
                    structural_split=UnusedAdapter(),
                    expand_instances=UnusedAdapter(),
                    semantic_decompose=UnusedAdapter(),
                ),
                max_concurrency=max_concurrency,
            )
            self.assertEqual("complete", runtime.run())
            observed[max_concurrency] = router.max_observed_active_calls
            self.assertEqual(
                [chr(ord("A") + index) for index in range(8)],
                runtime.state.processed_nodes,
            )
        self.assertGreater(observed[4], 1)
        self.assertLessEqual(observed[4], 4)
        self.assertEqual(1, observed[1])

    def test_completion_order_does_not_control_commit_or_child_order(self):
        structural = OrderedStructuralAdapter(["C", "D", "B", "A"])
        runtime = self.create_multi(
            name="commit-order",
            node_ids=["A", "B", "C", "D"],
            adapters=RuntimeAdapters(
                router=MappingRouter(
                    {
                        f"{node_id}.child_001": route_result()
                        for node_id in ("A", "B", "C", "D")
                    }
                ),
                structural_split=structural,
                expand_instances=UnusedAdapter(),
                semantic_decompose=UnusedAdapter(),
            ),
            max_concurrency=4,
        )
        for node_id in ("A", "B", "C", "D"):
            self.set_action(runtime, node_id, "structural_group", "structural_split")

        self.assertEqual("complete", runtime.run())
        self.assertEqual(["C", "D", "B", "A"], structural.completion_order)
        self.assertEqual(
            [
                "A",
                "B",
                "C",
                "D",
                "A.child_001",
                "B.child_001",
                "C.child_001",
                "D.child_001",
            ],
            runtime.state.processed_nodes,
        )
        self.assertEqual(
            [
                "A",
                "B",
                "C",
                "D",
                "A.child_001",
                "B.child_001",
                "C.child_001",
                "D.child_001",
            ],
            [node["node_id"] for node in runtime.store.snapshot()["nodes"]],
        )

    def test_bfs_level_barrier_blocks_child_compute_until_entire_parent_level_finishes(self):
        events: list[str] = []
        lock = threading.Lock()
        runtime = self.create_multi(
            name="level-barrier",
            node_ids=["A", "B"],
            adapters=RuntimeAdapters(
                router=ChildLoggingRouter(
                    {
                        "A.child_001": route_result(),
                        "B.child_001": route_result(),
                    },
                    events,
                    lock,
                ),
                structural_split=LevelBarrierStructuralAdapter(events, lock),
                expand_instances=UnusedAdapter(),
                semantic_decompose=UnusedAdapter(),
            ),
            max_concurrency=4,
        )
        for node_id in ("A", "B"):
            self.set_action(runtime, node_id, "structural_group", "structural_split")

        self.assertEqual("complete", runtime.run())
        first_child = min(
            index for index, value in enumerate(events) if value.startswith("depth1_start")
        )
        self.assertLess(events.index("depth0_end:A"), first_child)
        self.assertLess(events.index("depth0_end:B"), first_child)

    def _single_root_snapshot(self, name: str, max_concurrency: int) -> tuple[Any, Any]:
        runtime = RecursiveRuntime.create(
            run_dir=self.base / name,
            root_node_crop=self.source(f"{name}-source"),
            adapters=RuntimeAdapters(
                router=MappingRouter(
                    {
                        f"root.child_{index:03d}": route_result()
                        for index in range(1, 5)
                    }
                ),
                structural_split=MappingStrategy(
                    {
                        "root": structural_result(
                            "child_001", "child_002", "child_003", "child_004"
                        )
                    }
                ),
                expand_instances=UnusedAdapter(),
                semantic_decompose=UnusedAdapter(),
            ),
            config=RuntimeConfig(max_concurrency=max_concurrency),
        )
        self.set_action(runtime, "root", "structural_group", "structural_split")
        self.assertEqual("complete", runtime.run())
        return runtime.store.snapshot(), runtime.state.to_dict()

    def test_single_root_serial_and_concurrent_results_are_equivalent(self):
        serial = self._single_root_snapshot("single-serial", 1)
        concurrent = self._single_root_snapshot("single-concurrent", 4)
        self.assertEqual(serial, concurrent)

    def _multi_root_snapshot(self, name: str, max_concurrency: int) -> tuple[Any, Any]:
        runtime = self.create_multi(
            name=name,
            node_ids=["A", "B"],
            adapters=RuntimeAdapters(
                router=MappingRouter(
                    {
                        f"{root}.child_{index:03d}": route_result()
                        for root in ("A", "B")
                        for index in (1, 2)
                    }
                ),
                structural_split=MappingStrategy(
                    {
                        root: structural_result("child_001", "child_002")
                        for root in ("A", "B")
                    }
                ),
                expand_instances=UnusedAdapter(),
                semantic_decompose=UnusedAdapter(),
            ),
            max_concurrency=max_concurrency,
        )
        for node_id in ("A", "B"):
            self.set_action(runtime, node_id, "structural_group", "structural_split")
        self.assertEqual("complete", runtime.run())
        return runtime.store.snapshot(), runtime.state.to_dict()

    def test_multi_root_serial_and_concurrent_results_are_equivalent(self):
        serial = self._multi_root_snapshot("multi-serial", 1)
        concurrent = self._multi_root_snapshot("multi-concurrent", 4)
        self.assertEqual(serial, concurrent)

    def test_failure_isolation_commits_other_nodes(self):
        runtime = self.create_multi(
            name="failure-isolation",
            node_ids=["A", "B", "C", "D"],
            adapters=RuntimeAdapters(
                router=FailingRouter(),
                structural_split=UnusedAdapter(),
                expand_instances=UnusedAdapter(),
                semantic_decompose=UnusedAdapter(),
            ),
            max_concurrency=4,
        )
        self.assertEqual("failed", runtime.run())
        self.assertEqual(["A", "C", "D"], runtime.state.processed_nodes)
        self.assertEqual(["B"], runtime.state.failed_nodes)
        self.assertEqual("failed", runtime.store.get("B").status)
        self.assertIn("B transport failed", runtime.store.get("B").error or "")

    def test_retry_wait_does_not_block_other_workers(self):
        node_ids = ["A", "B", "C", "D"]
        colors = ["red", "green", "blue", "yellow"]
        sources = [
            self.source(f"retry-{node_id}", colors[index])
            for index, node_id in enumerate(node_ids)
        ]
        session = RetryConcurrencySession()
        client = ResponsesAPIVLMClient(
            VLMClientConfig("https://provider.example", "secret", "model"),
            session=session,
        )
        runtime = RecursiveRuntime.create_multi(
            run_dir=self.base / "retry-concurrency",
            roots=[
                RootInput(node_id, sources[index])
                for index, node_id in enumerate(node_ids)
            ],
            adapters=build_production_runtime_adapters(
                ProductionVisualAdapter(client)
            ),
            config=RuntimeConfig(validation_mode="real_image", max_concurrency=4),
        )
        analysis_urls = {
            node_id: encode_image_as_data_url(
                runtime._artifact(runtime.store.get(node_id).analysis_image, "analysis_image")
            )
            for node_id in node_ids
        }
        session.a_image_url = analysis_urls["A"]

        def retry_wait(seconds: float) -> None:
            self.assertEqual(5, seconds)
            session.sleep_started.set()
            self.assertTrue(session.other_call_during_retry.wait(timeout=5))

        with patch("vlm_client.time.sleep", side_effect=retry_wait) as sleep:
            self.assertEqual("complete", runtime.run())
        self.assertEqual(1, sleep.call_count)
        self.assertEqual(2, session.call_counts[analysis_urls["A"]])
        for node_id in node_ids[1:]:
            self.assertEqual(1, session.call_counts[analysis_urls[node_id]])
        self.assertEqual(node_ids, runtime.state.processed_nodes)

    def test_production_bound_request_context_is_thread_local(self):
        adapter = ProductionVisualAdapter(BarrierSemanticClient())
        image = self.base / "bound-context.png"
        Image.new("RGB", (1024, 512), "navy").save(image)

        def invoke(node_id: str) -> dict[str, Any]:
            adapter.bind_request(
                request_id=f"req-{node_id}",
                node_id=node_id,
                node_role="component_instance",
                adapter_kind="semantic_decompose",
                analysis_image=f"nodes/{node_id}/analysis-image.png",
            )
            return adapter.semantic_decompose(image)

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_a = executor.submit(invoke, "A")
            future_b = executor.submit(invoke, "B")
            result_a = future_a.result(timeout=5)
            result_b = future_b.result(timeout=5)
        self.assertEqual("A", result_a["node_id"])
        self.assertEqual("B", result_b["node_id"])
        self.assertEqual(2, adapter.consumed_response_count)

    @unittest.skipIf(vlm_client.requests is None, "requests package is not installed")
    def test_default_responses_client_uses_one_session_per_worker_thread(self):
        barrier = threading.Barrier(2)
        sessions: list[Any] = []
        sessions_lock = threading.Lock()

        class WorkerSession:
            def post(self, endpoint: str, **kwargs: Any) -> FakeResponse:
                del endpoint, kwargs
                barrier.wait(timeout=5)
                return FakeResponse(200, responses_body({"ok": True}))

        def session_factory() -> WorkerSession:
            session = WorkerSession()
            with sessions_lock:
                sessions.append(session)
            return session

        with patch.object(vlm_client.requests, "Session", side_effect=session_factory):
            client = ResponsesAPIVLMClient(
                VLMClientConfig("https://provider.example", "secret", "model")
            )
            image = self.source("thread-local-session")
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(
                        client.infer_json,
                        image,
                        "system",
                        "user",
                    )
                    for _ in range(2)
                ]
                self.assertEqual([{"ok": True}, {"ok": True}], [f.result() for f in futures])
        self.assertEqual(2, len(sessions))
        self.assertIsNot(sessions[0], sessions[1])

    def test_repeated_instance_semantic_limit_remains_two(self):
        runtime = RecursiveRuntime.create(
            run_dir=self.base / "repeated-limit",
            root_node_crop=self.source("repeated-source"),
            adapters=RuntimeAdapters(
                router=UnusedAdapter(),
                structural_split=UnusedAdapter(),
                expand_instances=MappingStrategy({"root": instances_result(25)}),
                semantic_decompose=SemanticStopAdapter(),
            ),
            config=RuntimeConfig(max_concurrency=4),
        )
        self.set_action(runtime, "root", "repeated_group", "expand_instances")

        self.assertEqual("complete_with_deferred", runtime.run())
        self.assertEqual(
            [f"root.instance_{index:03d}" for index in range(3, 26)],
            runtime.state.deferred_nodes,
        )
        self.assertEqual(
            ["root", "root.instance_001", "root.instance_002"],
            runtime.state.processed_nodes,
        )


if __name__ == "__main__":
    unittest.main()
