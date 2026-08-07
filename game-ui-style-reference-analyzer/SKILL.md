---
name: game-ui-style-reference-analyzer
description: Analyze exactly one user-owned game visual or art reference as a structured B1 result, including observable visual language, evidence sources, style candidates, content-specific traits, uncertainty, and confidence. Use for single-image B1 analysis; do not use for cross-image B2 synthesis, UI design recommendations, Composer page planning, image generation, or engine implementation.
---

# Game UI Style Reference Analyzer

Analyze exactly one user-owned game visual/art reference at a time. Treat each B1 execution as stateless and independent; analyze multiple B images through separate B1 executions, which may run in parallel. A future B2 process will consume those results, but B2 is not implemented here.

## B1 workflow

1. Inspect one image together with any optional user description and `user_intended_use`.
2. Identify what the complete reference is with `reference_kind`; keep it distinct from the main subject's `asset_category` and the user's intended use.
3. Describe only visible subject matter, composition, and appearance in `visual_description`.
4. Record the six comparable visual-language dimensions: color, material, shape, rendering, lighting, and decoration. Read the matching files under `references/visual-style-taxonomy/` before producing these fields.
5. Record image-scoped theme cues in `world_visual_context` without declaring the game's confirmed world setting.
6. Record traits worth later cross-image verification as `style_candidates`. Treat them as candidates, never as final overall-game style.
7. Put subject-, object-, scene-, pose-, and composition-specific details in `content_specific_traits` so later synthesis does not automatically globalize them.
8. Label claims as `observed`, `inferred`, or `user_provided` according to `references/quality/evidence-vs-inference.md`.
9. Put unsupported judgments in `uncertainties` instead of guessing. Assign evidence-based confidence values.
10. Emit one JSON object conforming to `schemas/asset-analysis.schema.json` and validate it with `scripts/validate_asset_analysis.py`.

## Boundaries

- Describe visual evidence and visual language; do not provide UI design recommendations.
- Do not infer the final overall game style from one image.
- Do not compare multiple images or perform B2 synthesis.
- Do not analyze other games' UI reference layouts.
- Do not perform Composer page planning, prompt compilation, image generation, provider calls, engine implementation, or actual nine-slice cutting.
- Preserve `layout_behavior` and `laya_new_ui` only as secondary compatibility fields. Do not let them drive the analysis.

Prioritize accuracy, evidence, comparability, concision, and completeness. Do not prioritize prose flair or design creativity.
