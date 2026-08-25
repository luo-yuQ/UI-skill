"""Pydantic v2 contracts for Stage 2.1.4 UI text auditing."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _AuditModel(BaseModel):
    """Strict base contract shared by all text-audit models."""

    model_config = ConfigDict(extra="forbid")


class Rect(_AuditModel):
    """Axis-aligned rectangle in source-image pixels."""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class TextStyle(_AuditModel):
    """Typography contract aligned with the Stage 2.1.3 output."""

    fontFamily: Literal["Microsoft YaHei", "Arial"]
    fontSize: int = Field(ge=8)
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    fontWeight: Literal[600, 700]
    strokeColor: Literal["#1e2322", "#f0f4f1"]
    strokeWidth: int = Field(ge=0, le=2)


class TextItem(_AuditModel):
    """Editable OCR item used by the local mask reconstruction step."""

    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    rect: Rect
    style: TextStyle
    mask_mode: str = Field(default="estimated_glyphs", min_length=1)


class TextCorrection(_AuditModel):
    """A small text item missed by Stage A and supplied by visual auditing."""

    text: str = Field(min_length=1)
    bbox_norm: list[float] = Field(min_length=4, max_length=4)
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    estimated_role: str = Field(default="slot_count", min_length=1)

    @model_validator(mode="after")
    def validate_bbox(self) -> TextCorrection:
        """Require an ordered, non-empty normalized rectangle."""

        if any(value < 0.0 or value > 1.0 for value in self.bbox_norm):
            raise ValueError("bbox_norm values must be between 0.0 and 1.0")
        x0, y0, x1, y1 = self.bbox_norm
        if x1 <= x0 or y1 <= y0:
            raise ValueError("bbox_norm must satisfy x0 < x1 and y0 < y1")
        return self


class StrippedSymbol(_AuditModel):
    """Action symbol separated from a composite OCR text candidate."""

    source_text_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    role: str = Field(default="button", min_length=1)
    estimated_bbox_norm: list[float] | None = Field(
        default=None,
        min_length=4,
        max_length=4,
    )

    @model_validator(mode="after")
    def validate_estimated_bbox(self) -> StrippedSymbol:
        """Validate an optional normalized symbol rectangle."""

        if self.estimated_bbox_norm is None:
            return self
        if any(value < 0.0 or value > 1.0 for value in self.estimated_bbox_norm):
            raise ValueError(
                "estimated_bbox_norm values must be between 0.0 and 1.0"
            )
        x0, y0, x1, y1 = self.estimated_bbox_norm
        if x1 <= x0 or y1 <= y0:
            raise ValueError(
                "estimated_bbox_norm must satisfy x0 < x1 and y0 < y1"
            )
        return self


class EditableTextItem(_AuditModel):
    """Confirmed copy that should become an editable text layer."""

    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    role: Literal[
        "button_label",
        "value",
        "title",
        "description",
        "slot_count",
    ]


class TextAuditResult(_AuditModel):
    """Visual-semantic adjudication plus Stage A miss corrections."""

    scene_summary: str
    raster_text_ids: list[str] = Field(default_factory=list)
    editable_texts: list[EditableTextItem] = Field(default_factory=list)
    stripped_symbols: list[StrippedSymbol] = Field(default_factory=list)
    text_corrections: list[TextCorrection] = Field(default_factory=list)
