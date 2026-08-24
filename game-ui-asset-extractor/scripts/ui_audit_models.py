"""Pydantic v2 contracts for Stage B UI text and asset auditing."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


NormalizedCoordinate = Annotated[float, Field(ge=0.0, le=1.0)]


class _AuditModel(BaseModel):
    """Strict base contract shared by all Stage B audit models."""

    model_config = ConfigDict(extra="forbid")


class Rect(_AuditModel):
    """Axis-aligned pixel rectangle."""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class TextCorrection(_AuditModel):
    """Editable text missed by Stage A OCR, located in normalized coordinates."""

    text: str = Field(min_length=1)
    bbox_norm: list[NormalizedCoordinate] = Field(min_length=4, max_length=4)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class StrippedSymbol(_AuditModel):
    """Action symbol separated from a composite OCR text candidate."""

    source_text_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    role: str = Field(default="button", min_length=1)
    estimated_bbox_norm: list[NormalizedCoordinate] | None = Field(
        default=None,
        min_length=4,
        max_length=4,
    )


class EditableTextItem(_AuditModel):
    """Confirmed copy that should become an editable text layer."""

    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    role: str = Field(min_length=1)


class TextAuditResult(_AuditModel):
    """Final Stage B adjudication of OCR copy and rasterized text artwork."""

    scene_summary: str
    raster_text_ids: list[str]
    editable_texts: list[EditableTextItem]
    stripped_symbols: list[StrippedSymbol]
    text_corrections: list[TextCorrection]
