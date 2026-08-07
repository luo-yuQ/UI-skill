#!/usr/bin/env python3
import json
import sys
from pathlib import Path

def main():
    if len(sys.argv) != 3:
        print("Usage: python generate_engine_neutral_spec.py <input.json> <output.json>")
        raise SystemExit(1)

    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    data = json.loads(in_path.read_text(encoding="utf-8"))
    gd = data.get("game_description", {})
    spec = {
        "game_profile": gd,
        "page_list": ["home", "gameplay-hud", "result"],
        "notes": [
            "This is a starter engine-neutral spec for v1.",
            "Expand component trees manually or with later automation."
        ]
    }
    out_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")

if __name__ == "__main__":
    main()
