#!/usr/bin/env python3
"""Deterministic fixture adapters for Recursive Runtime tests and local demos."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping


class FixtureResultAdapter:
    """Return configured JSON by node artifact directory, path, or call order."""

    def __init__(
        self,
        results: Mapping[str, dict[str, Any]] | list[dict[str, Any]],
    ) -> None:
        self._results = copy.deepcopy(results)
        self.calls: list[str] = []

    def _lookup(self, analysis_image: Path) -> dict[str, Any]:
        if isinstance(self._results, list):
            index = len(self.calls) - 1
            if index >= len(self._results):
                raise ValueError("fixture adapter has no result for this call")
            return self._results[index]

        candidates = (
            str(analysis_image),
            analysis_image.as_posix(),
            analysis_image.parent.name,
            analysis_image.stem,
        )
        for key in candidates:
            if key in self._results:
                return self._results[key]
        raise ValueError(
            f"fixture adapter has no result for Analysis Image {analysis_image}"
        )

    def _run(self, analysis_image: Path) -> dict[str, Any]:
        path = Path(analysis_image)
        self.calls.append(str(path))
        result = self._lookup(path)
        if not isinstance(result, dict):
            raise ValueError("fixture adapter result must be a JSON object")
        return copy.deepcopy(result)


class FixtureRouterAdapter(FixtureResultAdapter):
    def route(self, analysis_image: Path) -> dict[str, Any]:
        return self._run(analysis_image)


class FixtureStructuralSplitAdapter(FixtureResultAdapter):
    def run(self, analysis_image: Path) -> dict[str, Any]:
        return self._run(analysis_image)


class FixtureExpandInstancesAdapter(FixtureResultAdapter):
    def run(self, analysis_image: Path) -> dict[str, Any]:
        return self._run(analysis_image)


class FixtureSemanticDecomposeAdapter(FixtureResultAdapter):
    def run(self, analysis_image: Path) -> dict[str, Any]:
        return self._run(analysis_image)

