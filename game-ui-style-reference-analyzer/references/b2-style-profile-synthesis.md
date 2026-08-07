# B2 Style-Profile Synthesis

B2 consumes two to six validated B1 v0.2 asset-analysis JSON documents and produces one traceable `style-profile.json`. It is multi-reference, evidence-driven, and non-prescriptive.

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

Merge wording variants only when they plausibly describe the same visual phenomenon, such as “cold blue gray” and “desaturated cool gray-blue.” Retain original B1 provenance after normalization.

Do not merge traits merely because their wording is adjacent. “Gold” and “warm yellow highlight,” or “semi-realistic” and “flat cartoon,” remain distinct without stronger evidence. Normalization reduces phrasing variance; it does not reinterpret B1.

## Stage 4: classify

Classify normalized traits as:

- `stable`: strong, consistent support across multiple independent references, preferably with reference-kind diversity and reliable B1 confidence.
- `secondary`: repeated support with weaker coverage, diversity, consistency, or confidence than stable.
- `local`: tied mainly to one image, subject, scene, asset class, or content cluster. Start B1 `content_specific_traits` here unless independent recurrence supports promotion.
- `conflicting`: reliable sources provide materially incompatible evidence that cannot be merged responsibly.
- `uncertain`: evidence is sparse, ambiguous, low-confidence, or not reliably normalizable.

Never classify by frequency alone. Consider frequency, reference diversity, source confidence, content specificity, cross-reference consistency, conflict level, and explicit user group context. Repeated near-duplicate references do not equal diverse support.

## Provenance

Every classified trait must retain `supporting_references` as B1 `asset_id` values. Record `contradicting_references` when applicable. All referenced IDs must exist in `source_analyses`.

Never emit an untraceable style conclusion. User group context may alter transparent weighting but may not delete source evidence, overwrite B1 facts, or masquerade as observation.

## Overall identity and summary

Build `overall_visual_identity` primarily from stable and secondary evidence across dimensions. If conflicts prevent one coherent identity, say so directly instead of forcing polished prose.

Use `cross_dimension_summary` only to describe relationships among supported dimensions. Do not turn those relationships into UI, asset-usage, prompt, or implementation advice.

## Confidence

Set `overall_confidence` from source confidence, reference diversity, consistency, stable-trait coverage, unresolved conflicts, and uncertainty. Do not use a simple average, and do not raise confidence merely because near-identical references repeat.

## Output boundaries

- Describe, compare, normalize, and classify visual evidence.
- Do not make UI design decisions or recommend buttons, panels, pages, layouts, asset usage, prompts, providers, or engine implementation.
- Do not include source-image bytes, Provider/API parameters, Laya/FairyGUI node types, or Composer fields.
- Preserve unresolved conflicts and uncertainty instead of hiding or forcibly resolving them.
