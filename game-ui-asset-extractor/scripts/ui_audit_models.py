"""Pydantic v2 contracts for Stage B UI text and asset auditing."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _AuditModel(BaseModel):
    """Strict base contract shared by all Stage B audit models."""

    model_config = ConfigDict(extra="forbid")


class StrippedSymbol(_AuditModel):
    """Action symbol separated from a composite OCR text candidate."""

    source_text_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    role: str = Field(default="button", min_length=1)


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
