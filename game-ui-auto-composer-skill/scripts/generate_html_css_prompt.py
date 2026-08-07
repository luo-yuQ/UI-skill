#!/usr/bin/env python3
import json
import sys
from pathlib import Path

PROMPT_TEMPLATE = """Create a lightweight HTML/CSS prototype for the following game UI pages:
- Home
- Gameplay HUD
- Result

Constraints:
- keep gameplay center clear
- use relative layout
- large primary CTA
- minimize decorative clutter

Game profile:
{profile}
"""

def main():
    if len(sys.argv) != 2:
        print("Usage: python generate_html_css_prompt.py <input.json>")
        raise SystemExit(1)

    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    gd = data.get("game_description", {})
    print(PROMPT_TEMPLATE.format(profile=json.dumps(gd, ensure_ascii=False, indent=2)))

if __name__ == "__main__":
    main()
