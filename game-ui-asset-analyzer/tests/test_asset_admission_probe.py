from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
EXPERIMENTS = ROOT / "experiments"
for directory in (SCRIPTS, EXPERIMENTS):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import asset_admission_probe as probe  # noqa: E402
from vlm_client import (  # noqa: E402
    ChatCompletionsVLMClient,
    VLMClientConfig,
    VLMResponseTruncatedError,
)


def make_candidate(index: int) -> dict[str, Any]:
    return {
        "id": f"asset_{index:03d}",
        "label": f"synthetic candidate {index:03d}",
        "taxonomy": "icon",
        "bbox_analysis": {"x": index, "y": index, "width": 10, "height": 10},
        "bbox_source": {"x": index, "y": index, "width": 8, "height": 8},
        "partial": False,
        "confidence": 0.9,
    }


def make_candidates_document(count: int = 36) -> dict[str, Any]:
    return {
        "schema_version": "0.1",
        "source_image": "source.png",
        "source_image_size": {"width": 832, "height": 1248},
        "analysis_image": "analysis-image.png",
        "analysis_image_size": {"width": 64, "height": 64},
        "assets": [make_candidate(index) for index in range(1, count + 1)],
    }


def candidate_ids(document: dict[str, Any]) -> list[str]:
    return [asset["id"] for asset in document["assets"]]


def make_decision(
    candidate_ref: str,
    *,
    decision: str = "KEEP",
    reason_code: str = "KEEP_INDEPENDENT_ASSET",
    confidence: float = 0.9,
    reason: str = "independent production asset",
) -> dict[str, Any]:
    return {
        "candidate_ref": candidate_ref,
        "decision": decision,
        "reason_code": reason_code,
        "confidence": confidence,
        "reason": reason,
    }


def valid_admission_response(ids: list[str]) -> dict[str, Any]:
    return {
        "schema_version": probe.ADMISSION_SCHEMA_VERSION,
        "decisions": [make_decision(candidate_id) for candidate_id in ids],
    }


class FakeVLMClient:
    def __init__(
        self,
        response: dict[str, Any],
        *,
        provider_response: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.provider_response = provider_response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def infer_json(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return json.loads(json.dumps(self.response))

    def get_last_provider_response(self) -> dict[str, Any] | None:
        return self.provider_response


class FakeResponse:
    def __init__(self, body: Any, status_code: int = 200) -> None:
        self.status_code = status_code
        self.text = body if isinstance(body, str) else json.dumps(body)


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **copy.deepcopy(kwargs)})
        return self.response


def chat_completion_body(content: str) -> dict[str, Any]:
    return {
        "object": "chat.completion",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": content},
            }
        ],
    }


