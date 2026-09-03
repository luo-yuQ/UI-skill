#!/usr/bin/env python3
"""Stage2-A2 experiment: VLM admission gate over A1 direct-asset candidates.

The admission gate only emits KEEP / DROP decisions for an already-discovered
candidate set. It never discovers, merges, re-crops, or re-scores candidates.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Collection

from jsonschema import Draft202012Validator
from PIL import Image, UnidentifiedImageError


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from vlm_client import (  # noqa: E402
    VLMClient,
    VLMClientConfig,
    VLMError,
    create_configured_vlm_client,
)


ADMISSION_SCHEMA_VERSION = "asset-admission-v0.1"
ADMISSION_MAX_TOKENS = 12000
DECISION_VALUES = ("KEEP", "DROP")
REASON_CODES = (
    "KEEP_INDEPENDENT_ASSET",
    "DROP_STRUCTURAL_CONTAINER",
    "DROP_REDUNDANT_COMPOSITE",
    "DROP_STATE_EFFECT",
    "DROP_INCIDENTAL_DECORATION",
    "DROP_DEPENDENT_SUBSTRUCTURE",
    "DROP_DUPLICATE",
)
FORBIDDEN_OUTPUT_FIELDS = ("bbox", "taxonomy", "label")
REQUIRED_CANDIDATE_FIELDS = (
    "id",
    "label",
    "taxonomy",
    "bbox_analysis",
    "bbox_source",
    "partial",
    "confidence",
)
SYSTEM_PROMPT = """You are the Stage2-A2 Asset Admission Gate.

Stage2-A1 has already discovered a high-recall set of visual asset candidates
from one Analysis Image.

Your job is to review those existing candidates and decide which candidates
should survive into the final production asset set.

Every candidate is identified by candidate_ref and bbox_analysis.
Always ground your judgment in the visual region specified by bbox_analysis.

Do not discover new assets.
Do not change, merge, split, move, or re-crop candidates.
Do not infer candidate meaning from candidate_ref numbering."""


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_runs(runs: int) -> None:
    if runs < 1:
        raise ValueError("--runs must be at least 1")


def build_admission_response_schema(ids: Collection[str]) -> dict[str, Any]:
    """Build the frozen admission response schema pinned to one candidate set."""

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "decisions"],
        "properties": {
            "schema_version": {"const": ADMISSION_SCHEMA_VERSION},
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "candidate_ref",
                        "decision",
                        "reason_code",
                        "confidence",
                        "reason",
                    ],
                    "properties": {
                        "candidate_ref": {"type": "string", "enum": sorted(ids)},
                        "decision": {"type": "string", "enum": list(DECISION_VALUES)},
                        "reason_code": {"type": "string", "enum": list(REASON_CODES)},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "reason": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    }


def build_user_prompt(candidates: Collection[dict[str, Any]]) -> str:
    """Build the A2 grounding admission prompt v0.2 for one complete candidate set."""

    # Grounding manifest: A1 fields are passed through verbatim. bbox_analysis is
    # never modified, converted, or re-keyed, and bbox_source is not exposed.
    manifest_entries = [
        {
            "candidate_ref": asset["id"],
            "label_hint": asset["label"],
            "taxonomy_hint": asset["taxonomy"],
            "bbox_analysis": asset["bbox_analysis"],
        }
        for asset in candidates
    ]
    manifest = "\n".join(
        json.dumps(entry, ensure_ascii=False) for entry in manifest_entries
    )
    return f"""You are the Stage2-A2 Asset Admission Gate.

Stage2-A1 Direct Asset Discovery has already produced a high-recall candidate set
for the attached Analysis Image. Decide whether each already-discovered candidate
should remain in the final production asset set for reconstructing this UI.

A1 = high-recall candidate discovery.
A2 = remove candidates that should NOT survive into the final production asset set.
A2 does not discover new assets and never proposes a candidate_ref outside the list.

You MUST NOT:
- discover new assets or emit any candidate_ref that is not in the list
- merge, split, reorder, or re-crop candidates
- correct, refine, or reinterpret any candidate bbox
- assign taxonomy values or labels
- invent new reason codes

Do not infer candidate identity from candidate_ref numbering.

Candidate manifest ({len(manifest_entries)} entries). Each entry describes one A1
candidate; candidate_ref comes from the A1 id, and bbox_analysis is passed through
from A1 without any modification or coordinate conversion:

{manifest}

