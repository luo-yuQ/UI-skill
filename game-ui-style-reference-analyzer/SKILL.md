---
name: game-ui-style-reference-analyzer
description: "Analyze user-owned game visual and art references through two evidence-driven stages: B1 describes exactly one image as a structured visual analysis, and B2 synthesizes two to six validated B1 results into a traceable overall style profile. Use for single-reference B1 analysis or multi-reference B2 visual/style synthesis; do not use for UI design recommendations, Composer page planning, image generation, provider calls, or engine implementation."
---

# Game UI Style Reference Analyzer

Use two independent stages:

```text
B image 01 -> B1
B image 02 -> B1
B image 03 -> B1
             |
             v
             B2 -> style-profile.json -> future Composer
```

The future Composer consumes the result but is not an internal capability of this Skill.

## B1: single-reference analysis

Analyze exactly one user-owned game visual/art reference at a time. Keep each execution stateless, independently executable, and parallelizable.

1. Inspect one image and optional caller/user metadata.
2. Describe visible identity, facts, and the six visual-language dimensions.
3. Record image-scoped world/theme cues, style candidates, content-specific traits, provenance, uncertainties, and confidence.
4. Emit one object conforming to `schemas/asset-analysis.schema.json` and validate it with `scripts/validate_asset_analysis.py`.

Read `references/b1-single-reference-analysis.md`, the relevant visual taxonomy files, and `references/quality/evidence-vs-inference.md` before producing B1 output.

B1 describes only. Do not compare images, declare the overall game style, infer caller intent, generate legacy engineering fields, or provide design/usage recommendations.

## B2: multi-reference synthesis

Consume two to six valid B1 v0.2 JSON results. Do not open the source images, rerun B1, call a VLM, or infer visual facts from file names.

1. Validate all B1 inputs and require unique `asset_id` values.
2. Aggregate evidence by color, material, shape, rendering, lighting, decoration, and world/theme.
3. Normalize wording variants conservatively without rewriting B1 meaning or hiding differences behind broader wording.
4. Keep traits atomic. Require every cited B1 to support the full trait, then classify with the strict thresholds in `references/b2-style-profile-synthesis.md`.
5. Reserve `stable` for consistent multi-reference evidence with no equal or stronger counterevidence; prefer reference-kind diversity. Require two supporting references for `secondary` by default. Treat single-reference or content-specific traits as `local` or `uncertain` unless explicit user group context documents the narrow representativeness exception.
6. Preserve counterevidence, conflicts, uncertainties, and source confidence. Keep incompatible evidence conflicting instead of inventing a broad unifying trait.
7. Allow any dimension's `stable` array to remain empty. Keep material and shape traits within their visual-dimension boundaries.
8. Build `overall_visual_identity`, `cross_dimension_summary`, and `overall_confidence` from traceable stable and secondary evidence, not specific source-image content.
9. Ask "Does every supporting reference support the full trait?" for every trait. Remove a reference, split the trait, or downgrade it when the answer is no.
10. Emit one object conforming to `schemas/style-profile.schema.json` and validate it with `scripts/validate_style_profile.py`.

Read `references/b2-style-profile-synthesis.md` and `references/quality/cross-reference-conflicts.md` before producing B2 output.

B2 describes, compares, normalizes, and classifies. Do not read source images, invent new image facts, hide conflicts, or provide UI, asset-usage, prompt, provider, Composer, or engine recommendations.

## Shared boundaries

- Keep every field descriptive and evidence-based, including observed/inferred statements, summaries, notes, conflicts, and uncertainties.
- Never write suitability or usage judgments such as "suitable for," "recommended as," or "can be used as."
- Do not include source-image bytes, Provider/API parameters, Laya/FairyGUI node types, preview requests, or Composer plans.
- Prioritize accuracy, provenance, comparability, concision, and explicit uncertainty over polished prose.
