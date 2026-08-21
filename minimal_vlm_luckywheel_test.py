import json
import sys
from pathlib import Path

scripts = Path(
    r"D:\Third_Test_1\UI-skill\game-ui-asset-analyzer\scripts"
)

sys.path.insert(0, str(scripts))

from vlm_client import (
    VLMClientConfig,
    create_configured_vlm_client,
)


image_path = Path(
    r"D:\Third_Test_1\UI-skill\runs\20260819_luckywheel_source_root_new_08\nodes\lucky_wheel.child_008\analysis-image.png"
)


client = create_configured_vlm_client(
    VLMClientConfig.from_env()
)


system_prompt = """
You are analyzing a game UI screenshot for asset production.

Do not use any predefined UI taxonomy.

Do not classify into:
- structural_group
- repeated_group
- component_instance
- asset

Do not assume any existing component tree.

Look at the image as a game UI designer and asset producer.

Answer:

1. What are the visually meaningful parts of this UI?
2. Which parts appear to be repeated instances of the same type?
3. Which parts look like reusable game UI assets?
4. What parts should be ignored because they are background, decoration, or low reuse value?

Return JSON only.
"""


user_prompt = """
Analyze this game UI component.

Do not create pixel crops.
Do not output bounding boxes.
Do not classify into:
- structural_group
- repeated_group
- component_instance
- asset

Instead, describe the semantic ownership structure of this component.

Focus on:

1. What is the main component?
2. What meaningful child components does it own?
3. Which children are repeated collections?
4. Which visual elements are final reusable assets?
5. Which elements are only decoration and should remain attached to a parent component?

Important:
- Do not flatten repeated children into individual assets.
- Do not split one reusable component into arbitrary visual regions.
- Prefer ownership relationships over spatial regions.

Return JSON only.

Expected format:

{
  "component": "",
  "children": [
    {
      "name": "",
      "type": "component|collection|asset|decoration",
      "description": "",
      "children": []
    }
  ],
  "repeated_collections": [
    {
      "name": "",
      "count": 0,
      "description": ""
    }
  ],
  "reusable_assets": [],
  "decorations": []
}
"""


result = client.infer_json(
    image_path=image_path,
    system_prompt=system_prompt,
    user_prompt=user_prompt,
)


print(json.dumps(
    result,
    ensure_ascii=False,
    indent=2
))