Grounding contract. bbox_analysis is expressed in pixels in the attached Analysis Image.
The candidate bbox is authoritative for identifying which visual object the candidate
refers to. label_hint and taxonomy_hint are only hints produced by A1.
Do not reject a candidate merely because the hint appears imperfect. Judge the visual
region identified by candidate_ref + bbox_analysis.

For every candidate:

1. Read candidate_ref.
2. Locate bbox_analysis in the attached Analysis Image.
3. Inspect the visual object inside that bbox.
4. Use label_hint and taxonomy_hint only as supporting hints.
5. Compare the candidate with the other provided candidates when deciding
   structural container, redundant composite, or true duplicate.
6. Emit exactly one KEEP or DROP decision for that candidate_ref.

Never infer candidate identity from candidate_ref numbering.

asset_001, asset_002, ... have no semantic ordering.

Do not guess what an ID represents.
Use bbox_analysis to ground every decision.

KEEP definition:

Keep the candidate when the visual region represents a production asset
that should survive as an independent entry in the final asset set.

A candidate may still be KEEP when:

- it is visually nested inside another candidate
- it overlaps another candidate
- it is one repeated instance among several similar instances
- it is a large foundational surface such as a background
- its label_hint or taxonomy_hint is imperfect
- another candidate has similar appearance but represents a different
  physical instance at a different location

The question is whether this candidate itself should exist as an
independent production asset in the final reconstruction set.

Repeated instances at different positions are NOT duplicates.

Example:

four visually identical treasure chests at four different UI positions
are four candidate instances and may all be KEEP.

Frozen reason codes (use exactly one per decision):

- KEEP_INDEPENDENT_ASSET: keep the candidate as an independent entry in the
  final production asset set.
- DROP_STRUCTURAL_CONTAINER: the candidate primarily acts as a layout/container
  region whose meaningful visual contents are represented by other candidates,
  and the container itself should not survive as an independent production asset.
- DROP_REDUNDANT_COMPOSITE: the candidate is a composite visual candidate that
  redundantly contains multiple other independently admitted candidates, and
  retaining both the composite and its parts would duplicate the same production
  content. Do NOT use this reason merely because the candidate is large.
- DROP_STATE_EFFECT: the candidate is a transient visual state/effect layer such
  as glow, selection highlight, temporary emphasis, flash, or similar
  state-dependent visual treatment.
- DROP_INCIDENTAL_DECORATION: the candidate is minor decorative residue with no
  meaningful standalone production role.
- DROP_DEPENDENT_SUBSTRUCTURE: the candidate is only a fragment of another
  visual asset and does not have a meaningful independent production identity.
  Visual nesting alone is NOT sufficient. For example: a reusable currency or
  ticket icon placed on a button can still be KEEP, and an icon placed inside a
  navigation item can still be KEEP.
- DROP_DUPLICATE: use DROP_DUPLICATE only when two A1 candidates refer to the
  SAME physical visual object / same image region due to duplicate discovery.
  Do NOT treat repeated instances at different locations as duplicates.
  Examples: same chest detected twice with almost the same bbox → duplicate;
  four identical chest instances at four different positions → NOT duplicate.