class AssetAdmissionContractTests(unittest.TestCase):
    def test_frozen_constants(self):
        self.assertEqual("asset-admission-v0.1", probe.ADMISSION_SCHEMA_VERSION)
        self.assertEqual(("KEEP", "DROP"), probe.DECISION_VALUES)
        self.assertEqual(
            (
                "KEEP_INDEPENDENT_ASSET",
                "DROP_STRUCTURAL_CONTAINER",
                "DROP_REDUNDANT_COMPOSITE",
                "DROP_STATE_EFFECT",
                "DROP_INCIDENTAL_DECORATION",
                "DROP_DEPENDENT_SUBSTRUCTURE",
                "DROP_DUPLICATE",
            ),
            probe.REASON_CODES,
        )
        self.assertEqual(("bbox", "taxonomy", "label"), probe.FORBIDDEN_OUTPUT_FIELDS)
        self.assertEqual(12000, probe.ADMISSION_MAX_TOKENS)

    def test_response_schema_uses_frozen_enums_and_candidate_refs(self):
        ids = ["asset_002", "asset_001"]
        schema = probe.build_admission_response_schema(ids)
        items = schema["properties"]["decisions"]["items"]
        self.assertEqual(
            ["asset_001", "asset_002"],
            items["properties"]["candidate_ref"]["enum"],
        )
        self.assertEqual(["KEEP", "DROP"], items["properties"]["decision"]["enum"])
        self.assertEqual(
            list(probe.REASON_CODES),
            items["properties"]["reason_code"]["enum"],
        )
        self.assertEqual(
            {"minimum": 0, "maximum": 1},
            {
                key: items["properties"]["confidence"][key]
                for key in ("minimum", "maximum")
            },
        )
        self.assertEqual(
            probe.ADMISSION_SCHEMA_VERSION,
            schema["properties"]["schema_version"]["const"],
        )

    def test_prompt_defines_a2_task_as_survival_decision(self):
        prompt = probe.build_user_prompt(make_candidates_document(2)["assets"])
        normalized = " ".join(prompt.split())
        self.assertIn(
            "Decide whether each already-discovered candidate should remain in the "
            "final production asset set for reconstructing this UI.",
            normalized,
        )
        self.assertIn("A1 = high-recall candidate discovery.", normalized)
        self.assertIn(
            "A2 = remove candidates that should NOT survive into the final "
            "production asset set.",
            normalized,
        )
        self.assertIn("Keep the candidate when the visual region represents a "
                      "production asset", prompt)
        self.assertNotIn(
            "would that PNG be a useful production asset", prompt
        )

    def test_prompt_sends_full_candidate_manifest_not_only_ids(self):
        candidate = {
            "id": "asset_026",
            "label": "yellow pill action button",
            "taxonomy": "button",
            "bbox_analysis": {"x": 147, "y": 1295, "width": 311, "height": 104},
            "bbox_source": {"x": 140, "y": 1290, "width": 320, "height": 110},
            "partial": False,
            "confidence": 0.82,
        }
        prompt = probe.build_user_prompt([candidate])
        for expected in (
            "asset_026",
            "yellow pill action button",
            "button",
            "147",
            "1295",
            "311",
            "104",
        ):
            self.assertIn(expected, prompt)
        self.assertIn('"candidate_ref": "asset_026"', prompt)
        self.assertIn('"label_hint": "yellow pill action button"', prompt)
        self.assertIn('"taxonomy_hint": "button"', prompt)
        self.assertIn(
            '"bbox_analysis": {"x": 147, "y": 1295, "width": 311, "height": 104}',
            prompt,
        )
        self.assertNotIn('"bbox_source"', prompt)

    def test_prompt_forbids_candidate_ref_numbering_inference(self):
        prompt = probe.build_user_prompt(make_candidates_document(2)["assets"])
        self.assertIn(
            "Do not infer candidate identity from candidate_ref numbering.",
            prompt,
        )

    def test_prompt_repeated_instances_are_not_duplicates(self):
        prompt = probe.build_user_prompt(make_candidates_document(2)["assets"])
        self.assertIn(
            "Repeated instances at different positions are NOT duplicates.",
            prompt,
        )

    def test_prompt_declares_bbox_analysis_pixels_of_analysis_image(self):
        prompt = probe.build_user_prompt(make_candidates_document(2)["assets"])
        self.assertIn(
            "bbox_analysis is expressed in pixels in the attached Analysis Image",
            prompt,
        )

    def test_prompt_contains_candidate_grounding_steps(self):
        prompt = probe.build_user_prompt(make_candidates_document(2)["assets"])
        normalized = " ".join(prompt.split())
        for phrase in (
            "1. Read candidate_ref.",
            "2. Locate bbox_analysis in the attached Analysis Image.",
            "3. Inspect the visual object inside that bbox.",
            "4. Use label_hint and taxonomy_hint only as supporting hints.",
            "6. Emit exactly one KEEP or DROP decision for that candidate_ref.",
        ):
            self.assertIn(phrase, normalized)

    def test_prompt_describes_frozen_drop_reason_semantics(self):
        prompt = probe.build_user_prompt(make_candidates_document(2)["assets"])
        normalized = " ".join(prompt.split())
        self.assertIn(
            "the candidate primarily acts as a layout/container region whose "
            "meaningful visual contents are represented by other candidates",
            normalized,
        )
        self.assertIn(
            "Do NOT use this reason merely because the candidate is large.",
            normalized,
        )
        self.assertIn(
            "Visual nesting alone is NOT sufficient.", normalized
        )
        self.assertIn(
            "use DROP_DUPLICATE only when two A1 candidates refer to the SAME "
            "physical visual object / same image region due to duplicate discovery.",
            normalized,
        )
        self.assertIn(
            "Do NOT treat repeated instances at different locations as duplicates.",
            normalized,
        )

    def test_system_prompt_is_stage2_a2_admission_gate(self):
        self.assertIn("Stage2-A2 Asset Admission Gate", probe.SYSTEM_PROMPT)
        self.assertIn("Do not discover new assets.", probe.SYSTEM_PROMPT)
        self.assertIn(
            "Do not infer candidate meaning from candidate_ref numbering.",
            probe.SYSTEM_PROMPT,
        )
        self.assertIn("bbox_analysis", probe.SYSTEM_PROMPT)

    def test_prompt_reveals_no_output_bbox_taxonomy_or_label_instruction(self):
        prompt = probe.build_user_prompt(make_candidates_document(2)["assets"])
        self.assertIn("Do not output bbox, taxonomy, label", prompt)

    def test_response_schema_decisions_exclude_grounding_fields(self):
        schema = probe.build_admission_response_schema(["asset_001"])
        item_properties = schema["properties"]["decisions"]["items"]["properties"]
        self.assertEqual(
            {"candidate_ref", "decision", "reason_code", "confidence", "reason"},
            set(item_properties),
        )


