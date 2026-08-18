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

import validate_expand_instances  # noqa: E402
import validate_node_route  # noqa: E402
import validate_semantic_decomposition  # noqa: E402
from interactive_file_adapter import InteractiveFileAdapter  # noqa: E402
from production_visual_adapter import (  # noqa: E402
    CONTRACTS,
    ProductionVisualAdapter,
    StrategySchemaValidationError,
    build_production_runtime_adapters,
)
from recursive_runtime import RecursiveRuntime, RuntimeConfig  # noqa: E402
from vlm_client import (  # noqa: E402
    VLMResponseParseError,
    VLMTransportError,
    parse_json_object,
)


def route_result(role: str = "asset") -> dict[str, Any]:
    return {"node_role": role, "confidence": 0.9, "reason": "visible evidence"}


def structural_result() -> dict[str, Any]:
    return {
        "no_useful_structural_split": True,
        "children": [],
        "reason": "no direct structural child",
    }


def expand_result() -> dict[str, Any]:
    return {
        "instance_type": "slot",
        "repeat_count": 0,
        "instances": [],
        "reason": "no complete repeated instance",
    }


def semantic_result() -> dict[str, Any]:
    return {
        "node_id": "current",
        "node_role": "component_instance",
        "task": "semantic_decompose",
        "bbox_constraint": "completeness",
        "analysis_image_size": {"width": 1024, "height": 512},
        "decision": "stop_as_asset",
        "asset_taxonomy": "icon",
        "children": [],
        "reason": "one coherent asset",
    }


class MockVLMClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def infer_json(
        self,
        image_path: Path,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "image_path": image_path,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "response_schema": copy.deepcopy(response_schema),
                "client_id": id(self),
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return copy.deepcopy(response)


class ProductionVisualAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = tempfile.TemporaryDirectory()
        self.addCleanup(self.context.cleanup)
        self.base = Path(self.context.name)
        self.image = self.base / "analysis-image.png"
        Image.new("RGB", (1024, 512), "navy").save(self.image)

    def adapter(self, *responses: Any) -> tuple[ProductionVisualAdapter, MockVLMClient]:
        client = MockVLMClient(list(responses))
        return ProductionVisualAdapter(client), client

    def test_t01_one_instance_exposes_all_four_visual_entrypoints(self):
        adapter, _ = self.adapter()
        for name in (
            "route",
            "structural_split",
            "expand_instances",
            "semantic_decompose",
        ):
            self.assertTrue(callable(getattr(adapter, name)))

    def test_t02_route_loads_frozen_node_router_contract(self):
        adapter, client = self.adapter(route_result())
        adapter.route(self.image)
        self.assertIn("Stage2-A Node Router v0.1", client.calls[0]["user_prompt"])
        self.assertNotIn("Validation evidence", client.calls[0]["user_prompt"])
        self.assertNotIn("Known gap", client.calls[0]["user_prompt"])
        self.assertEqual(
            json.loads(CONTRACTS["router"].schema_path.read_text(encoding="utf-8")),
            client.calls[0]["response_schema"],
        )

    def test_t03_structural_split_loads_frozen_contract(self):
        adapter, client = self.adapter(structural_result())
        adapter.structural_split(self.image)
        self.assertIn("`structural_split` v0.1", client.calls[0]["user_prompt"])

    def test_t04_expand_instances_loads_frozen_contract(self):
        adapter, client = self.adapter(expand_result())
        adapter.expand_instances(self.image)
        self.assertIn("`expand_instances` v0.1", client.calls[0]["user_prompt"])

    def test_t05_semantic_decompose_loads_frozen_contract(self):
        adapter, client = self.adapter(semantic_result())
        adapter.semantic_decompose(self.image)
        self.assertIn("`semantic_decompose` v0.1", client.calls[0]["user_prompt"])

    def test_t06_all_four_methods_share_one_vlm_client(self):
        adapter, client = self.adapter(
            route_result(), structural_result(), expand_result(), semantic_result()
        )
        adapter.route(self.image)
        adapter.structural_split(self.image)
        adapter.expand_instances(self.image)
        adapter.semantic_decompose(self.image)
        self.assertEqual({id(client)}, {call["client_id"] for call in client.calls})

    def test_t07_client_receives_exact_analysis_image_path(self):
        adapter, client = self.adapter(route_result())
        adapter.route(self.image)
        self.assertEqual(client.calls[0]["image_path"], self.image)

    def test_t08_filename_does_not_select_or_hardcode_result(self):
        second = self.base / "an-entirely-different-node-id.png"
        Image.new("RGB", (1024, 512), "navy").save(second)
        expected = route_result("repeated_group")
        adapter, client = self.adapter(expected, expected)
        first_result = adapter.route(self.image)
        second_result = adapter.route(second)
        self.assertEqual(first_result, second_result)
        self.assertEqual(2, len(client.calls))

    def test_t09_router_result_passes_frozen_validator(self):
        adapter, _ = self.adapter(route_result())
        self.assertEqual([], validate_node_route.validate_document(adapter.route(self.image)))

    def test_t10_expand_result_passes_frozen_validator(self):
        adapter, _ = self.adapter(expand_result())
        result = adapter.expand_instances(self.image)
        self.assertEqual(
            [], validate_expand_instances.validate_document(result, self.image)
        )

    def test_t11_semantic_result_passes_frozen_validator(self):
        adapter, _ = self.adapter(semantic_result())
        result = adapter.semantic_decompose(self.image)
        self.assertEqual(
            [], validate_semantic_decomposition.validate_document(result, self.image)
        )

    def test_t12_invalid_json_is_response_parse_error(self):
        with self.assertRaisesRegex(VLMResponseParseError, "vlm_response_parse_error"):
            parse_json_object("not json")

    def test_t13_valid_json_with_wrong_schema_is_strategy_validation_error(self):
        adapter, _ = self.adapter({"node_role": "asset"})
        with self.assertRaisesRegex(
            StrategySchemaValidationError, "strategy_schema_validation_error"
        ):
            adapter.route(self.image)

    def test_t14_transport_timeout_is_transport_error(self):
        adapter, _ = self.adapter(TimeoutError("secret-bearing provider detail"))
        with self.assertRaisesRegex(VLMTransportError, "vlm_transport_error") as caught:
            adapter.route(self.image)
        self.assertNotIn("secret-bearing", str(caught.exception))

    def test_t15_adapter_has_no_node_tree_or_queue_mutation_surface(self):
        adapter, _ = self.adapter()
        self.assertEqual(
            {"vlm_client", "consumed_response_count"}, set(vars(adapter))
        )
        for name in ("node", "tree", "queue", "children", "deferred"):
            self.assertFalse(hasattr(adapter, name))

    def test_t16_router_result_contains_no_next_action_mapping(self):
        adapter, _ = self.adapter(route_result("repeated_group"))
        self.assertNotIn("next_action", adapter.route(self.image))

    def test_t17_real_image_accepts_production_visual(self):
        adapter, _ = self.adapter(route_result())
        runtime = RecursiveRuntime.create(
            run_dir=self.base / "real-image",
            root_node_crop=self.image,
            adapters=build_production_runtime_adapters(adapter),
            config=RuntimeConfig(validation_mode="real_image"),
        )
        self.assertEqual("complete", runtime.run())

    def test_t18_construction_does_not_claim_visual_inference(self):
        adapter, _ = self.adapter(route_result())
        runtime = RecursiveRuntime.create(
            run_dir=self.base / "not-consumed",
            root_node_crop=self.image,
            adapters=build_production_runtime_adapters(adapter),
            config=RuntimeConfig(validation_mode="real_image"),
        )
        self.assertFalse(runtime.state.real_visual_inference_used)

    def test_t19_valid_consumed_response_sets_visual_inference_flag(self):
        adapter, _ = self.adapter(route_result())
        runtime = RecursiveRuntime.create(
            run_dir=self.base / "consumed",
            root_node_crop=self.image,
            adapters=build_production_runtime_adapters(adapter),
            config=RuntimeConfig(validation_mode="real_image"),
        )
        runtime.run()
        manifest = json.loads(runtime.manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(manifest["real_visual_inference_used"])
        self.assertEqual(
            {"production_visual"}, set(manifest["adapter_types"].values())
        )

    def test_t20_interactive_file_adapter_is_retained(self):
        adapter = InteractiveFileAdapter(self.base / "interactive", "router")
        self.assertEqual("interactive_visual", adapter.adapter_type)
        self.assertTrue(callable(adapter.route))


if __name__ == "__main__":
    unittest.main()
