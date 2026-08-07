#!/usr/bin/env python3
"""Simple placeholder helper for future asset classification workflows.

This v1 script intentionally stays lightweight. It can be extended later
with vision or metadata-assisted classification.
"""
import json
import sys
from pathlib import Path

def guess_role(filename: str) -> str:
    name = filename.lower()
    if "bg" in name or "background" in name:
        return "background"
    if "btn" in name or "button" in name:
        return "button"
    if "icon" in name:
        return "icon"
    if "panel" in name or "popup" in name:
        return "panel"
    if "char" in name or "role" in name or "hero" in name:
        return "character_or_frame"
    return "unknown"

def main():
    if len(sys.argv) != 2:
        print("Usage: python classify_assets.py <input.json>")
        raise SystemExit(1)

    path = Path(sys.argv[1])
    data = json.loads(path.read_text(encoding="utf-8"))
    pics = data.get("pics", [])
    result = []

    for item in pics:
        if isinstance(item, str):
            filename = item
        else:
            filename = item.get("file", "unknown")
        result.append({
            "file": filename,
            "guessed_role": guess_role(filename)
        })

    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