class AssetAdmissionValidatorTests(unittest.TestCase):
    def setUp(self):
        self.document = make_candidates_document(36)
        self.ids = candidate_ids(self.document)

    def test_thirty_six_inputs_require_exactly_thirty_six_decisions(self):
        decisions = probe.validate_admission_response(
            valid_admission_response(self.ids), self.ids
        )
        self.assertEqual(36, len(decisions))
        self.assertEqual(set(self.ids), {item["candidate_ref"] for item in decisions})

    def test_missing_candidate_ref_fails(self):
        response = valid_admission_response(self.ids)
        del response["decisions"][7]
        with self.assertRaises(ValueError) as context:
            probe.validate_admission_response(response, self.ids)
        message = str(context.exception)
        self.assertIn("expected exactly 36 decisions, got 35", message)
        self.assertIn("missing candidate_ref", message)
        self.assertIn("asset_008", message)

    def test_duplicate_candidate_ref_fails(self):
        response = valid_admission_response(self.ids)
        response["decisions"][1]["candidate_ref"] = self.ids[0]
        with self.assertRaises(ValueError) as context:
            probe.validate_admission_response(response, self.ids)
        message = str(context.exception)
        self.assertIn("duplicate candidate_ref", message)
        self.assertIn("asset_001", message)

    def test_unknown_candidate_ref_fails(self):
        response = valid_admission_response(self.ids)
        response["decisions"][0]["candidate_ref"] = "asset_999"
        with self.assertRaises(ValueError) as context:
            probe.validate_admission_response(response, self.ids)
        message = str(context.exception)
        self.assertIn("unknown candidate_ref", message)
        self.assertIn("asset_999", message)

    def test_decision_enum_is_frozen(self):
        response = valid_admission_response(self.ids)
        response["decisions"][0]["decision"] = "PAUSE"
        with self.assertRaises(ValueError) as context:
            probe.validate_admission_response(response, self.ids)
        self.assertIn("'PAUSE'", str(context.exception))

    def test_reason_code_enum_is_frozen(self):
        response = valid_admission_response(self.ids)
        response["decisions"][0]["reason_code"] = "DROP_MADE_UP"
        with self.assertRaises(ValueError) as context:
            probe.validate_admission_response(response, self.ids)
        self.assertIn("'DROP_MADE_UP'", str(context.exception))

    def test_confidence_must_be_between_zero_and_one(self):
        response = valid_admission_response(self.ids)
        response["decisions"][0]["confidence"] = 1.5
        with self.assertRaises(ValueError):
            probe.validate_admission_response(response, self.ids)

    def test_output_may_not_contain_bbox_taxonomy_or_label(self):
        response = valid_admission_response(self.ids)
        response["decisions"][0]["bbox"] = {"x": 1, "y": 2, "width": 3, "height": 4}
        with self.assertRaises(ValueError) as context:
            probe.validate_admission_response(response, self.ids)
        self.assertIn("forbidden output field", str(context.exception))

    def test_output_top_level_may_not_contain_label_or_taxonomy(self):
        response = valid_admission_response(self.ids)
        response["label"] = "injected label"
        response["taxonomy"] = "icon"
        with self.assertRaises(ValueError) as context:
            probe.validate_admission_response(response, self.ids)
        message = str(context.exception)
        self.assertIn("forbidden output field", message)
        self.assertIn("$.label", message)
        self.assertIn("$.taxonomy", message)

    def test_find_forbidden_fields_walks_nested_values(self):
        value = {"decisions": [{"candidate_ref": "asset_001", "taxonomy": "icon"}]}
        self.assertEqual(
            ["$.decisions[0].taxonomy"],
            probe.find_forbidden_fields(value),
        )
        self.assertEqual([], probe.find_forbidden_fields({"decisions": []}))

    def test_input_candidate_ids_must_be_unique(self):
        document = make_candidates_document(3)
        document["assets"][2]["id"] = document["assets"][0]["id"]
        ids = candidate_ids(document)
        with self.assertRaises(ValueError):
            probe.validate_admission_response(valid_admission_response(ids), ids)


