"""Pydantic v2 contracts for Stage 2.2.1 UI layer planning."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


NormalizedPoint = list[float]


class _PlanModel(BaseModel):
    """Strict base contract shared by all layer-planning models."""

    model_config = ConfigDict(extra="forbid")


class GeometryHint(_PlanModel):
    """Normalized SAM box and point prompts for one asset instance."""

    bbox_norm: list[float] = Field(min_length=4, max_length=4)
    positive_points_norm: list[NormalizedPoint] = Field(min_length=1, max_length=3)
    negative_points_norm: list[NormalizedPoint] = Field(
        default_factory=list,
        max_length=2,
    )

    @field_validator("bbox_norm")
    @classmethod
    def validate_bbox_norm(cls, value: list[float]) -> list[float]:
        """Require a non-empty normalized ``[x0, y0, x1, y1]`` box."""

        if any(
            not math.isfinite(coordinate) or coordinate < 0.0 or coordinate > 1.0
            for coordinate in value
        ):
            raise ValueError("bbox_norm coordinates must be between 0.0 and 1.0")
        x0, y0, x1, y1 = value
        if x0 >= x1 or y0 >= y1:
            raise ValueError("bbox_norm must satisfy x0 < x1 and y0 < y1")
        return value

    @field_validator("positive_points_norm", "negative_points_norm")
    @classmethod
    def validate_points(cls, value: list[NormalizedPoint]) -> list[NormalizedPoint]:
        """Require every point to be a normalized ``[x, y]`` pair."""

        for point in value:
            if len(point) != 2:
                raise ValueError("normalized points must contain exactly two values")
            if any(
                not math.isfinite(coordinate)
                or coordinate < 0.0
                or coordinate > 1.0
                for coordinate in point
            ):
                raise ValueError(
                    "normalized point coordinates must be between 0.0 and 1.0"
                )
        return value

    @model_validator(mode="after")
    def validate_positive_points_inside_bbox(self) -> GeometryHint:
        """Keep positive entity anchors inside their target bounding box."""

        x0, y0, x1, y1 = self.bbox_norm
        if any(
            not (x0 <= point[0] <= x1 and y0 <= point[1] <= y1)
            for point in self.positive_points_norm
        ):
            raise ValueError("positive points must lie inside bbox_norm")
        return self


class QueryItem(_PlanModel):
    """One independently segmentable material layer requested from SAM."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: Literal[
        "panel",
        "card",
        "slot",
        "button",
        "icon",
        "badge",
        "logo",
        "decoration",
    ]
    role: Literal["container", "interactive", "visual_artwork", "foreground"]
    parent_query_id: str | None
    z_order: int
    element_repair_mode: Literal["image", "surface", "none"]
    geometry_hints: list[GeometryHint] = Field(min_length=1)


class BackgroundRepair(_PlanModel):
    """Repair strategy for the full-screen background after extraction."""

    mode: Literal["scene", "surface", "none"]
    description: str | None = None


class LayerPlanResult(_PlanModel):
    """Validated semantic layer graph and segmentation prompts."""

    scene_summary: str = Field(min_length=1)
    raster_text_ids: list[str]
    queries: list[QueryItem]
    background_repair: BackgroundRepair

    @field_validator("raster_text_ids")
    @classmethod
    def validate_raster_text_ids(cls, value: list[str]) -> list[str]:
        """Reject empty or duplicate references to Stage 0 text candidates."""

        if any(not text_id for text_id in value):
            raise ValueError("raster_text_ids must not contain empty IDs")
        if len(value) != len(set(value)):
            raise ValueError("raster_text_ids must be unique")
        return value

    @model_validator(mode="after")
    def validate_layer_topology(self) -> LayerPlanResult:
        """Validate IDs, serialized z-order, and direct material parents."""

        query_ids = [query.id for query in self.queries]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("query IDs must be unique")

        z_orders = [query.z_order for query in self.queries]
        if any(
            current >= following
            for current, following in zip(z_orders, z_orders[1:])
        ):
            raise ValueError(
                "queries must be ordered from back to front with strictly increasing z_order"
            )

        query_by_id = {query.id: query for query in self.queries}
        for query in self.queries:
            parent_id = query.parent_query_id
            if parent_id is None:
                continue
            parent = query_by_id.get(parent_id)
            if parent is None:
                raise ValueError(
                    f"parent_query_id {parent_id!r} for {query.id!r} does not exist"
                )
            if parent.z_order >= query.z_order:
                raise ValueError(
                    f"parent {parent_id!r} must have a lower z_order than child {query.id!r}"
                )
        return self
