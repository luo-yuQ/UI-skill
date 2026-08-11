#!/usr/bin/env python3
"""Inject Runner-managed deterministic source metadata into an A1 analysis."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


REFERENCE_ID = "layout-001"
SOURCE_FIELDS = ("file_name", "width", "height", "orientation")


class SourceInjectionError(ValueError):
    """Raised when A1 source metadata cannot be injected safely."""


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, document: Any) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def validate_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise SourceInjectionError(f"Metadata for {REFERENCE_ID} must be an object")
    if record.get("reference_id") != REFERENCE_ID:
        raise SourceInjectionError(f"Metadata is not for {REFERENCE_ID}")

    for field in ("path", *SOURCE_FIELDS):
        if field not in record:
            raise SourceInjectionError(f"Metadata for {REFERENCE_ID} is missing {field}")

    if not isinstance(record["path"], str) or not record["path"]:
        raise SourceInjectionError(f"Metadata path for {REFERENCE_ID} must be a string")
    if not isinstance(record["file_name"], str) or not record["file_name"]:
        raise SourceInjectionError(f"Metadata file_name for {REFERENCE_ID} must be a string")
    for field in ("width", "height"):
        value = record[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise SourceInjectionError(
                f"Metadata {field} for {REFERENCE_ID} must be a positive integer"
            )

    width = record["width"]
    height = record["height"]
    expected_orientation = (
        "landscape" if width > height else "portrait" if height > width else "square"
    )
    if record["orientation"] != expected_orientation:
        raise SourceInjectionError(
            f"Metadata orientation for {REFERENCE_ID} must be {expected_orientation!r}"
        )
    if Path(record["path"]).name != record["file_name"]:
        raise SourceInjectionError(
            f"Metadata path and file_name disagree for {REFERENCE_ID}"
        )
    return record


def inject_a1_source(run_path: Path) -> dict[str, Any]:
    run_root = run_path.resolve()
    if not run_root.is_dir():
        raise SourceInjectionError(f"Run directory not found: {run_path}")

    metadata_path = run_root / "00-input" / "input-metadata.json"
    analysis_path = run_root / "10-layout-reference" / "layout-analysis.json"
    if not metadata_path.is_file():
        raise SourceInjectionError(f"input-metadata.json not found: {metadata_path}")
    if not analysis_path.is_file():
        raise SourceInjectionError(f"layout-analysis.json not found: {analysis_path}")

    metadata = load_json(metadata_path)
    if not isinstance(metadata, dict):
        raise SourceInjectionError("input-metadata.json must contain a JSON object")
    references = metadata.get("layout_references")
    if not isinstance(references, list):
        raise SourceInjectionError("input-metadata.json layout_references must be an array")
    matching = [item for item in references if isinstance(item, dict) and item.get("reference_id") == REFERENCE_ID]
    if len(matching) != 1:
        raise SourceInjectionError(
            f"Expected exactly one {REFERENCE_ID} metadata record, found {len(matching)}"
        )
    record = validate_record(matching[0])

    analysis = load_json(analysis_path)
    if not isinstance(analysis, dict):
        raise SourceInjectionError("layout-analysis.json must contain a JSON object")
    source = analysis.get("source")
    if not isinstance(source, dict):
        raise SourceInjectionError("layout-analysis.json source must be an object")

    source["source_ref"] = f"run-input:{REFERENCE_ID}"
    for field in SOURCE_FIELDS:
        source[field] = record[field]
    write_json_atomic(analysis_path, analysis)

    return {
        "status": "injected",
        "run": str(run_root),
        "analysis": str(analysis_path),
        "reference_id": REFERENCE_ID,
        "source_ref": source["source_ref"],
        "file_name": source["file_name"],
        "width": source["width"],
        "height": source["height"],
        "orientation": source["orientation"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path, help="Existing Runner run path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = inject_a1_source(args.run)
    except (OSError, UnicodeError, json.JSONDecodeError, SourceInjectionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