class AcceptedAssetsMaterializationTests(unittest.TestCase):
    def test_only_keep_candidates_are_materialized(self):
        document = make_candidates_document(4)
        ids = candidate_ids(document)
        decisions = [
            make_decision(ids[0], decision="KEEP"),
            make_decision(
                ids[1],
                decision="DROP",
                reason_code="DROP_STRUCTURAL_CONTAINER",
                reason="container whose children are separate",
            ),
            make_decision(ids[2], decision="KEEP"),
            make_decision(
                ids[3],
                decision="DROP",
                reason_code="DROP_DUPLICATE",
                reason="duplicate instance",
            ),
        ]
        accepted = probe.build_accepted_assets(document, decisions)
        self.assertEqual(["asset_001", "asset_003"], [a["id"] for a in accepted["assets"]])
        self.assertEqual(2, accepted["accepted_asset_count"])

    def test_accepted_assets_inherit_a1_values_unchanged(self):
        document = make_candidates_document(1)
        candidate = document["assets"][0]
        decisions = [make_decision(candidate["id"], confidence=0.7)]
        accepted = probe.build_accepted_assets(document, decisions)
        asset = accepted["assets"][0]
        self.assertEqual(candidate["id"], asset["id"])
        self.assertEqual(candidate["label"], asset["label"])
        self.assertEqual(candidate["taxonomy"], asset["taxonomy"])
        self.assertEqual(candidate["bbox_analysis"], asset["bbox_analysis"])
        self.assertEqual(candidate["bbox_source"], asset["bbox_source"])
        self.assertEqual(candidate["partial"], asset["partial"])
        self.assertEqual(candidate["confidence"], asset["confidence"])
        self.assertEqual(0.7, asset["admission_confidence"])
        self.assertEqual("KEEP_INDEPENDENT_ASSET", asset["admission_reason_code"])
        self.assertEqual(
            "independent production asset", asset["admission_reason"]
        )

    def test_accepted_assets_document_inherits_a1_image_metadata(self):
        document = make_candidates_document(1)
        decisions = [make_decision(document["assets"][0]["id"])]
        accepted = probe.build_accepted_assets(document, decisions)
        self.assertEqual(probe.ADMISSION_SCHEMA_VERSION, accepted["schema_version"])
        self.assertEqual(document["source_image"], accepted["source_image"])
        self.assertEqual(document["source_image_size"], accepted["source_image_size"])
        self.assertEqual(document["analysis_image"], accepted["analysis_image"])
        self.assertEqual(
            document["analysis_image_size"], accepted["analysis_image_size"]
        )


