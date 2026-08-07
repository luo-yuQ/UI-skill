# Supported Scope v1

## Positioning
This v1 is an MVP for **automatic game UI composition from assets**.

It is intentionally narrow:
- prioritize practical value
- reduce developer input
- focus on core page closure
- output implementation-friendly structure

## Best-fit scenarios
- WeChat mini-games
- HTML5 mini-games
- lightweight casual games
- runner / lane-switch / click-switch / merge-lite / rhythm-lite patterns
- projects without a finished design mockup
- workflows that need AI to infer roles and page structure from assets

## Core supported pages
Priority P0:
- Home
- Gameplay HUD
- Result

Priority P1:
- Mode Select
- Level Select

## Output priorities
1. Structured page plan
2. Asset mapping
3. Engine-neutral UI spec
4. HTML/CSS prototype guidance
5. Light motion guidance

## Explicit non-goals for v1
- full-scale RPG / MMO / SLG UI systems
- deep engine-native exporter pipelines
- premium final art polish replacement
- large branded cross-platform design systems

## Success criteria for v1
A v1 run is successful when it can:
- infer a compact page set
- map most assets into sensible roles
- keep gameplay safe zones clear
- generate an implementation-friendly result bundle
- minimize developer back-and-forth
