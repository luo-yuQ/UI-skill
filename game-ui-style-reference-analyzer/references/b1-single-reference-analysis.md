# B1 Single-Reference Analysis

B1 analyzes exactly one user-owned game visual/art reference and produces one structured, image-scoped description. Each execution is stateless and independent, so multiple references may be processed separately and in parallel.

## Inputs

- One image from the user's own game.
- Optional user description.
- Optional user-intended use.

Treat user text as `user_provided`; do not present it as directly observed image evidence.

## Required analysis

1. Identify the complete image with `reference_kind` and the main subject with `asset_category`.
2. Describe visible content in `visual_description` without recommendations.
3. Describe color, material, shape, rendering, lighting, and decoration with concise, comparable structures.
4. Record image-supported theme cues in `world_visual_context` as inference where appropriate.
5. Record `style_candidates` for later B2 verification, never as confirmed global traits.
6. Isolate scene-, subject-, object-, pose-, and composition-specific details in `content_specific_traits`.
7. Separate `observed`, `inferred`, and `user_provided` statements.
8. Record unsupported questions in `uncertainties` and assign evidence-based confidence.

## Output discipline

- Describe the current image only.
- Prefer stable categories and short evidence statements over decorative prose.
- Do not invent precise colors, materials, light directions, identities, or world facts when evidence is insufficient.
- Do not provide UI, layout, asset-generation, prompt, provider, or engine recommendations.
- Preserve legacy `layout_behavior` and `laya_new_ui` fields only for compatibility; they are not the B1 main path.

B1 does not compare references or decide stable, secondary, local, or conflicting traits across images. Those decisions belong to future B2 synthesis.