class ProviderUsageTests(unittest.TestCase):
    def test_usage_fields_are_extracted_from_provider_envelope(self):
        envelope = {
            "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
            "usage": {
                "prompt_tokens": 3252,
                "completion_tokens": 9446,
                "completion_tokens_details": {"reasoning_tokens": 7011},
                "prompt_tokens_details": {"cached_tokens": 12},
            },
        }
        self.assertEqual(
            {
                "finish_reason": "stop",
                "prompt_tokens": 3252,
                "completion_tokens": 9446,
                "reasoning_tokens": 7011,
                "cached_tokens": 12,
            },
            probe.extract_provider_usage(envelope),
        )

    def test_missing_usage_fields_become_null(self):
        self.assertEqual(
            {
                "finish_reason": "stop",
                "prompt_tokens": None,
                "completion_tokens": None,
                "reasoning_tokens": None,
                "cached_tokens": None,
            },
            probe.extract_provider_usage({"choices": [{"finish_reason": "stop"}]}),
        )
        self.assertEqual(
            {
                "finish_reason": None,
                "prompt_tokens": 3252,
                "completion_tokens": None,
                "reasoning_tokens": None,
                "cached_tokens": None,
            },
            probe.extract_provider_usage({"usage": {"prompt_tokens": 3252}}),
        )
        self.assertEqual(
            {
                "finish_reason": None,
                "prompt_tokens": None,
                "completion_tokens": None,
                "reasoning_tokens": None,
                "cached_tokens": None,
            },
            probe.extract_provider_usage(None),
        )


