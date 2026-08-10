# B2 Style-Profile Synthesis

B2 consumes two to six validated B1 v0.2 asset-analysis JSON documents and produces one traceable `style-profile.json`. B2 v0.1.1 tightens synthesis semantics without changing the v0.1 JSON contract. It is multi-reference, evidence-driven, and non-prescriptive.

## Contents

- Inputs and stages
- Trait construction and provenance
- Classification rules
- Visual-dimension boundaries
- Overall identity and confidence
- Output audit and boundaries

## Inputs

- Require at least two B1 analyses with unique `asset_id` values and retained `source_ref`, `reference_kind`, evidence provenance, uncertainties, and confidence.
- Accept optional `user_group_context` only as `user_provided` context.
- Treat B1 JSON as the authoritative visual evidence. Do not open source images, rerun B1, call a VLM, infer from file names, or invent new image facts.
- Fail explicitly on malformed, duplicate, mixed-contract, or non-B1 input. Do not silently skip invalid sources.

## Stage 1: validate

Validate every input against the frozen B1 schema. Preserve each source identity and confidence. Reject style profiles, Composer plans, preview requests, and duplicate source IDs as B1 inputs.

## Stage 2: aggregate

Collect evidence separately for color, material, shape, rendering, lighting, decoration, and world/theme. Also retain B1 `style_candidates`, `content_specific_traits`, uncertainties, evidence source labels, confidence, and `reference_kind`.

Aggregation builds evidence pools only. Do not assign classifications yet.

## Stage 3: normalize

Merge wording variants only when they describe the same complete visual phenomenon, such as "cold blue gray" and "desaturated cool gray-blue." Retain original B1 provenance after normalization.

Do not merge traits merely because their wording is adjacent. "Gold" and "warm yellow highlight," or "semi-realistic" and "flat cartoon," remain distinct without stronger evidence. Normalization reduces phrasing variance; it does not reinterpret B1 or conceal differences through broader wording.

## Trait construction

Keep every trait atomic, clear, and comparable. A trait should express one visual phenomenon whose complete meaning can be checked against every cited B1.

Do not bind independently testable facts into one compound trait. Split "bright ambient lighting with localized emissive fire and strong warm/cool contrast" into separate lighting and color traits. Classify each resulting trait from its own evidence.

## Full-trait provenance

Every `supporting_references` entry must support the complete core meaning of the trait, not merely one word or clause. For each trait, ask:

> Does every supporting reference support the full trait?

If the answer is no, do one or more of the following:

- Remove the partial-support reference.
- Split the trait into atomic traits.
- Downgrade the classification when the remaining evidence is insufficient.

Keep `supporting_references` and `contradicting_references` as B1 `asset_id` values present in `source_analyses`. User group context may alter transparent weighting, but may not delete source evidence, overwrite B1 facts, or masquerade as observation.

## Stage 4: classify

Never classify by frequency alone. Consider independent support, full-trait provenance, reference-kind diversity, source confidence, content specificity, consistency, counterevidence, and explicit user group context. Repeated near-duplicate references do not establish diverse support.

### Stable

Classify a trait as `stable` only when all of the following hold:

- Multiple independent B1 analyses support the full trait.
- Support is consistent rather than repeated wording from one image or content cluster.
- Reference evidence preferably spans at least two distinct `reference_kind` values.
- The trait is not content-specific.
- No equal or stronger counterevidence remains.
- The wording does not overgeneralize or hide material differences among references.

Several similar character-art references alone normally justify `secondary`, not `stable`, even when the trait is frequent. Stable means cross-reference evidence is genuinely consistent; it does not mean that a broad phrase can make different evidence sound similar.

### Secondary

Classify a trait as `secondary` when it has repeated full-trait support but weaker coverage, diversity, consistency, or confidence than `stable`.

Require at least two supporting references by default. With exactly one supporting reference, classify the trait as `local` or `uncertain` unless explicit `user_group_context` establishes that the source is organizationally representative. For that narrow exception:

- Explain the representativeness and weighting in `evidence_summary`.
- Keep the wording and confidence cautious.
- Preserve that the visual evidence still comes from one B1.

### Local

