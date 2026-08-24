"""Frozen Stage2-B1 Extraction Plan v0.1 contract and planner boundary."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping, Protocol

from jsonschema import Draft202012Validator


SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "extraction-plan.schema.json"

EXTRACTION_MODES = frozenset(
    {"direct_crop", "foreground_extract", "repair_required"}
)
BACKENDS = frozenset({"direct", "color_distance", "grabcut", "unknown"})
MODE_BACKENDS = {
    "direct_crop": frozenset({"direct"}),
    "foreground_extract": frozenset(
        {"color_distance", "grabcut", "unknown"}
    ),
    "repair_required": frozenset({"unknown"}),
}
QUALITY_CHECKS = (
    "empty_output",
    "empty_mask",
    "bbox_outside",
    "foreground_ratio_abnormal",
    "background_ratio_abnormal",
    "extraction_failure",
)
RESERVED_METADATA_KEYS = frozenset(
    {
        "node_id",
        "taxonomy",
        "input_bbox",
        "source_crop",
        "planner_policy",
        "decision_reason",
    }
)


@dataclass(frozen=True)
class BBox:
    """Immutable Stage2-A bbox copied into the B1 plan without refinement."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in (self.x, self.y, self.width, self.height)
        ):
            raise TypeError("bbox values must be integers")
        if self.x < 0 or self.y < 0:
            raise ValueError("bbox x and y must be non-negative")
        if self.width < 1 or self.height < 1:
            raise ValueError("bbox width and height must be positive")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BBox":
        if not isinstance(value, Mapping):
            raise TypeError("bbox must be an object")
        try:
            return cls(
                x=value["x"],
                y=value["y"],
                width=value["width"],
                height=value["height"],
            )
        except KeyError as exc:
            raise ValueError(f"bbox is missing {exc.args[0]!r}") from exc

    def to_dict(self) -> dict[str, int]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class AssetLeaf:
    """Minimal immutable handoff from Stage2-A into Stage2-B1."""

    asset_id: str
    node_id: str
    taxonomy: str
    bbox: BBox
    source_crop: str

    def __post_init__(self) -> None:
        for name in ("asset_id", "node_id", "taxonomy", "source_crop"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AssetLeaf":
        if not isinstance(value, Mapping):
            raise TypeError("asset leaf must be an object")
        try:
            return cls(
                asset_id=value["asset_id"],
                node_id=value["node_id"],
                taxonomy=value["taxonomy"],
                bbox=BBox.from_mapping(value["bbox"]),
                source_crop=str(value["source_crop"]),
            )
        except KeyError as exc:
            raise ValueError(f"asset leaf is missing {exc.args[0]!r}") from exc


@dataclass(frozen=True)
class PlanningDecision:
    """Policy result consumed by the contract-owning planner."""

    extraction_mode: str
    backend: str
    confidence: float
    reason: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.extraction_mode not in EXTRACTION_MODES:
            raise ValueError(f"unsupported extraction_mode: {self.extraction_mode!r}")
        if self.backend not in BACKENDS:
            raise ValueError(f"unsupported backend: {self.backend!r}")
        if self.backend not in MODE_BACKENDS[self.extraction_mode]:
            raise ValueError(
                f"backend {self.backend!r} is invalid for "
                f"{self.extraction_mode!r}"
            )
        if isinstance(self.confidence, bool) or not isinstance(
            self.confidence, (int, float)
        ):
            raise TypeError("confidence must be numeric")
        if not 0 <= float(self.confidence) <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be an object")
        reserved = RESERVED_METADATA_KEYS.intersection(self.metadata)
        if reserved:
            raise ValueError(
                "policy metadata cannot replace B1 lineage fields: "
                + ", ".join(sorted(reserved))
            )


class PlanningPolicy(Protocol):
    """Extension point for deterministic, non-VLM planning evidence."""

    name: str

    def decide(self, asset_leaf: AssetLeaf) -> PlanningDecision:
        """Return a decision without changing or reclassifying the asset leaf."""


class ConservativePlanningPolicy:
    """Fail closed when v0.1 has no deterministic pixel evidence."""

    name = "conservative_v0.1"

    def decide(self, asset_leaf: AssetLeaf) -> PlanningDecision:
        # Deliberately independent of taxonomy. A later deterministic policy may
        # inspect pixels or upstream evidence behind this same interface.
        return PlanningDecision(
            extraction_mode="repair_required",
            backend="unknown",
            confidence=0.0,
            reason="No deterministic extraction evidence was supplied.",
        )


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_extraction_plan(document: Mapping[str, Any]) -> list[str]:
    """Return stable validation messages without mutating the document."""

    validator = Draft202012Validator(load_schema())
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    return [error.message for error in errors]


def assert_valid_extraction_plan(document: Mapping[str, Any]) -> None:
    errors = validate_extraction_plan(document)
    if errors:
        raise ValueError("invalid ExtractionPlan v0.1: " + "; ".join(errors))


class ExtractionPlanner:
    """Create the frozen plan envelope around a replaceable planning policy."""

    def __init__(self, policy: PlanningPolicy | None = None) -> None:
        self.policy = policy or ConservativePlanningPolicy()

    def plan(self, asset_leaf: AssetLeaf | Mapping[str, Any]) -> dict[str, Any]:
        leaf = (
            asset_leaf
            if isinstance(asset_leaf, AssetLeaf)
            else AssetLeaf.from_mapping(asset_leaf)
        )
        decision = self.policy.decide(leaf)
        if not isinstance(decision, PlanningDecision):
            raise TypeError("planning policy must return PlanningDecision")

        metadata = deepcopy(dict(decision.metadata))
        metadata.update(
            {
                "node_id": leaf.node_id,
                "taxonomy": leaf.taxonomy,
                "input_bbox": leaf.bbox.to_dict(),
                "source_crop": leaf.source_crop,
                "planner_policy": self.policy.name,
                "decision_reason": decision.reason,
            }
        )
        document = {
            "asset_id": leaf.asset_id,
            "extraction_mode": decision.extraction_mode,
            "backend": decision.backend,
            "confidence": float(decision.confidence),
            "quality_gate": {
                "required": True,
                "checks": list(QUALITY_CHECKS),
                "foreground_ratio": {"min": 0.01, "max": 0.99},
                "background_ratio": {"min": 0.01, "max": 0.99},
            },
            "metadata": metadata,
        }
        assert_valid_extraction_plan(document)
        return document
