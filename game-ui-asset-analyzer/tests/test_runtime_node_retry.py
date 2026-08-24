from __future__ import annotations

import copy
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Callable

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from production_visual_adapter import StrategySchemaValidationError  # noqa: E402
from recursive_runtime import (  # noqa: E402
    RecursiveRuntime,
    RootInput,
    RuntimeAdapters,
    RuntimeConfig,
)
from runtime_geometry import read_image_size  # noqa: E402
from vlm_client import VLMResponseParseError, VLMTransportError  # noqa: E402


ResultFactory = Callable[[Path], dict[str, Any]]


class UnexpectedAdapter:
    def route(self, analysis_image: Path) -> dict[str, Any]:
        raise AssertionError(f"unexpected router call for {analysis_image}")

    def run(self, analysis_image: Path) -> dict[str, Any]:
        raise AssertionError(f"unexpected strategy call for {analysis_image}")


class AssetRouter:
    def route(self, analysis_image: Path) -> dict[str, Any]:
        del analysis_image
        return {
            "node_role": "asset",
            "confidence": 0.99,
            "reason": "Retry transaction fixture leaf.",
        }


class SequencedAdapter:
    def __init__(self, *steps: BaseException | ResultFactory) -> None:
        self.steps = list(steps)
        self.calls: list[str] = []

    def run(self, analysis_image: Path) -> dict[str, Any]:
        node_id = Path(analysis_image).parent.name
        self.calls.append(node_id)
        if not self.steps:
            raise AssertionError("sequenced adapter was called too many times")
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return copy.deepcopy(step(analysis_image))


def semantic_result(
    analysis_image: Path,
    *,
    child_id: str = "icon_001",
    bbox: dict[str, int] | None = None,
) -> dict[str, Any]:
    node_id = Path(analysis_image).parent.name
    width, height = read_image_size(analysis_image)
    return {
        "node_id": node_id,
        "node_role": "component_instance",
        "task": "semantic_decompose",
        "bbox_constraint": "completeness",
        "analysis_image_size": {"width": width, "height": height},
        "decision": "decompose",
        "children": [
            {
                "id": child_id,
                "label": child_id,
                "taxonomy": "icon",
                "bbox": bbox or {"x": 20, "y": 20, "width": 100, "height": 100},
                "partial": False,
                "confidence": 0.99,
            }
        ],
        "reason": "One direct visual child.",
    }


def semantic_stop(analysis_image: Path) -> dict[str, Any]:
    node_id = Path(analysis_image).parent.name
    width, height = read_image_size(analysis_image)
    return {
        "node_id": node_id,
        "node_role": "component_instance",
        "task": "semantic_decompose",
        "bbox_constraint": "completeness",
        "analysis_image_size": {"width": width, "height": height},
        "decision": "stop_as_asset",
        "asset_taxonomy": "illustration",
        "children": [],
        "reason": "Atomic retry fixture asset.",
    }


def structural_result(*children: dict[str, Any]) -> ResultFactory:
    def build(_analysis_image: Path) -> dict[str, Any]:
        return {
            "no_useful_structural_split": False,
            "children": list(children),
            "reason": "Retry transaction fixture children.",
        }

    return build


def structural_child(
    child_id: str, *, y: int = 0, height: int = 100
) -> dict[str, Any]:
    return {
        "id": child_id,
        "label": child_id,
        "bbox": {"x": 0, "y": y, "width": 100, "height": height},
        "confidence": 0.99,
    }


class ConcurrentRetrySemanticAdapter:
    def __init__(self, node_ids: list[str], retry_node_id: str) -> None:
        self.first_attempt_barrier = threading.Barrier(len(node_ids))
        self.retry_node_id = retry_node_id
        self.lock = threading.Lock()
        self.call_counts: dict[str, int] = {}
        self.active_counts: dict[str, int] = {}
        self.max_active_counts: dict[str, int] = {}
        self.events: list[str] = []

    def run(self, analysis_image: Path) -> dict[str, Any]:
        node_id = Path(analysis_image).parent.name
        with self.lock:
            attempt = self.call_counts.get(node_id, 0) + 1
            self.call_counts[node_id] = attempt
            active = self.active_counts.get(node_id, 0) + 1
            self.active_counts[node_id] = active
            self.max_active_counts[node_id] = max(
                active, self.max_active_counts.get(node_id, 0)
            )
            self.events.append(f"start:{node_id}:{attempt}")
        try:
            if attempt == 1:
                self.first_attempt_barrier.wait(timeout=5)
            if node_id == self.retry_node_id and attempt == 1:
                raise VLMTransportError(
                    "Provider request failed: HTTP 502; attempts=3/3",
                    retryable=True,
                    status_code=502,
                )
            return semantic_stop(analysis_image)
        finally:
            with self.lock:
                self.events.append(f"end:{node_id}:{attempt}")
                self.active_counts[node_id] -= 1


class RuntimeNodeRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = tempfile.TemporaryDirectory()
        self.addCleanup(self.context.cleanup)
        self.base = Path(self.context.name)
        self.source = self.base / "source.png"
        Image.new("RGB", (400, 200), "navy").save(self.source)

    def create_runtime(
        self,
        *,
        name: str,
        semantic: Any,
        structural: Any | None = None,
        router: Any | None = None,
        max_node_retries: int = 2,
        max_concurrency: int = 1,
    ) -> RecursiveRuntime:
        runtime = RecursiveRuntime.create(
            run_dir=self.base / name,
            root_node_crop=self.source,
            adapters=RuntimeAdapters(
                router=router or UnexpectedAdapter(),
                structural_split=structural or UnexpectedAdapter(),
                expand_instances=UnexpectedAdapter(),
                semantic_decompose=semantic,
            ),
            config=RuntimeConfig(
                max_node_retries=max_node_retries,
                max_concurrency=max_concurrency,
            ),
        )
        node = runtime.store.get("root")
        node.node_role = "component_instance"
        node.next_action = "semantic_decompose"
        node.requires_router = False
        runtime.store.update(node)
        return runtime

    def test_config_default_is_two_retries_and_rejects_invalid_values(self) -> None:
        self.assertEqual(2, RuntimeConfig().max_node_retries)
        for value in (-1, True, 1.5, None):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "max_node_retries"
            ):
                RuntimeConfig(max_node_retries=value)  # type: ignore[arg-type]

    def test_bbox_validation_error_requeues_then_commits_children_once(self) -> None:
        bbox_error = StrategySchemaValidationError(
            "semantic_decompose",
            [
                "$.children[0].bbox: bottom edge 379 exceeds "
                "Analysis Image height 357"
            ],
        )
        adapter = SequencedAdapter(bbox_error, semantic_result)
        runtime = self.create_runtime(name="bbox-retry", semantic=adapter)

        self.assertEqual("complete", runtime.run())
        node = runtime.store.get("root")
        self.assertEqual(["root"], runtime.state.processed_nodes)
        self.assertEqual([], runtime.state.failed_nodes)
        self.assertEqual(2, len(adapter.calls))
        self.assertEqual(2, node.attempt_count)
        self.assertEqual(1, node.retry_count)
        self.assertEqual("model_output_transient", node.last_error_category)
        self.assertIn("bottom edge 379", node.last_error or "")
        self.assertEqual(
            ["root.icon_001"],
            [child.node_id for child in runtime.store.children_of("root")],
        )
        loaded = RecursiveRuntime.load(
            run_dir=runtime.run_dir,
            adapters=runtime.adapters,
        )
        loaded_node = loaded.store.get("root")
        self.assertEqual(2, loaded.config.max_node_retries)
        self.assertEqual(2, loaded_node.attempt_count)
        self.assertEqual(1, loaded_node.retry_count)
        self.assertEqual("model_output_transient", loaded_node.last_error_category)

    def test_http_502_requeues_then_processes_node(self) -> None:
        adapter = SequencedAdapter(
            VLMTransportError(
                "Provider request failed: HTTP 502; attempts=3/3",
                retryable=True,
                status_code=502,
            ),
            semantic_stop,
        )
        runtime = self.create_runtime(name="transport-retry", semantic=adapter)

        self.assertEqual("complete", runtime.run())
        node = runtime.store.get("root")
        self.assertEqual(2, len(adapter.calls))
        self.assertEqual(["root"], runtime.state.processed_nodes)
        self.assertEqual([], runtime.state.failed_nodes)
        self.assertEqual(2, node.attempt_count)
        self.assertEqual(1, node.retry_count)
        self.assertEqual("transport_transient", node.last_error_category)

    def test_malformed_model_json_requeues_then_processes_node(self) -> None:
        adapter = SequencedAdapter(
            VLMResponseParseError("model response is not valid JSON"),
            semantic_stop,
        )
        runtime = self.create_runtime(name="json-retry", semantic=adapter)

        self.assertEqual("complete", runtime.run())
        node = runtime.store.get("root")
        self.assertEqual(2, len(adapter.calls))
        self.assertEqual(2, node.attempt_count)
        self.assertEqual(1, node.retry_count)
        self.assertEqual("model_output_transient", node.last_error_category)

    def test_retryable_error_exhaustion_stops_after_initial_plus_two_retries(self) -> None:
        def invalid(analysis_image: Path) -> dict[str, Any]:
            _, height = read_image_size(analysis_image)
            return semantic_result(
                analysis_image,
                bbox={"x": 0, "y": height - 10, "width": 50, "height": 50},
            )

        adapter = SequencedAdapter(invalid, invalid, invalid)
        runtime = self.create_runtime(name="retry-exhausted", semantic=adapter)

        self.assertEqual("failed", runtime.run())
        node = runtime.store.get("root")
        self.assertEqual(3, len(adapter.calls))
        self.assertEqual(3, node.attempt_count)
        self.assertEqual(2, node.retry_count)
        self.assertEqual([], runtime.state.processed_nodes)
        self.assertEqual(["root"], runtime.state.failed_nodes)
        self.assertEqual("failed", node.status)
        self.assertEqual("model_output_transient", node.last_error_category)

    def test_non_retryable_engineering_error_fails_once(self) -> None:
        adapter = SequencedAdapter(RuntimeError("local invariant broke"))
        runtime = self.create_runtime(name="engineering-error", semantic=adapter)

        self.assertEqual("failed", runtime.run())
        node = runtime.store.get("root")
        self.assertEqual(1, len(adapter.calls))
        self.assertEqual(1, node.attempt_count)
        self.assertEqual(0, node.retry_count)
        self.assertEqual("non_retryable", node.last_error_category)
        self.assertEqual(["root"], runtime.state.failed_nodes)

    def test_non_transient_vlm_transport_error_fails_once(self) -> None:
        adapter = SequencedAdapter(VLMTransportError("HTTP 401 unauthorized"))
        runtime = self.create_runtime(name="transport-non-retryable", semantic=adapter)

        self.assertEqual("failed", runtime.run())
        node = runtime.store.get("root")
        self.assertEqual(1, len(adapter.calls))
        self.assertEqual(1, node.attempt_count)
        self.assertEqual(0, node.retry_count)
        self.assertEqual("non_retryable", node.last_error_category)
        self.assertEqual(["root"], runtime.state.failed_nodes)

    def test_non_bbox_schema_error_is_not_retried(self) -> None:
        adapter = SequencedAdapter(
            StrategySchemaValidationError(
                "semantic_decompose",
                ["$.children[0].taxonomy: 'sprite' is not one of the frozen values"],
            )
        )
        runtime = self.create_runtime(name="schema-non-retryable", semantic=adapter)

        self.assertEqual("failed", runtime.run())
        node = runtime.store.get("root")
        self.assertEqual(1, len(adapter.calls))
        self.assertEqual(1, node.attempt_count)
        self.assertEqual(0, node.retry_count)
        self.assertEqual("non_retryable", node.last_error_category)

    def test_failed_validation_attempt_leaves_no_child_or_crop_residue(self) -> None:
        first = structural_result(
            structural_child("ghost_001"),
            structural_child("invalid_002", y=500, height=100),
        )
        second = structural_result(structural_child("final_001"))
        adapter = SequencedAdapter(first, second)
        runtime = self.create_runtime(
            name="atomic-validation",
            semantic=UnexpectedAdapter(),
            structural=adapter,
            router=AssetRouter(),
        )
        root = runtime.store.get("root")
        root.node_role = "structural_group"
        root.next_action = "structural_split"
        runtime.store.update(root)

        self.assertEqual("complete", runtime.run())
        self.assertEqual(2, len(adapter.calls))
        self.assertEqual(
            ["root.final_001"],
            [child.node_id for child in runtime.store.children_of("root")],
        )
        self.assertFalse(runtime.store.contains("root.ghost_001"))
        self.assertFalse(
            (runtime.store.node_directory("root.ghost_001") / "node-crop.png").exists()
        )
        self.assertTrue(
            (runtime.store.node_directory("root.final_001") / "node-crop.png").is_file()
        )

    def test_concurrent_retry_is_requeued_once_after_sibling_attempts(self) -> None:
        node_ids = ["A", "B", "C"]
        adapter = ConcurrentRetrySemanticAdapter(node_ids, retry_node_id="B")
        runtime = RecursiveRuntime.create_multi(
            run_dir=self.base / "concurrent-retry",
            roots=[RootInput(node_id, self.source) for node_id in node_ids],
            adapters=RuntimeAdapters(
                router=UnexpectedAdapter(),
                structural_split=UnexpectedAdapter(),
                expand_instances=UnexpectedAdapter(),
                semantic_decompose=adapter,
            ),
            config=RuntimeConfig(max_concurrency=4, max_node_retries=2),
        )
        for node_id in node_ids:
            node = runtime.store.get(node_id)
            node.node_role = "component_instance"
            node.next_action = "semantic_decompose"
            node.requires_router = False
            runtime.store.update(node)

        self.assertEqual("complete", runtime.run())
        self.assertEqual({"A": 1, "B": 2, "C": 1}, adapter.call_counts)
        self.assertEqual(1, adapter.max_active_counts["B"])
        retry_start = adapter.events.index("start:B:2")
        self.assertLess(adapter.events.index("end:A:1"), retry_start)
        self.assertLess(adapter.events.index("end:C:1"), retry_start)
        self.assertCountEqual(node_ids, runtime.state.processed_nodes)
        self.assertEqual([], runtime.state.failed_nodes)


if __name__ == "__main__":
    unittest.main()
