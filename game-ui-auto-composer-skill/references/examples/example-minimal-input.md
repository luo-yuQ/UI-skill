# Example Minimal Input

```json
{
  "pics": ["bg_home.png", "btn_primary.png", "character_idle_01.png"],
  "game_description": {
    "title": "Swap",
    "genre": "casual lane-switch",
    "platform": "wechat mini-game",
    "orientation": "landscape",
    "gameplay": "tap to switch lanes and avoid obstacles"
  }
}
```

Expected behavior:
- infer Home + Gameplay HUD + Result
- classify the three assets
- avoid asking unnecessary questions
- produce a compact page plan
