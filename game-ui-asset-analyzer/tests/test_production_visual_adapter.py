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

    def test_semantic_prompt_uses_visual_component_composition_boundary(self):
        adapter, client = self.adapter(semantic_result())
        adapter.semantic_decompose(self.image)
        prompt = client.calls[0]["user_prompt"]

        for required_text in (
            "Functional completeness is irrelevant",
            "visual UI component composition",
            "multiple visually distinguishable foundational UI components",
            "even when they jointly form one complete functional control or one "
            "semantically coherent asset",
            "`panel + icon + text`",
            "A visually distinguishable label or value placed on a panel/base",
            "component-level `text` child even when",
            "Pixel extraction difficulty belongs to the later extraction stage",
            "Map a badge treatment or decorative overlay to `decoration`",
            "progress track or fill to `progress_bar`",
        ):
            self.assertIn(required_text, prompt)

        for invalid_stop_reason in (
            "It is already a complete button.",
            "The elements form one functional asset.",
            "The illustration is part of the same button.",
            "The composition is semantically unified.",
        ):
            self.assertIn(invalid_stop_reason, prompt)

    def test_semantic_prompt_includes_green_base_and_potion_few_shot(self):
        adapter, client = self.adapter(semantic_result())
        adapter.semantic_decompose(self.image)
        prompt = client.calls[0]["user_prompt"]
        example_start = prompt.index(
            "A detailed sprouting potion bottle graphic is centered on top of a "
            "green rounded panel."
        )
        example_end = prompt.find("\n### ", example_start)
        example = prompt[example_start : example_end if example_end >= 0 else None]
        normalized = example.lower()

        self.assertIn("incorrect:", normalized)
        self.assertIn("stop_as_asset", normalized)
        self.assertIn("complete button", normalized)
        self.assertIn("correct decomposition:", normalized)
        self.assertIn("decompose", normalized)
        self.assertIn("green rounded base", normalized)
        self.assertIn("panel", normalized)
        self.assertIn("potion/bottle graphic", normalized)
        self.assertIn("icon", normalized)
        self.assertIn("highly rendered", normalized)
        self.assertIn("do not classify it as `illustration`", normalized)

    def test_semantic_prompt_uses_role_first_icon_illustration_boundary(self):
        adapter, client = self.adapter(semantic_result())
        adapter.semantic_decompose(self.image)
        prompt = client.calls[0]["user_prompt"]

        for icon_case in (
            "localized visual component that represents a discrete object, item, "
            "action, status, resource, ability, category, or symbolic concept",
            "potion bottle",
            "sword or weapon graphic",
            "detailed glowing coin graphic with particles is still an `icon`",
            "character portrait used as an avatar",
            "complex localized fire, lightning, or magic skill graphic is still "
            "an `icon`",
            "inventory item",
        ):
            self.assertIn(icon_case, prompt)

        for illustration_case in (
            "larger artwork-like visual region whose primary role is decorative, "
            "narrative, scenic, promotional, or content presentation",
            "Character-plus-environment promotional artwork",
            "large fantasy scene on a card",
            "multi-object decorative artwork",
            "character, building, sky, and environment",
        ):
            self.assertIn(illustration_case, prompt)

        self.assertIn("UI component role", prompt)
        self.assertIn("Do not ask whether it is simple or highly rendered", prompt)
        self.assertIn("Bbox size is only weak supporting evidence", prompt)
        self.assertIn("Do not use a fixed bbox-area percentage threshold", prompt)

    def test_semantic_prompt_reserves_stop_for_atomic_components(self):
        adapter, client = self.adapter(semantic_result())
        adapter.semantic_decompose(self.image)
        lines = [
            line.strip().lower()
            for line in client.calls[0]["user_prompt"].splitlines()
        ]

        for taxonomy in ("icon", "panel", "illustration"):
            with self.subTest(taxonomy=taxonomy):
                self.assertTrue(
                    any(
                        taxonomy in line
                        and "stop_as_asset" in line
                        and any(
                            boundary in line
                            for boundary in ("atomic", "single", "standalone")
                        )
                        for line in lines
                    ),
                    f"production prompt lacks an atomic {taxonomy} stop example",
                )

    def test_semantic_prompt_keeps_bbox_overlap_out_of_the_decision(self):
        adapter, client = self.adapter(semantic_result())
        adapter.semantic_decompose(self.image)
        prompt = client.calls[0]["user_prompt"]

        self.assertIn("`bbox overlap is allowed`", prompt)
        self.assertIn("spatial overlap does not change the decision", prompt)
        self.assertIn(
            "change the decision to `stop_as_asset` to avoid overlap",
            prompt,
        )
        for later_stage_concept in (
            "Foreground occlusion",
            "masks",
            "segmentation",
            "inpainting",
            "extraction",
            "missing-pixel repair",
            "belong to later stages",
        ):
            self.assertIn(later_stage_concept, prompt)

    def test_semantic_prompt_exposes_strict_structured_output_contract(self):
        adapter, client = self.adapter(semantic_result())
        adapter.semantic_decompose(self.image)
        prompt = client.calls[0]["user_prompt"]
        for required_text in (
            "`bbox` MUST be a JSON object",
            "`x`, `y`, `width`, and `height` MUST be JSON integer fields",
            "Never return `bbox` as an array",
            "Every child `id` must be a unique, non-empty string",
            "Every child `label` must be a non-empty string",
            "`partial` must be a JSON boolean",
            "`confidence` must be a JSON number from 0 through 1",
        ):
            self.assertIn(required_text, prompt)

        examples: dict[str, dict[str, Any]] = {}
        for decision in ("decompose", "stop_as_asset"):
            heading = f"#### `{decision}` JSON shape"
            section = prompt.split(heading, 1)[1]
            code = section.split("```json", 1)[1].split("```", 1)[0]
            examples[decision] = json.loads(code)

        decompose = examples["decompose"]
        self.assertEqual("decompose", decompose["decision"])
        self.assertGreaterEqual(len(decompose["children"]), 2)
        self.assertTrue(
            {"panel", "icon"}.issubset(
                {child["taxonomy"] for child in decompose["children"]}
            )
        )
        self.assertIsInstance(decompose["children"][0]["bbox"], dict)
        self.assertNotIn("asset_taxonomy", decompose)
        self.assertEqual(
            [], validate_semantic_decomposition.validate_document(decompose, self.image)
        )

        stop = examples["stop_as_asset"]
        self.assertEqual("stop_as_asset", stop["decision"])
        self.assertEqual([], stop["children"])
        self.assertIn("asset_taxonomy", stop)
        self.assertEqual(
            [], validate_semantic_decomposition.validate_document(stop, self.image)
        )

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
            {"vlm_client", "consumed_response_count", "_request_context"},
            set(vars(adapter)),
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

    def test_semantic_bound_caller_metadata_overrides_empty_and_wrong_model_values(self):
        for returned_node_id in ("", "wrong"):
            with self.subTest(returned_node_id=returned_node_id):
                response = semantic_result()
                response.update(
                    {
                        "node_id": returned_node_id,
                        "node_role": "wrong",
                        "task": "wrong",
                        "bbox_constraint": "wrong",
                        "analysis_image_size": {"width": 1, "height": 1},
                    }
                )
                adapter, _ = self.adapter(response)
                adapter.bind_request(
                    request_id="req_000001",
                    node_id="test.node_001",
                    node_role="component_instance",
                    adapter_kind="semantic_decompose",
                    analysis_image="nodes/test.node_001/analysis-image.png",
                )
                result = adapter.semantic_decompose(self.image)
                self.assertEqual("test.node_001", result["node_id"])
                self.assertEqual("component_instance", result["node_role"])
                self.assertEqual("semantic_decompose", result["task"])
                self.assertEqual("completeness", result["bbox_constraint"])
                self.assertEqual(
                    {"width": 1024, "height": 512},
                    result["analysis_image_size"],
                )

    def test_semantic_explicit_caller_node_id_overrides_model_value(self):
        response = semantic_result()
        response["node_id"] = "current_node"
        adapter, _ = self.adapter(response)

        result = adapter.semantic_decompose(
            self.image, node_id="test_component_001"
        )

        self.assertEqual("test_component_001", result["node_id"])

    def test_semantic_child_ids_are_derived_from_taxonomy(self):
        response = semantic_result()
        response.update(
            {
                "decision": "decompose",
                "children": [
                    {
                        "id": "base_001",
                        "label": "green rounded base",
                        "taxonomy": "panel",
                        "bbox": {"x": 0, "y": 0, "width": 1024, "height": 512},
                        "partial": False,
                        "confidence": 0.97,
                    },
                    {
                        "id": "potion_art",
                        "label": "detailed sprouting potion bottle icon",
                        "taxonomy": "icon",
                        "bbox": {"x": 420, "y": 116, "width": 184, "height": 280},
                        "partial": False,
                        "confidence": 0.96,
                    },
                ],
                "reason": "The panel and localized potion icon are separate components.",
            }
        )
        response.pop("asset_taxonomy")
        adapter, _ = self.adapter(response)

        result = adapter.semantic_decompose(
            self.image, node_id="test_component_001"
        )

        self.assertEqual(
            ["panel_001", "icon_001"],
            [child["id"] for child in result["children"]],
        )

    def test_runtime_bind_hook_supplies_semantic_node_metadata(self):
        response = semantic_result()
        response["node_id"] = "wrong"
        response["node_role"] = "component"
        adapter, _ = self.adapter(route_result("component_instance"), response)
        runtime = RecursiveRuntime.create(
            run_dir=self.base / "bound-runtime",
            root_node_crop=self.image,
            root_id="test.node_001",
            adapters=build_production_runtime_adapters(adapter),
            config=RuntimeConfig(validation_mode="real_image"),
        )
        self.assertEqual("complete", runtime.run())
        result_path = runtime.store.node_directory("test.node_001") / "strategy-result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual("test.node_001", result["node_id"])
        self.assertEqual("component_instance", result["node_role"])

    def test_semantic_bbox_array_remains_invalid_without_repair(self):
        response = semantic_result()
        response.update(
            {
                "decision": "decompose",
                "children": [
                    {
                        "id": "child_001",
                        "label": "direct visual asset",
                        "taxonomy": "icon",
                        "bbox": [1, 2, 3, 4],
                        "partial": False,
                        "confidence": 0.9,
                    }
                ],
            }
        )
        response.pop("asset_taxonomy")
        adapter, _ = self.adapter(response)
        with self.assertRaisesRegex(
            StrategySchemaValidationError, "is not of type 'object'"
        ):
            adapter.semantic_decompose(self.image)

    def test_semantic_valid_bbox_object_still_passes(self):
        response = semantic_result()
        response.update(
            {
                "decision": "decompose",
                "children": [
                    {
                        "id": "child_001",
                        "label": "direct visual asset",
                        "taxonomy": "icon",
                        "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
                        "partial": False,
                        "confidence": 0.9,
                    }
                ],
            }
        )
        response.pop("asset_taxonomy")
        adapter, _ = self.adapter(response)
        result = adapter.semantic_decompose(self.image)
        self.assertEqual(
            {"x": 1, "y": 2, "width": 3, "height": 4},
            result["children"][0]["bbox"],
        )

    def test_analysis_size_t01_canonicalizes_one_pixel_height_mismatch(self):
        image = self.base / "analysis-1039-a.png"
        Image.new("RGB", (1024, 1039), "navy").save(image)
        response = semantic_result()
        response["analysis_image_size"] = {"width": 1024, "height": 1040}
        adapter, _ = self.adapter(response)
        result = adapter.semantic_decompose(image)
        self.assertEqual(
            {"width": 1024, "height": 1039}, result["analysis_image_size"]
        )

    def test_analysis_size_t02_canonicalizes_larger_height_mismatch(self):
        image = self.base / "analysis-1039-b.png"
        Image.new("RGB", (1024, 1039), "navy").save(image)
        response = semantic_result()
        response["analysis_image_size"] = {"width": 1024, "height": 1031}
        adapter, _ = self.adapter(response)
        result = adapter.semantic_decompose(image)
        self.assertEqual(
            {"width": 1024, "height": 1039}, result["analysis_image_size"]
        )

    def test_analysis_size_t03_real_bbox_bounds_remain_strict(self):
        image = self.base / "analysis-1039-overflow.png"
        Image.new("RGB", (1024, 1039), "navy").save(image)
        response = semantic_result()
        response.update(
            {
                "analysis_image_size": {"width": 1024, "height": 1040},
                "decision": "decompose",
                "children": [
                    {
                        "id": "overflow",
                        "label": "overflow",
                        "taxonomy": "icon",
                        "bbox": {"x": 1000, "y": 100, "width": 41, "height": 25},
                        "partial": False,
                        "confidence": 0.9,
                    }
                ],
            }
        )
        response.pop("asset_taxonomy")
        adapter, _ = self.adapter(response)
        with self.assertRaisesRegex(
            StrategySchemaValidationError, "right edge 1041 exceeds"
        ):
            adapter.semantic_decompose(image)

    def test_analysis_size_t04_preserves_semantics_and_bbox(self):
        image = self.base / "analysis-1039-preserve.png"
        Image.new("RGB", (1024, 1039), "navy").save(image)
        response = semantic_result()
        response.update(
            {
                "analysis_image_size": {"width": 1024, "height": 1040},
                "decision": "decompose",
                "children": [
                    {
                        "id": "icon_001",
                        "label": "preserved icon",
                        "taxonomy": "icon",
                        "bbox": {"x": 11, "y": 13, "width": 17, "height": 19},
                        "partial": False,
                        "confidence": 0.87,
                    }
                ],
                "reason": "preserve every semantic field",
            }
        )
        response.pop("asset_taxonomy")
        expected_semantics = copy.deepcopy(response)
        expected_semantics.pop("analysis_image_size")
        adapter, _ = self.adapter(response)
        result = adapter.semantic_decompose(image)
        actual_semantics = copy.deepcopy(result)
        actual_semantics.pop("analysis_image_size")
        self.assertEqual(expected_semantics, actual_semantics)
        self.assertEqual(
            {"width": 1024, "height": 1039}, result["analysis_image_size"]
        )


if __name__ == "__main__":
    unittest.main()
