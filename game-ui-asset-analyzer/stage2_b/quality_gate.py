"""Deterministic Stage2-B1 extraction quality checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .extraction_executor import ExtractionArtifact
from .extraction_plan import assert_valid_extraction_plan


@dataclass(frozen=True)
class QualityGateResult:
    passed: bool
    issues: tuple[str, ...]
    metrics: Mapping[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "issues": list(self.issues),
            "metrics": dict(self.metrics),
        }


class ExtractionQualityGate:
    """Reject mechanical extraction anomalies without semantic image rules."""

    def evaluate(
        self,
        plan: Mapping[str, Any],
        artifact: ExtractionArtifact,
    ) -> QualityGateResult:
        assert_valid_extraction_plan(plan)
        configured = set(plan["quality_gate"]["checks"])
        issues: list[str] = []
        metrics: dict[str, float] = {}

        if "extraction_failure" in configured and (
            not artifact.success or artifact.error is not None
        ):
            issues.append("extraction_failure")

        if "empty_output" in configured and (
            not artifact.png_bytes or artifact.width < 1 or artifact.height < 1
        ):
            issues.append("empty_output")

        bbox = plan["metadata"]["input_bbox"]
        if (
            "bbox_outside" in configured
            and artifact.source_width is not None
            and artifact.source_height is not None
            and (
                bbox["x"] + bbox["width"] > artifact.source_width
                or bbox["y"] + bbox["height"] > artifact.source_height
            )
        ):
            issues.append("bbox_outside")

        total = artifact.total_mask_pixels
        foreground = artifact.foreground_pixels
        background = artifact.background_pixels
        if total is not None and total > 0 and foreground is not None:
            foreground_ratio = foreground / total
            metrics["foreground_ratio"] = foreground_ratio
            if "empty_mask" in configured and foreground == 0:
                issues.append("empty_mask")
            bounds = plan["quality_gate"]["foreground_ratio"]
            if "foreground_ratio_abnormal" in configured and not (
                bounds["min"] <= foreground_ratio <= bounds["max"]
            ):
                issues.append("foreground_ratio_abnormal")

        if total is not None and total > 0:
            if background is None and foreground is not None:
                background = total - foreground
            if background is not None:
                background_ratio = background / total
                metrics["background_ratio"] = background_ratio
                bounds = plan["quality_gate"]["background_ratio"]
                if "background_ratio_abnormal" in configured and not (
                    bounds["min"] <= background_ratio <= bounds["max"]
                ):
                    issues.append("background_ratio_abnormal")

        unique_issues = tuple(dict.fromkeys(issues))
        return QualityGateResult(
            passed=not unique_issues,
            issues=unique_issues,
            metrics=metrics,
        )
