#!/usr/bin/env python3
"""Build immutable, deterministic A/B evidence registries from real upstream JSON."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


ASourceType = Literal[
    "region",
    "region_relationship",
    "component_group",
    "visual_hierarchy",
    "layout_rule",
    "excluded_content",
    "uncertainty",
]
TraitClassification = Literal["stable", "secondary", "local", "conflicting", "uncertain"]

# These are declaration fields verified against the current authoritative A schema.
# Values are never listed or hard-coded; they are collected from each input artifact.
A_DECLARATION_FIELDS: dict[str, ASourceType] = {
    "region_id": "region",
    "relationship_id": "region_relationship",
    "group_id": "component_group",
    "entity_id": "visual_hierarchy",
    "rule_id": "layout_rule",
    "uncertainty_id": "uncertainty",
}
TRAIT_CLASSIFICATIONS: tuple[TraitClassification, ...] = (
    "stable",
    "secondary",
    "local",
    "conflicting",
    "uncertain",
)


class ASourceRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    source_type: ASourceType
    path: str
    field_name: str


class BTraitRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    trait_id: str
    dimension: str
    classification: TraitClassification
    path: str


class EvidenceRegistry(BaseModel):
    """Immutable registry snapshot. It contains copied IDs, never upstream objects."""

    model_config = ConfigDict(frozen=True)

    a_sources: tuple[ASourceRecord, ...]
    b_traits: tuple[BTraitRecord, ...]

    def a_matches(self, source_id: str, source_type: str | None = None) -> tuple[ASourceRecord, ...]:
        return tuple(
            record
            for record in self.a_sources
            if record.source_id == source_id
            and (source_type is None or record.source_type == source_type)
        )

    def b_match(self, trait_id: str) -> BTraitRecord | None:
        return next((record for record in self.b_traits if record.trait_id == trait_id), None)


def _json_path(parent: str, part: str | int) -> str:
    return f"{parent}[{part}]" if isinstance(part, int) else f"{parent}.{part}"


def collect_a_source_records(layout: Any) -> tuple[ASourceRecord, ...]:
    """Recursively collect actual A declaration values and their paths."""

    records: list[ASourceRecord] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = _json_path(path, key)
                source_type = A_DECLARATION_FIELDS.get(key)
                if source_type is not None and isinstance(child, str):
                    records.append(
                        ASourceRecord(
                            source_id=child,
                            source_type=source_type,
                            path=child_path,
                            field_name=key,
                        )
                    )
                # excluded_content.category is a stable schema enum trace token,
                # not an *_id field, so accept it only inside that collection.
                if (
                    key == "category"
                    and isinstance(child, str)
                    and ".excluded_content[" in child_path
                ):
                    records.append(
                        ASourceRecord(
                            source_id=child,
                            source_type="excluded_content",
                            path=child_path,
                            field_name=key,
                        )
                    )
                visit(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, _json_path(path, index))

    visit(layout, "$.layout_reference_analysis")
    unique = {
        (record.source_id, record.source_type, record.path, record.field_name): record
        for record in records
    }
    return tuple(unique[key] for key in sorted(unique))


def collect_b_trait_records(style: Any) -> tuple[BTraitRecord, ...]:
    """Collect B2 trait IDs with the classification declared by B2 itself."""

    records: list[BTraitRecord] = []
    profiles = style.get("visual_profiles", {}) if isinstance(style, dict) else {}
    if not isinstance(profiles, dict):
        return ()
    for profile_name, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        dimension = profile_name.removesuffix("_profile")
        for collection_classification in TRAIT_CLASSIFICATIONS:
            traits = profile.get(collection_classification, [])
            if not isinstance(traits, list):
                continue
            for index, trait in enumerate(traits):
                if not isinstance(trait, dict) or not isinstance(trait.get("trait_id"), str):
                    continue
                classification = trait.get("classification")
                if classification not in TRAIT_CLASSIFICATIONS:
                    continue
                records.append(
                    BTraitRecord(
                        trait_id=trait["trait_id"],
                        dimension=dimension,
                        classification=classification,
                        path=(
                            f"$.style_profile.visual_profiles.{profile_name}."
                            f"{collection_classification}[{index}].trait_id"
                        ),
                    )
                )
    by_id: dict[str, BTraitRecord] = {}
    for record in records:
        previous = by_id.get(record.trait_id)
        if previous and (
            previous.dimension != record.dimension
            or previous.classification != record.classification
        ):
            raise ValueError(
                f"Duplicate B trait_id with conflicting registry facts: {record.trait_id}"
            )
        by_id[record.trait_id] = record
    return tuple(by_id[key] for key in sorted(by_id))


def build_evidence_registry(layout: Any, style: Any) -> EvidenceRegistry:
    return EvidenceRegistry(
        a_sources=collect_a_source_records(layout),
        b_traits=collect_b_trait_records(style),
    )


def collect_a_ids(layout: Any) -> dict[str, set[str]]:
    """Compatibility view used by tests and callers."""

    result: dict[str, set[str]] = {
        source_type: set()
        for source_type in (
            "region",
            "region_relationship",
            "component_group",
            "visual_hierarchy",
            "layout_rule",
            "excluded_content",
            "uncertainty",
        )
    }
    for record in collect_a_source_records(layout):
        result[record.source_type].add(record.source_id)
    return result


def collect_b_traits(style: Any) -> dict[str, tuple[str, str]]:
    """Compatibility view: trait_id -> (dimension, classification)."""

    return {
        record.trait_id: (record.dimension, record.classification)
        for record in collect_b_trait_records(style)
    }
