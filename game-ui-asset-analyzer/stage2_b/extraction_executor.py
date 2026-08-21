"""Stage2-B1 executor that consumes, but never revises, ExtractionPlan v0.1."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Mapping, Protocol

from PIL import Image, UnidentifiedImageError

from .extraction_plan import (
    AssetLeaf,
    BBox,
    assert_valid_extraction_plan,
)


class ExecutionDeferred(RuntimeError):
    """Raised when a plan belongs to Stage2-C instead of B1 execution."""


@dataclass(frozen=True)
class ExtractionArtifact:
    """Backend-neutral executor result consumed by the quality gate."""

    success: bool
    png_bytes: bytes = b""
    width: int = 0
    height: int = 0
    source_width: int | None = None
    source_height: int | None = None
    foreground_pixels: int | None = None
    background_pixels: int | None = None
    total_mask_pixels: int | None = None
    error: str | None = None


class ForegroundBackend(Protocol):
    """Interface for optional color-distance or GrabCut implementations."""

    def extract(self, source_crop: Path, bbox: BBox) -> ExtractionArtifact:
        """Extract the already-planned bbox and return PNG bytes plus metrics."""


class ExtractionExecutor:
    """Execute a frozen plan with direct crop or an injected foreground backend."""

    def __init__(
        self,
        foreground_backends: Mapping[str, ForegroundBackend] | None = None,
    ) -> None:
        self.foreground_backends = dict(foreground_backends or {})

    def execute(
        self,
        plan: Mapping,
        asset_leaf: AssetLeaf | Mapping,
    ) -> ExtractionArtifact:
        assert_valid_extraction_plan(plan)
        leaf = (
            asset_leaf
            if isinstance(asset_leaf, AssetLeaf)
            else AssetLeaf.from_mapping(asset_leaf)
        )
        self._assert_unchanged_lineage(plan, leaf)

        mode = plan["extraction_mode"]
        backend_name = plan["backend"]
        if mode == "repair_required":
            raise ExecutionDeferred(
                f"asset {leaf.asset_id!r} requires Stage2-C repair"
            )
        if mode == "direct_crop":
            return self._direct_crop(Path(leaf.source_crop), leaf.bbox)

        backend = self.foreground_backends.get(backend_name)
        if backend is None:
            return ExtractionArtifact(
                success=False,
                error=f"foreground backend {backend_name!r} is not configured",
            )
        artifact = backend.extract(Path(leaf.source_crop), leaf.bbox)
        if not isinstance(artifact, ExtractionArtifact):
            raise TypeError("foreground backend must return ExtractionArtifact")
        return artifact

    @staticmethod
    def _assert_unchanged_lineage(plan: Mapping, leaf: AssetLeaf) -> None:
        metadata = plan["metadata"]
        if plan["asset_id"] != leaf.asset_id:
            raise ValueError("plan asset_id does not match the Stage2-A asset leaf")
        if metadata["node_id"] != leaf.node_id:
            raise ValueError("plan node_id does not match the Stage2-A asset leaf")
        if metadata["taxonomy"] != leaf.taxonomy:
            raise ValueError("B1 cannot reclassify the Stage2-A asset leaf")
        if metadata["input_bbox"] != leaf.bbox.to_dict():
            raise ValueError("B1 cannot modify the Stage2-A bbox")
        if metadata["source_crop"] != leaf.source_crop:
            raise ValueError("plan source_crop does not match the Stage2-A asset leaf")

    @staticmethod
    def _direct_crop(source_crop: Path, bbox: BBox) -> ExtractionArtifact:
        try:
            with Image.open(source_crop) as source:
                source.load()
                source_width, source_height = source.size
                if (
                    bbox.x + bbox.width > source_width
                    or bbox.y + bbox.height > source_height
                ):
                    return ExtractionArtifact(
                        success=False,
                        source_width=source_width,
                        source_height=source_height,
                        error="bbox is outside the source crop",
                    )
                cropped = source.crop(
                    (
                        bbox.x,
                        bbox.y,
                        bbox.x + bbox.width,
                        bbox.y + bbox.height,
                    )
                )
                output = BytesIO()
                cropped.save(output, format="PNG")
        except (OSError, UnidentifiedImageError) as exc:
            return ExtractionArtifact(success=False, error=str(exc))

        return ExtractionArtifact(
            success=True,
            png_bytes=output.getvalue(),
            width=bbox.width,
            height=bbox.height,
            source_width=source_width,
            source_height=source_height,
        )
