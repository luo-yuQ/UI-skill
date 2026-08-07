# B1 Single-Reference Analysis

B1 analyzes exactly one user-owned game visual/art reference and produces one structured, image-scoped description. Each execution is stateless and independent, so multiple references may be processed separately and in parallel.

## Inputs

- One image from the user's own game.
- Optional user description.
- Optional caller/user-intended use and other caller-provided metadata.

Treat user text as `user_provided`; do not present it as directly observed image evidence. `reference_kind` answers what the image is and may be visually analyzed. `user_intended_use` answers how the caller wants to use it and must never be inferred from `reference_kind` or visible content.

## Required analysis

1. Identify the complete image with `reference_kind` and the main subject with `asset_category`.
2. Describe visible content in `visual_description` without recommendations.
3. Describe color, material, shape, rendering, lighting, and decoration with concise, comparable structures.
4. Record image-supported theme cues in `world_visual_context` as inference where appropriate.
5. Record color, material, shape, rendering, lighting, decoration, and world/theme `style_candidates` for later B2 verification, never as confirmed global traits.
6. Isolate scene-, subject-, object-, pose-, and composition-specific details in `content_specific_traits`.
7. Separate `observed`, `inferred`, and `user_provided` statements.
8. Record unsupported questions in `uncertainties` and assign evidence-based confidence.

## Output discipline

- Describe the current image only.
- Prefer stable categories and short evidence statements over decorative prose.
- Do not invent precise colors, materials, light directions, identities, or world facts when evidence is insufficient.
- Do not provide UI, layout, asset-generation, prompt, provider, or engine recommendations.
- Keep every string field descriptive, including `evidence`, `inferred` statements, notes, candidates, and uncertainties. Do not write "suitable for," "recommended as," "can be used as," or equivalent purpose judgments anywhere in the B1 result.
- Copy `user_intended_use` only when explicitly supplied. Otherwise omit it or use `null`.
- Do not infer or generate `intended_role` or `role_candidates`; preserve them only in caller-provided or legacy data.
- Do not generate legacy `layout_behavior` or `laya_new_ui` fields in normal B1 output. They remain optional in the schema only for old-data compatibility and may belong to a separate future engineering-asset stage.
- Keep composition, camera angle, subject placement, perspective layout, page layout, element positions, and current-page spatial organizations such as "upper illustration plus lower panel" out of `style_candidates`. They may remain in factual `visual_description` or, when image-specific, `content_specific_traits`.
- Restrict Material Language to tangible objects and surface appearance. Fire, smoke, fog, glow, bloom, emissive/magical light, particles, sparks, and other non-tangible effects are strictly forbidden as materials; record them as lighting, decoration/effects, atmosphere, or visible content instead.

B1 does not compare references or decide stable, secondary, local, or conflicting traits across images. Those decisions belong to future B2 synthesis.