Prefer `local` for traits supported by one reference or tied to B1 `content_specific_traits`, one character, scene, UI, asset class, event, composition, or specific decoration.

Visual prominence does not promote local content to an overall style trait. For example, "red cape" remains local. If red accents recur independently as a cape, banner, rune, and UI accent, B2 may separately normalize the broader recurring evidence as a red-accent tendency; the cape itself remains local.

### Conflicting

Classify reliable, materially incompatible evidence as `conflicting`. Do not create a broad umbrella trait merely to make the sources agree. In particular, do not resolve "semi-realistic" versus "flat cartoon" as "stylized rendering" unless explicit evidence supports that complete higher-level trait.

Retain the alternatives, their supporting references, relevant contradicting references, and a precise conflict description. See `quality/cross-reference-conflicts.md`.

### Uncertain

Use `uncertain` instead of forcing another class when classification is unreliable. Prefer it when:

- Only one low-confidence occurrence exists.
- Normalization is uncertain.
- Trait wording remains too broad.
- References support only parts of a proposed trait.
- The relationship between user context and image evidence is unclear.
- It is unclear whether the feature is style or specific content.

## Empty classification groups

Every classification array, including each dimension's `stable` array, may be empty. Do not manufacture an abstract stable trait for completeness. An empty evidence-backed group is more accurate than a filled group with the wrong dimension or unsupported abstraction.

## Visual-dimension boundaries

Assign each atomic trait to the dimension that directly owns its visual meaning.

### Color

Use for palette, saturation, temperature, value, contrast, and accent relationships.

### Material

Use for tangible materials and surface treatment, such as metal, stone, fabric, wood, crystal, glass, leather, rough, polished, matte, or glossy. Exclude readability, layer separation, composition, lighting phenomena, and non-solid effects such as fire, smoke, fog, glow, or particles.

### Shape

Use for shape and silhouette language, such as sharp or rounded, slender or heavy, symmetric or asymmetric, geometric or organic, regular or broken, and simple or complex silhouettes. Exclude central composition, layout position, and foreground/background arrangement.

### Rendering

Use for image-making treatment, such as anime, semi-realistic, painterly, cel-shaded, 3D-like, linework, texture density, and surface-detail treatment.

### Lighting

Use for illumination and atmospheric light behavior, such as bright or dark exposure, ambient light, rim light, emissive light, bloom, fog-mediated lighting, highlights, and shadows.

### Decoration

Use for motifs and ornament systems, such as ornament density, carved patterns, gothic motifs, runes, and mechanical decoration. Do not treat one specific prop or isolated ornament as a cross-reference decorative system.

### World visual

Use for setting-level visual cues, such as medieval fantasy, eastern fantasy, science fiction, steampunk, military language, era cues, and technology/magic relationships.

## Overall identity and summary

Build `overall_visual_identity` primarily from supported `stable` and `secondary` traits. Describe the visual system, not the contents of the source images. Exclude specific characters, castles, events, poses, or isolated objects unless the evidence establishes a broader cross-reference visual rule.

If conflicts prevent one coherent identity, state the instability directly. Use `cross_dimension_summary` only for supported relationships among dimensions; do not convert those relationships into UI, asset-usage, prompt, or implementation advice.

## Confidence

Set `overall_confidence` from source confidence, reference diversity, consistency, stable-trait coverage, unresolved conflicts, and uncertainty. Do not use a simple average, and do not raise confidence merely because near-identical references repeat.

## Output audit

Before emitting the profile, verify:

1. Every cited reference supports the full atomic trait.
2. Every single-reference trait is `local` or `uncertain` unless the documented context exception applies.
3. No stable trait exists only to fill a dimension.
4. No material trait is really readability, composition, lighting, or a non-solid effect.
5. No shape trait is really composition or layout.
6. No conflict was hidden by broader wording.
7. Overall identity summarizes supported visual rules rather than source-image content.

## Output boundaries

- Describe, compare, normalize, and classify visual evidence.
- Do not make UI design decisions or recommend buttons, panels, pages, layouts, asset usage, prompts, providers, or engine implementation.
- Do not include source-image bytes, Provider/API parameters, Laya/FairyGUI node types, or Composer fields.
- Preserve unresolved conflicts and uncertainty instead of hiding or forcibly resolving them.