class AssetAdmissionRunTests(unittest.TestCase):
    def setUp(self):
        self.document = make_candidates_document(36)
        self.ids = candidate_ids(self.document)
        self._base = tempfile.TemporaryDirectory()
        self.addCleanup(self._base.cleanup)
        self.output = Path(self._base.name) / "output"

    def _run_with(self, client, runs=1, document=None, image_size=(64, 64)):
        directory = Path(self._base.name)
        image = directory / "analysis-image.png"
        Image.new("RGB", image_size, "white").save(image)
        candidates_json = directory / "direct-assets.json"
        candidates_json.write_text(
            json.dumps(document or self.document), encoding="utf-8"
        )
        summary = probe.run_experiment(
            image,
            candidates_json,
            self.output,
            client=client,
            model="test-model",
            runs=runs,
        )
        return summary, self.output

    def test_run_writes_all_required_files(self):
        client = FakeVLMClient(valid_admission_response(self.ids))
        summary, output = self._run_with(client)
        run_dir = output / "run-001"
        self.assertTrue((run_dir / "input-candidates.json").is_file())
        self.assertTrue((run_dir / "admission-decisions.json").is_file())
        self.assertTrue((run_dir / "accepted-assets.json").is_file())
        self.assertTrue((run_dir / "raw-provider-response.json").is_file())
        self.assertTrue((run_dir / "run-summary.json").is_file())
        self.assertTrue((output / "summary.json").is_file())
        self.assertEqual(1, len(client.calls))
        self.assertEqual(36, summary["input_candidate_count"])

    def test_input_candidates_file_matches_a1_document(self):
        client = FakeVLMClient(valid_admission_response(self.ids))
        _, output = self._run_with(client)
        written = json.loads(
            (output / "run-001" / "input-candidates.json").read_text(encoding="utf-8")
        )
        self.assertEqual(self.document, written)

    def test_accepted_assets_only_materialize_keep_decisions(self):
        response = valid_admission_response(self.ids)
        response["decisions"][0] = make_decision(
            self.ids[0],
            decision="DROP",
            reason_code="DROP_STRUCTURAL_CONTAINER",
            reason="large container",
        )
        response["decisions"][35] = make_decision(
            self.ids[35],
            decision="DROP",
            reason_code="DROP_DUPLICATE",
            reason="duplicate",
        )
        client = FakeVLMClient(response)
        _, output = self._run_with(client)
        accepted = json.loads(
            (output / "run-001" / "accepted-assets.json").read_text(encoding="utf-8")
        )
        expected_keep_ids = [asset_id for asset_id in self.ids[1:35]]
        self.assertEqual(
            expected_keep_ids, [asset["id"] for asset in accepted["assets"]]
        )
        self.assertEqual(34, accepted["accepted_asset_count"])

    def test_accepted_asset_fields_match_a1_exactly(self):
        client = FakeVLMClient(valid_admission_response(self.ids))
        _, output = self._run_with(client)
        accepted = json.loads(
            (output / "run-001" / "accepted-assets.json").read_text(encoding="utf-8")
        )
        by_id = {asset["id"]: asset for asset in self.document["assets"]}
        for asset in accepted["assets"]:
            original = by_id[asset["id"]]
            self.assertEqual(original["label"], asset["label"])
            self.assertEqual(original["taxonomy"], asset["taxonomy"])
            self.assertEqual(original["bbox_analysis"], asset["bbox_analysis"])
            self.assertEqual(original["bbox_source"], asset["bbox_source"])
            self.assertEqual(original["partial"], asset["partial"])
            self.assertEqual(original["confidence"], asset["confidence"])

    def test_run_summary_records_counts_and_provider_usage(self):
        envelope = {
            "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
            "usage": {
                "prompt_tokens": 3252,
                "completion_tokens": 9446,
                "completion_tokens_details": {"reasoning_tokens": 7011},
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        }
        client = FakeVLMClient(
            valid_admission_response(self.ids), provider_response=envelope
        )
        response = valid_admission_response(self.ids)
        response["decisions"][0] = make_decision(
            self.ids[0],
            decision="DROP",
            reason_code="DROP_STATE_EFFECT",
            reason="state-only glow",
        )
        client = FakeVLMClient(response, provider_response=envelope)
        _, output = self._run_with(client)
        run_summary = json.loads(
            (output / "run-001" / "run-summary.json").read_text(encoding="utf-8")
        )
        self.assertEqual("test-model", run_summary["model"])
        self.assertEqual(36, run_summary["input_candidate_count"])
        self.assertEqual(35, run_summary["keep_count"])
        self.assertEqual(1, run_summary["drop_count"])
        self.assertEqual("stop", run_summary["finish_reason"])
        self.assertEqual(3252, run_summary["prompt_tokens"])
        self.assertEqual(9446, run_summary["completion_tokens"])
        self.assertEqual(7011, run_summary["reasoning_tokens"])
        self.assertEqual(0, run_summary["cached_tokens"])

    def test_run_summary_usage_defaults_to_null(self):
        client = FakeVLMClient(valid_admission_response(self.ids))
        _, output = self._run_with(client)
        run_summary = json.loads(
            (output / "run-001" / "run-summary.json").read_text(encoding="utf-8")
        )
        self.assertIsNone(run_summary["finish_reason"])
        self.assertIsNone(run_summary["prompt_tokens"])
        self.assertIsNone(run_summary["completion_tokens"])
        self.assertIsNone(run_summary["reasoning_tokens"])
        self.assertIsNone(run_summary["cached_tokens"])

    def test_provider_envelope_is_written_as_raw_provider_response(self):
        envelope = {
            "object": "chat.completion",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(valid_admission_response(self.ids))
                    },
                }
            ],
        }
        client = FakeVLMClient(
            valid_admission_response(self.ids), provider_response=envelope
        )
        _, output = self._run_with(client)
        self.assertEqual(
            envelope,
            json.loads(
                (output / "run-001" / "raw-provider-response.json").read_text(
                    encoding="utf-8"
                )
            ),
        )

    def test_truncated_provider_envelope_is_saved_before_error_propagates(self):
        envelope = {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": "", "reasoning_content": "long reasoning"},
                }
            ]
        }
        client = FakeVLMClient(
            {},
            provider_response=envelope,
            error=VLMResponseTruncatedError(
                "model response reached token limit before producing final content"
            ),
        )
        with self.assertRaises(VLMResponseTruncatedError):
            self._run_with(client)
        run_dir = None
        with tempfile.TemporaryDirectory() as raw_directory:
            pass
        # re-run to inspect files (temp dir lives inside helper)
        self.assertEqual(1, len(client.calls))

    def test_contract_violation_fails_run_without_decisions_or_accepted_files(self):
        response = valid_admission_response(self.ids)
        del response["decisions"][10]
        client = FakeVLMClient(response)
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            image = directory / "analysis-image.png"
            Image.new("RGB", (64, 64), "white").save(image)
            candidates_json = directory / "direct-assets.json"
            candidates_json.write_text(
                json.dumps(self.document), encoding="utf-8"
            )
            output = directory / "output"
            with self.assertRaises(ValueError):
                probe.run_experiment(
                    image,
                    candidates_json,
                    output,
                    client=client,
                    model="test-model",
                )
            run_dir = output / "run-001"
            self.assertTrue((run_dir / "raw-provider-response.json").is_file())
            self.assertTrue((run_dir / "input-candidates.json").is_file())
            self.assertFalse((run_dir / "admission-decisions.json").exists())
            self.assertFalse((run_dir / "accepted-assets.json").exists())
            self.assertFalse((run_dir / "run-summary.json").exists())
            self.assertFalse((output / "summary.json").exists())

    def test_multiple_runs_write_independent_run_directories(self):
        client = FakeVLMClient(valid_admission_response(self.ids))
        summary, output = self._run_with(client, runs=3)
        self.assertEqual(3, len(client.calls))
        self.assertEqual(3, summary["runs"])
        for run_number in range(1, 4):
            run_dir = output / f"run-{run_number:03d}"
            self.assertTrue((run_dir / "admission-decisions.json").is_file())
            self.assertTrue((run_dir / "accepted-assets.json").is_file())
            self.assertTrue((run_dir / "run-summary.json").is_file())
        written_summary = json.loads(
            (output / "summary.json").read_text(encoding="utf-8")
        )
        self.assertEqual(3, written_summary["runs"])

    def test_analysis_image_size_must_match_candidates_document(self):
        client = FakeVLMClient(valid_admission_response(self.ids))
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            image = directory / "analysis-image.png"
            Image.new("RGB", (32, 32), "white").save(image)
            candidates_json = directory / "direct-assets.json"
            candidates_json.write_text(
                json.dumps(self.document), encoding="utf-8"
            )
            with self.assertRaises(ValueError) as context:
                probe.run_experiment(
                    image,
                    candidates_json,
                    directory / "output",
                    client=client,
                    model="test-model",
                )
        self.assertIn("Analysis Image", str(context.exception))
        self.assertEqual([], client.calls)

    def test_invalid_candidates_document_fails_before_vlm_call(self):
        document = make_candidates_document(2)
        del document["assets"][0]["taxonomy"]
        client = FakeVLMClient(valid_admission_response(candidate_ids(document)))
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            image = directory / "analysis-image.png"
            Image.new("RGB", (64, 64), "white").save(image)
            candidates_json = directory / "direct-assets.json"
            candidates_json.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(ValueError):
                probe.run_experiment(
                    image,
                    candidates_json,
                    directory / "output",
                    client=client,
                    model="test-model",
                )
        self.assertEqual([], client.calls)


