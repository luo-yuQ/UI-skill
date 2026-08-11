#!/usr/bin/env python3
"""Synchronize deterministic Stage 1 image inputs for an existing Runner run."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    from PIL import Image, UnidentifiedImageError
except ModuleNotFoundError:  # pragma: no cover - exercised only without dependency
    Image = None  # type: ignore[assignment]

    class UnidentifiedImageError(OSError):
        """Fallback type used only to keep the dependency error path importable."""


SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


class InputSyncError(ValueError):
    """Raised when an existing run cannot be synchronized safely."""


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


def orientation_for(width: int, height: int) -> str:
    if width > height:
        return "landscape"
    if height > width:
        return "portrait"
    return "square"


def image_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise InputSyncError(f"Input directory not found: {directory}")
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.casefold() in SUPPORTED_EXTENSIONS
        ),
        key=lambda path: (path.name.casefold(), path.name),
    )


def inspect_references(run_root: Path, kind: str) -> list[dict[str, Any]]:
    if Image is None:
        raise InputSyncError(
            "Pillow is required to read image dimensions; install the 'Pillow' package"
        )

    directory = run_root / "00-input" / f"{kind}-reference"
    records: list[dict[str, Any]] = []
    for index, image_path in enumerate(image_files(directory), start=1):
        try:
            with Image.open(image_path) as image:
                width, height = image.size
                image.load()
        except (OSError, UnidentifiedImageError) as exc:
            raise InputSyncError(f"Unable to read image {image_path}: {exc}") from exc

        if width < 1 or height < 1:
            raise InputSyncError(
                f"Image dimensions must be positive for {image_path}: {width}x{height}"
            )

        relative_path = image_path.relative_to(run_root).as_posix()
        records.append(
            {
                "reference_id": f"{kind}-{index:03d}",
                "path": relative_path,
                "file_name": image_path.name,
                "width": width,
                "height": height,
                "orientation": orientation_for(width, height),
            }
        )
    return records


def sync_stage1_inputs(run_path: Path) -> dict[str, Any]:
    run_root = run_path.resolve()
    if not run_root.is_dir():
        raise InputSyncError(f"Run directory not found: {run_path}")

    request_path = run_root / "00-input" / "request.json"
    if not request_path.is_file():
        raise InputSyncError(f"request.json not found: {request_path}")

    request = load_json(request_path)
    if not isinstance(request, dict):
        raise InputSyncError("request.json must contain a JSON object")
    if "user_requirement" not in request:
        raise InputSyncError("request.json is missing user_requirement")
    original_requirement = request["user_requirement"]

    layout_metadata = inspect_references(run_root, "layout")
    style_metadata = inspect_references(run_root, "style")
    request["layout_references"] = [item["path"] for item in layout_metadata]
    request["style_references"] = [item["path"] for item in style_metadata]

    metadata = {
        "layout_references": layout_metadata,
        "style_references": style_metadata,
    }
    metadata_path = run_root / "00-input" / "input-metadata.json"
    write_json_atomic(request_path, request)
    write_json_atomic(metadata_path, metadata)

    written_request = load_json(request_path)
    if written_request.get("user_requirement") != original_requirement:
        raise InputSyncError("user_requirement changed while synchronizing request.json")

    return {
        "status": "synchronized",
        "run": str(run_root),
        "request": str(request_path),
        "metadata": str(metadata_path),
        "layout_reference_count": len(layout_metadata),
        "style_reference_count": len(style_metadata),
        "user_requirement_preserved": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path, help="Existing Runner run path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = sync_stage1_inputs(args.run)
    except (OSError, UnicodeError, json.JSONDecodeError, InputSyncError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
