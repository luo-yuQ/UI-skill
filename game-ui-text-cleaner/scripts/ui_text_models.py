"""Pydantic v2 contracts for Stage A UI text extraction."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _TextModel(BaseModel):
    """Strict base model shared by all Stage A contracts."""

    model_config = ConfigDict(extra="forbid")


class Rect(_TextModel):
    """Axis-aligned rectangle in source-image pixels."""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class TextStyle(_TextModel):
    """Typography inferred from local screenshot pixels."""

    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    fontFamily: Literal["Microsoft YaHei", "Arial"]
    fontSize: int = Field(ge=8)
    fontWeight: Literal[600, 700]
    strokeColor: Literal["#1e2322", "#f0f4f1"]
    strokeWidth: int = Field(ge=0, le=2)


class TextItem(_TextModel):
    """One retained OCR text candidate and its inferred presentation."""

    id: str = Field(pattern=r"^text_\d{3,}$")
    text: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    rect: Rect
    style: TextStyle
    mask_mode: Literal["estimated_glyphs", "coarse"]


class TextExtractionResult(_TextModel):
    """Complete Stage A result for one source image."""

    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    count: int = Field(ge=0)
    items: list[TextItem]

    @model_validator(mode="after")
    def validate_count(self) -> TextExtractionResult:
        """Keep the declared count consistent with the item array."""

        if self.count != len(self.items):
            raise ValueError("count must equal len(items)")
        return self