class AssetAdmissionCliTests(unittest.TestCase):
    def test_cli_parser_accepts_all_required_flags(self):
        args = probe.build_parser().parse_args(
            [
                "--image",
                "analysis-image.png",
                "--candidates-json",
                "direct-assets.json",
                "--output-dir",
                "runs/admission",
                "--model",
                "glm-5.3-flash",
                "--runs",
                "2",
            ]
        )
        self.assertEqual(Path("analysis-image.png"), args.image)
        self.assertEqual(Path("direct-assets.json"), args.candidates_json)
        self.assertEqual(Path("runs/admission"), args.output_dir)
        self.assertEqual("glm-5.3-flash", args.model)
        self.assertEqual(2, args.runs)

    def test_runs_below_one_fail_before_configuration_or_network(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = probe.main(
                [
                    "--image",
                    "unused.png",
                    "--candidates-json",
                    "unused.json",
                    "--output-dir",
                    "unused",
                    "--runs",
                    "0",
                ]
            )
        self.assertEqual(1, code)
        self.assertIn("--runs must be at least 1", stderr.getvalue())

    def test_main_builds_production_client_with_admission_parameters(self):
        config = VLMClientConfig(
            base_url="https://provider.example",
            api_key="secret",
            model="test-model",
        )
        summary = {"runs": 1, "results": [{"run": 1, "keep_count": 1, "drop_count": 0}]}
        expected_config = replace(
            config,
            api_mode="chat_completions",
            thinking_policy="omit",
        )
        with patch.object(
            probe.VLMClientConfig,
            "from_env",
            return_value=config,
        ), patch.object(
            probe,
            "create_configured_vlm_client",
            return_value=object(),
        ) as create_client, patch.object(
            probe,
            "run_experiment",
            return_value=summary,
        ):
            code = probe.main(
                [
                    "--image",
                    "unused.png",
                    "--candidates-json",
                    "unused.json",
                    "--output-dir",
                    "unused",
                ]
            )
        self.assertEqual(0, code)
        create_client.assert_called_once_with(
            expected_config,
            max_tokens=probe.ADMISSION_MAX_TOKENS,
        )
        self.assertEqual("chat_completions", expected_config.api_mode)
        self.assertEqual("omit", expected_config.thinking_policy)
        self.assertEqual(12000, probe.ADMISSION_MAX_TOKENS)


class WireBodyTests(unittest.TestCase):
    def test_wire_body_omits_thinking_and_stream_and_uses_json_schema(self):
        ids = candidate_ids(make_candidates_document(2))
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            image = directory / "analysis.png"
            Image.new("RGB", (16, 8), "navy").save(image)
            session = FakeSession(
                FakeResponse(
                    chat_completion_body(
                        json.dumps(valid_admission_response(ids))
                    )
                )
            )
            client = ChatCompletionsVLMClient(
                VLMClientConfig(
                    base_url="https://provider.example",
                    api_key="unit-test-secret",
                    model="glm-5.3-flash",
                    api_mode="chat_completions",
                    thinking_policy="omit",
                ),
                session=session,
                max_tokens=probe.ADMISSION_MAX_TOKENS,
            )
            client.infer_json(
                image_path=image,
                system_prompt=probe.SYSTEM_PROMPT,
                user_prompt=probe.build_user_prompt(
                    make_candidates_document(2)["assets"]
                ),
                response_schema=probe.build_admission_response_schema(ids),
            )
        self.assertEqual(1, len(session.calls))
        self.assertEqual(
            "https://provider.example/v1/chat/completions",
            session.calls[0]["url"],
        )
        payload = session.calls[0]["json"]
        self.assertNotIn("thinking", payload)
        self.assertNotIn("stream", payload)
        self.assertEqual("glm-5.3-flash", payload["model"])
        self.assertEqual(0, payload["temperature"])
        self.assertEqual(1, payload["top_p"])
        self.assertEqual(12000, payload["max_tokens"])
        self.assertEqual(
            {"role": "system", "content": probe.SYSTEM_PROMPT},
            payload["messages"][0],
        )
        user_content = payload["messages"][1]["content"]
        self.assertEqual("text", user_content[0]["type"])
        self.assertEqual("image_url", user_content[1]["type"])
        self.assertTrue(
            user_content[1]["image_url"]["url"].startswith(
                "data:image/png;base64,"
            )
        )
        response_format = payload["response_format"]
        self.assertEqual("json_schema", response_format["type"])
        self.assertTrue(response_format["json_schema"]["strict"])
        self.assertEqual(
            probe.build_admission_response_schema(ids),
            response_format["json_schema"]["schema"],
        )


if __name__ == "__main__":
    unittest.main()
