#!/usr/bin/env python3
"""Unified production implementation of the four Stage2-A visual capabilities."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import validate_expand_instances
import validate_node_route
import validate_semantic_decomposition
import validate_structural_split
from vlm_client import VLMClient, VLMError, VLMResponseParseError, VLMTransportError


ROOT = Path(__file__).resolve().parents[1]
SYSTEM_PROMPT = """You are a Stage2-A game UI visual structure analyzer.
Execute only the currently specified visual analysis contract.
Judge only from the current input image and current contract; never infer answers from historical tests.
Return output that conforms to the specified JSON schema."""


@dataclass(frozen=True)
class StrategyContract:
    reference_path: Path
    schema_path: Path
    validator: Callable[[Any, Path], list[str]]


def _route_validator(result: Any, _analysis_image: Path) -> list[str]:
    return validate_node_route.validate_document(result)


CONTRACTS = {
    "router": StrategyContract(
        ROOT / "references" / "node-router-v0.1.md",
        ROOT / "schemas" / "node-route.schema.json",
        _route_validator,
    ),
    "structural_split": StrategyContract(
        ROOT / "references" / "structural-split-v0.1.md",
        ROOT / "schemas" / "structural-split.schema.json",
        validate_structural_split.validate_document,
    ),
    "expand_instances": StrategyContract(
        ROOT / "references" / "expand-instances-v0.1.md",
        ROOT / "schemas" / "expand-instances.schema.json",
        validate_expand_instances.validate_document,
    ),
    "semantic_decompose": StrategyContract(
        ROOT / "references" / "semantic-decompose-v0.1.md",
        ROOT / "schemas" / "semantic-decomposition.schema.json",
        validate_semantic_decomposition.validate_document,
    ),
}


class StrategySchemaValidationError(ValueError):
    def __init__(self, strategy: str, errors: list[str]) -> None:
        super().__init__(
            f"strategy_schema_validation_error: {strategy}: " + "; ".join(errors)
        )


class ProductionVisualAdapter:
    """Choose only the current Stage2-A contract; never control Runtime workflow."""

    adapter_type = "production_visual"

    def __init__(self, vlm_client: VLMClient) -> None:
        self.vlm_client = vlm_client
        self.consumed_response_count = 0

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"schema root must be an object: {path}")
        return value

    @staticmethod
    def _load_prompt(path: Path) -> str:
        reference = path.read_text(encoding="utf-8")
        prompt_start = reference.find("## Production prompt")
        if prompt_start < 0:
            raise ValueError(f"frozen reference has no Production prompt section: {path}")
        prompt = reference[prompt_start:]
        end_positions = [
            position
            for heading in (
                "\n## Engineering contract",
                "\n## v0.1 behavior summary",
                "\n## Validation Evidence",
                "\n## Validation evidence",
            )
            if (position := prompt.find(heading)) >= 0
        ]
        if end_positions:
            prompt = prompt[: min(end_positions)]
        return (
            "Execute the following frozen Stage2-A contract for the attached current "
            "Analysis Image. Return JSON only.\n\n"
            + prompt.strip()
        )

    def _infer(self, strategy: str, analysis_image: Path) -> dict[str, Any]:
        image_path = Path(analysis_image)
        contract = CONTRACTS[strategy]
        user_prompt = self._load_prompt(contract.reference_path)
        response_schema = self._load_json(contract.schema_path)
        try:
            result = self.vlm_client.infer_json(
                image_path=image_path,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_schema=response_schema,
            )
        except VLMError:
            raise
        except (TimeoutError, ConnectionError, OSError) as exc:
            raise VLMTransportError(type(exc).__name__) from exc
        if not isinstance(result, dict):
            raise VLMResponseParseError("VLMClient returned a non-object result")
        errors = contract.validator(result, image_path)
        if errors:
            raise StrategySchemaValidationError(strategy, errors)
        self.consumed_response_count += 1
        return copy.deepcopy(result)

    def route(self, analysis_image: Path) -> dict[str, Any]:
        return self._infer("router", analysis_image)

    def structural_split(self, analysis_image: Path) -> dict[str, Any]:
        return self._infer("structural_split", analysis_image)

    def expand_instances(self, analysis_image: Path) -> dict[str, Any]:
        return self._infer("expand_instances", analysis_image)

    def semantic_decompose(self, analysis_image: Path) -> dict[str, Any]:
        return self._infer("semantic_decompose", analysis_image)


class _ProductionStrategyAdapter:
    """Thin run()-name compatibility view over one ProductionVisualAdapter."""

    adapter_type = "production_visual"

    def __init__(self, visual_adapter: ProductionVisualAdapter, method_name: str) -> None:
        self.visual_adapter = visual_adapter
        self.method_name = method_name

    def run(self, analysis_image: Path) -> dict[str, Any]:
        method = getattr(self.visual_adapter, self.method_name)
        return method(analysis_image)


def build_production_runtime_adapters(
    visual_adapter: ProductionVisualAdapter,
) -> Any:
    """Inject one production object through the existing four Runtime Protocol slots."""

    from recursive_runtime import RuntimeAdapters

    return RuntimeAdapters(
        router=visual_adapter,
        structural_split=_ProductionStrategyAdapter(
            visual_adapter, "structural_split"
        ),
        expand_instances=_ProductionStrategyAdapter(
            visual_adapter, "expand_instances"
        ),
        semantic_decompose=_ProductionStrategyAdapter(
            visual_adapter, "semantic_decompose"
        ),
    )