Return JSON only, with exactly the frozen schema shape.
Do not output bbox, taxonomy, label, or any field other than the schema fields.
"""


def _format_validation_path(parts: Any) -> str:
    path = "$"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def find_forbidden_fields(value: Any, path: str = "$") -> list[str]:
    """Return JSON paths of every forbidden output field, walking nested values."""

    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if key in FORBIDDEN_OUTPUT_FIELDS:
                found.append(child)
            found.extend(find_forbidden_fields(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(find_forbidden_fields(item, f"{path}[{index}]"))
    return found


def validate_admission_response(
    response: Any,
    ids: Collection[str],
) -> list[dict[str, Any]]:
    """Enforce the deterministic admission contract: one decision per A1 id."""

    expected_ids = list(ids)
    if len(set(expected_ids)) != len(expected_ids):
        duplicates = sorted({cid for cid in expected_ids if expected_ids.count(cid) > 1})
        raise ValueError(
            "input candidate ids must be unique: duplicate candidate_ref "
            + ", ".join(duplicates)
        )
    if not isinstance(response, dict):
        raise ValueError("admission response must be a JSON object")
    decisions = response.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("$.decisions must be an array")

    errors: list[str] = []
    validator = Draft202012Validator(build_admission_response_schema(expected_ids))
    for error in sorted(
        validator.iter_errors(response), key=lambda item: list(item.absolute_path)
    ):
        errors.append(f"{_format_validation_path(error.absolute_path)}: {error.message}")

    id_set = set(expected_ids)
    counts: dict[str, int] = {}
    for index, decision in enumerate(decisions):
        ref = decision.get("candidate_ref") if isinstance(decision, dict) else None
        if isinstance(ref, str) and ref in id_set:
            counts[ref] = counts.get(ref, 0) + 1
        else:
            errors.append(f"$.decisions[{index}]: unknown candidate_ref {ref!r}")
    if len(decisions) != len(expected_ids):
        errors.append(
            f"$.decisions: expected exactly {len(expected_ids)} decisions, "
            f"got {len(decisions)}"
        )
    missing = [cid for cid in expected_ids if cid not in counts]
    if missing:
        errors.append("$.decisions: missing candidate_ref " + ", ".join(missing))
    duplicates = sorted(cid for cid, count in counts.items() if count > 1)
    if duplicates:
        errors.append("$.decisions: duplicate candidate_ref " + ", ".join(duplicates))
    for path in find_forbidden_fields(response):
        errors.append(f"{path}: forbidden output field")
    if errors:
        raise ValueError("invalid admission response:\n- " + "\n- ".join(errors))
    return decisions


def build_accepted_assets(
    document: dict[str, Any],
    decisions: Collection[dict[str, Any]],
) -> dict[str, Any]:
    """Materialize KEEP candidates by joining A1 fields with admission decisions."""

    by_id = {asset["id"]: asset for asset in document["assets"]}
    assets = []
    for decision in decisions:
        if decision["decision"] != "KEEP":
            continue
        candidate = by_id[decision["candidate_ref"]]
        assets.append(
            {
                **candidate,
                "admission_confidence": decision["confidence"],
                "admission_reason_code": decision["reason_code"],
                "admission_reason": decision["reason"],
            }
        )
    return {
        "schema_version": ADMISSION_SCHEMA_VERSION,
        "source_image": document.get("source_image"),
        "source_image_size": document.get("source_image_size"),
        "analysis_image": document.get("analysis_image"),
        "analysis_image_size": document.get("analysis_image_size"),
        "accepted_asset_count": len(assets),
        "assets": assets,
    }


def extract_provider_usage(envelope: Any) -> dict[str, Any]:
    """Extract run-summary usage fields from the raw provider envelope (or nulls)."""

    usage: dict[str, Any] = {
        "finish_reason": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "reasoning_tokens": None,
        "cached_tokens": None,
    }
    if not isinstance(envelope, dict):
        return usage
    choices = envelope.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        finish_reason = choices[0].get("finish_reason")
        if finish_reason is not None:
            usage["finish_reason"] = finish_reason
    provider_usage = envelope.get("usage")
    if isinstance(provider_usage, dict):
        usage["prompt_tokens"] = provider_usage.get("prompt_tokens")
        usage["completion_tokens"] = provider_usage.get("completion_tokens")
        completion_details = provider_usage.get("completion_tokens_details")
        if isinstance(completion_details, dict):
            usage["reasoning_tokens"] = completion_details.get("reasoning_tokens")
        prompt_details = provider_usage.get("prompt_tokens_details")
        if isinstance(prompt_details, dict):
            usage["cached_tokens"] = prompt_details.get("cached_tokens")
    return usage


def _raw_provider_response(client: VLMClient) -> Any | None:
    getter = getattr(client, "get_last_provider_response", None)
    return getter() if callable(getter) else None


def _load_candidates_document(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"candidates JSON is not valid JSON: {path}") from exc
    if not isinstance(document, dict):
        raise ValueError("candidates document must be a JSON object")
    return document


def validate_candidates_document(document: dict[str, Any]) -> list[str]:
    """Fail fast on any A1 document that cannot be admitted deterministically."""

    assets = document.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError("$.assets must be a non-empty array of candidate objects")
    ids: list[str] = []
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            raise ValueError(f"$.assets[{index}] must be a candidate object")
        for field_name in REQUIRED_CANDIDATE_FIELDS:
            if field_name not in asset:
                raise ValueError(
                    f"$.assets[{index}]: missing required A1 candidate field "
                    f"{field_name!r}"
                )
        ids.append(asset["id"])
    duplicates = sorted({cid for cid in ids if ids.count(cid) > 1})
    if duplicates:
        raise ValueError("$.assets: duplicate candidate id " + ", ".join(duplicates))
    return ids


def _read_analysis_size(image: Path) -> dict[str, int]:
    try:
        with Image.open(image) as opened:
            opened.load()
            return {"width": opened.width, "height": opened.height}
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"unable to read Analysis Image {image}: {exc}") from exc


def _verify_analysis_size(document: dict[str, Any], analysis_size: dict[str, int]) -> None:
    document_size = document.get("analysis_image_size")
    if document_size != analysis_size:
        document_width = document_size.get("width") if isinstance(document_size, dict) else None
        document_height = document_size.get("height") if isinstance(document_size, dict) else None
        raise ValueError(
            f"Analysis Image size {analysis_size['width']}x{analysis_size['height']} "
            f"does not match candidates document analysis_image_size "
            f"{document_width}x{document_height}"
        )


def run_experiment(
    image: Path,
    candidates_json: Path,
    output_dir: Path,
    *,
    client: VLMClient,
    model: str,
    runs: int = 1,
) -> dict[str, Any]:
    """Admit one complete candidate set per run and persist deterministic evidence."""

    validate_runs(runs)
    document = _load_candidates_document(candidates_json)
    ids = validate_candidates_document(document)
    analysis_size = _read_analysis_size(image)
    _verify_analysis_size(document, analysis_size)
    user_prompt = build_user_prompt(document["assets"])
    response_schema = build_admission_response_schema(ids)
    output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for run_number in range(1, runs + 1):
        run_dir = output_dir / f"run-{run_number:03d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        write_json(run_dir / "input-candidates.json", document)
        try:
            raw_result = client.infer_json(
                image_path=image,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_schema=response_schema,
            )
        except VLMError:
            provider_response = _raw_provider_response(client)
            if provider_response is not None:
                write_json(run_dir / "raw-provider-response.json", provider_response)
            raise
        provider_response = _raw_provider_response(client)
        write_json(
            run_dir / "raw-provider-response.json",
            raw_result if provider_response is None else provider_response,
        )
        decisions = validate_admission_response(raw_result, ids)
        accepted = build_accepted_assets(document, decisions)
        write_json(
            run_dir / "admission-decisions.json",
            {"schema_version": ADMISSION_SCHEMA_VERSION, "decisions": decisions},
        )
        write_json(run_dir / "accepted-assets.json", accepted)
        keep_count = sum(1 for item in decisions if item["decision"] == "KEEP")
        usage = extract_provider_usage(provider_response)
        write_json(
            run_dir / "run-summary.json",
            {
                "schema_version": ADMISSION_SCHEMA_VERSION,
                "run": run_number,
                "model": model,
                "input_candidate_count": len(ids),
                "keep_count": keep_count,
                "drop_count": len(decisions) - keep_count,
                "finish_reason": usage["finish_reason"],
                "prompt_tokens": usage["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"],
                "reasoning_tokens": usage["reasoning_tokens"],
                "cached_tokens": usage["cached_tokens"],
                "timestamp": utc_timestamp(),
            },
        )
        results.append(
            {
                "run": run_number,
                "keep_count": keep_count,
                "drop_count": len(decisions) - keep_count,
            }
        )

    summary = {
        "schema_version": ADMISSION_SCHEMA_VERSION,
        "model": model,
        "runs": runs,
        "input_candidate_count": len(ids),
        "results": results,
        "timestamp": utc_timestamp(),
    }
    write_json(output_dir / "summary.json", summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Stage2-A2 VLM admission gate (KEEP/DROP only) over one "
            "A1 direct-asset candidate set."
        )
    )
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--candidates-json", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override STAGE2A_VLM_MODEL for this experiment.",
    )
    parser.add_argument("--runs", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        validate_runs(args.runs)
        config = replace(
            VLMClientConfig.from_env(model_override=args.model),
            api_mode="chat_completions",
            thinking_policy="omit",
        )
        client = create_configured_vlm_client(
            config,
            max_tokens=ADMISSION_MAX_TOKENS,
        )
        summary = run_experiment(
            args.image,
            args.candidates_json,
            args.output_dir,
            client=client,
            model=config.model,
            runs=args.runs,
        )
    except (OSError, UnicodeError, ValueError, VLMError) as exc:
        print(f"Asset admission probe failed: {exc}", file=sys.stderr)
        return 1
    counts = ", ".join(
        f"run-{item['run']:03d}=keep {item['keep_count']}/drop {item['drop_count']}"
        for item in summary["results"]
    )
    print(f"Completed {summary['runs']} asset admission run(s): {counts}")
    print(f"Wrote experiment outputs to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
