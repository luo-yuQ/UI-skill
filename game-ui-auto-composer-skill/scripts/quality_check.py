#!/usr/bin/env python3
import json
import sys
from pathlib import Path

CHECKS = [
    "gameplay safety",
    "visual clarity",
    "style consistency",
    "clickability",
    "implementability",
    "motion cost",
    "output target match"
]

def main():
    if len(sys.argv) != 2:
        print("Usage: python quality_check.py <spec.json>")
        raise SystemExit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        raise SystemExit(1)

    _ = json.loads(path.read_text(encoding="utf-8"))
    print("Run the following manual checks:")
    for item in CHECKS:
        print(f"- {item}")

if __name__ == "__main__":
    main()
