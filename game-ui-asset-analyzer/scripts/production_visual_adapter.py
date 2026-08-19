#!/usr/bin/env python3
"""Unified production implementation of the four Stage2-A visual capabilities."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, local
from typing import Any, Callable

import validate_expand_instances
import validate_node_route
import validate_semantic_decomposition
import validate_structural_split
from bbox_boundary_canonicalizer import (
    BBOX_BOUNDARY_TOLERANCE_CAP_PX,
    BBOX_BOUNDARY_TOLERANCE_FLOOR_PX,
    BBOX_BOUNDARY_TOLERANCE_PX,
    BBOX_BOUNDARY_TOLERANCE_RATIO,
    BBOX_BOUNDARY_TOLERANCE_VERSION,
    STRATEGY_BBOX_COLLECTIONS,
    canonicalize_strategy_bboxes,
)
from runtime_geometry import read_image_size
from vlm_client import VLMClient, VLMError, VLMResponseParseError, VLMTransportError


ROOT = Path(__file__).resolve().parents[1]
SYSTEM_PROMPT = """You are a Stage2-A game UI visual structure analyzer.
Execute only the currently specified visual analysis contract.
Judge only from the current input image and current contract; never infer answers from historical tests.
Return output that conforms to the specified JSON schema."""
_CONSUMED_RESPONSE_COUNT_LOCK = Lock()


@dataclass(frozen=True)
class StrategyContract:
    reference_path: Path
    schema_path: Path
    validator: Callable[[Any, Path], list[str]]


@dataclass(frozen=True)
class ProductionRequestContext:
    request_id: str
    node_id: str
    node_role: str | None
    adapter_kind: str
    analysis_image: str


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


def canonicalize_analysis_image_size(
    result: dict[str, Any],
    response_schema: dict[str, Any],
    analysis_image: Path,
) -> None:
    """Canonicalize schema-declared image dimensions from the actual image file."""

    properties = response_schema.get("properties")
    if not isinstance(properties, dict) or "analysis_image_size" not in properties:
        return
    width, height = read_image_size(analysis_image)
    result["analysis_image_size"] = {"width": width, "height": height}


def canonicalize_semantic_contract_metadata(
    result: dict[str, Any],
    *,
    request_context: ProductionRequestContext | None,
    analysis_image: Path,
) -> None:
    """Inject semantic fields owned by the contract or current Runtime node."""

    result["task"] = "semantic_decompose"
    result["bbox_constraint"] = "completeness"
    width, height = read_image_size(analysis_image)
    result["analysis_image_size"] = {"width": width, "height": height}
    if request_context is not None:
        result["node_id"] = request_context.node_id
        result["node_role"] = request_context.node_role


def persist_bbox_boundary_diagnostic(
    *,
    strategy: str,
    analysis_image: Path,
    image_size: tuple[int, int],
    canonicalizations: list[dict[str, Any]],
) -> None:
    """Persist raw/canonical bbox evidence outside frozen strategy results."""

    if not canonicalizations:
        return
    width, height = image_size
    diagnostic = {
        "diagnostic_version": BBOX_BOUNDARY_TOLERANCE_VERSION,
        "policy": f"bbox-boundary-tolerance-v{BBOX_BOUNDARY_TOLERANCE_VERSION}",
        "strategy": strategy,
        "analysis_image": analysis_image.name,
        "analysis_image_size": {"width": width, "height": height},
        "bbox_boundary_tolerance_px": BBOX_BOUNDARY_TOLERANCE_PX,
        "bbox_boundary_tolerance_floor_px": BBOX_BOUNDARY_TOLERANCE_FLOOR_PX,
        "bbox_boundary_tolerance_cap_px": BBOX_BOUNDARY_TOLERANCE_CAP_PX,
        "bbox_boundary_tolerance_ratio": BBOX_BOUNDARY_TOLERANCE_RATIO,
        "bbox_boundary_canonicalized": True,
        "canonicalizations": canonicalizations,
    }
    path = (
        analysis_image.parent
        / f"{strategy}-bbox-boundary-canonicalization.json"
    )
    path.write_text(
        json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class ProductionVisualAdapter:
    """Choose only the current Stage2-A contract; never control Runtime workflow."""

    adapter_type = "production_visual"

    def __init__(self, vlm_client: VLMClient) -> None:
        self.vlm_client = vlm_client
        self.consumed_response_count = 0
        self._request_context = local()

    def bind_request(
        self,
        *,
        request_id: str,
        node_id: str,
        node_role: str | None,
        adapter_kind: str,
        analysis_image: str,
    ) -> None:
        """Bind caller-owned metadata for exactly one subsequent visual call."""

        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id must be a non-empty string")
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("node_id must be a non-empty string")
        if adapter_kind not in CONTRACTS:
            raise ValueError(f"unsupported production adapter kind: {adapter_kind!r}")
        if not isinstance(analysis_image, str) or not analysis_image:
            raise ValueError("analysis_image must be a non-empty string")
        self._request_context.value = ProductionRequestContext(
            request_id=request_id,
            node_id=node_id,
            node_role=node_role,
            adapter_kind=adapter_kind,
            analysis_image=analysis_image,
        )

    def _take_request_context(
        self, strategy: str
    ) -> ProductionRequestContext | None:
        context = getattr(self._request_context, "value", None)
        self._request_context.value = None
        if context is not None and context.adapter_kind != strategy:
            raise ValueError(
                "production adapter kind context mismatch: "
                f"expected {strategy!r}, got {context.adapter_kind!r}"
            )
        return context

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
                "\n## v0.1.1 behavior summary",
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
        request_context = self._take_request_context(strategy)
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
        canonical_result = copy.deepcopy(result)
        if strategy == "semantic_decompose":
            canonicalize_semantic_contract_metadata(
                canonical_result,
                request_context=request_context,
                analysis_image=image_path,
            )
        else:
            canonicalize_analysis_image_size(
                canonical_result, response_schema, image_path
            )
        image_size: tuple[int, int] | None = None
        canonicalizations: list[dict[str, Any]] = []
        if strategy in STRATEGY_BBOX_COLLECTIONS:
            image_size = read_image_size(image_path)
            canonicalizations = canonicalize_strategy_bboxes(
                canonical_result,
                strategy=strategy,
                image_size=image_size,
            )
        errors = contract.validator(canonical_result, image_path)
        if errors:
            raise StrategySchemaValidationError(strategy, errors)
        if image_size is not None:
            persist_bbox_boundary_diagnostic(
                strategy=strategy,
                analysis_image=image_path,
                image_size=image_size,
                canonicalizations=canonicalizations,
            )
        with _CONSUMED_RESPONSE_COUNT_LOCK:
            self.consumed_response_count += 1
        return canonical_result

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

    def bind_request(
        self,
        *,
        request_id: str,
        node_id: str,
        node_role: str | None,
        adapter_kind: str,
        analysis_image: str,
    ) -> None:
        self.visual_adapter.bind_request(
            request_id=request_id,
            node_id=node_id,
            node_role=node_role,
            adapter_kind=adapter_kind,
            analysis_image=analysis_image,
        )

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
