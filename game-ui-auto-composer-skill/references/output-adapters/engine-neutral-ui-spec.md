# Engine-Neutral UI Spec

## Purpose
Represent UI in a way that is not tied to one engine.

## Required sections
- page list
- asset catalog
- page component trees
- anchors and layout constraints
- interaction states
- motion notes
- implementation notes

## Example page structure
```yaml
page_id: home
goal: introduce game and expose primary start action
components:
  - id: bg_home
    type: background
    asset_ref: bg_home
    anchor: full-background
  - id: title_group
    type: title-group
    anchor: top-center
  - id: start_button
    type: button
    asset_ref: btn_primary
    anchor: bottom-center
    states: [idle, pressed, disabled]
```

## Why this matters
This structure is easier to adapt to:
- Unity
- Cocos
- HTML5/H5
- Godot
- custom engines
