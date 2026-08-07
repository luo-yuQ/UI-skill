#!/usr/bin/env python3
import json
import sys
from pathlib import Path

DEFAULT_PAGES = ["home", "gameplay-hud", "result"]

def main():
    if len(sys.argv) != 2:
        print("Usage: python build_page_plan.py <input.json>")
        raise SystemExit(1)

    path = Path(sys.argv[1])
    data = json.loads(path.read_text(encoding="utf-8"))
    gd = data.get("game_description", {})
    known = gd.get("known_pages") or []

    pages = list(DEFAULT_PAGES)
    for p in known:
        if p not in pages:
            pages.append(p)

    print(json.dumps({"page_list": pages}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
