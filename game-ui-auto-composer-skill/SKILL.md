---
name: game-ui-auto-composer-skill
description: Synthesize one new engine-neutral game UI design intent from an authoritative A layout-reference final analysis, an authoritative B2 style profile, and an ordinary-language user requirement. Use when the caller supplies a JSON object conforming to schemas/ui-compose-input.schema.json and needs a traceable ui-compose-plan v2 covering reference application, target visual direction, new page/component hierarchy, layout intent, interactions, navigation, generation constraints, assumptions, and warnings.
---

# Game UI Auto Composer v2

Consume layout structure as reference, visual style as evidence, and the user's target requirement as authority. Design a new UI instead of reconstructing either reference.

## Required resources

Treat these files as authoritative:

- `schemas/ui-compose-input.schema.json` — Composer v2 input contract; directly references the upstream A and B schemas.
- `schemas/ui-compose-plan.schema.json` — Composer v2 output contract.
- `references/input-schema.md` — authority and input usage rules.
- `references/output-schema.md` — output responsibilities.
- `references/workflow.md` — ordered synthesis workflow and stop conditions.
- `references/examples/example-ui-compose-input.json` — complete valid input using real A final and B2 examples.
- `references/examples/example-ui-compose-plan.json` — complete valid new-design output.

## Hard boundary

Do not:

- accept or inspect raw images;
- reopen or reinterpret A's screenshot or B's source art;
- use multimodal analysis to override A or B;
- generate images, GPT Image prompts, HTML/CSS, FairyGUI XML, implementation code, or engine-specific fields;
- cut assets or verify that source images exist;
- copy reference business content, text, characters, rewards, events, or page theme into the target by default;
- treat A or B as a target asset library.

Do:

- interpret the user's target page and business goal;
- select, adapt, ignore, or reject A layout principles;
- select B2 traits according to their classification and target relevance;
- design a new page hierarchy, component tree, layout intent, visual direction, interaction intent, and navigation intent;
- emit structured constraints for a future image-generation stage without compiling a prompt;
- trace which decisions came from the user, A, or B and record adaptations, assumptions, conflicts, and risks.

## Input contract

Accept exactly one JSON object conforming to `schemas/ui-compose-input.schema.json`:

```text
schema_version: 2.0
request
layout_reference_analysis
style_profile
```

Require non-whitespace `request.user_requirement`. Allow optional project, game, page, orientation, resolution, constraint, and visual-preference context without requiring the user to write a professional UI specification.

Require the complete A final object and complete B2 object. Resolve their `$ref` contracts to the adjacent upstream Skills; never replace either with a private summary schema.

Reject and stop on:

- invalid Composer, A, or B schema;
- missing or empty `user_requirement`;
- legacy `pics` anywhere;
- legacy top-level `assets`;
- unexpected fields under strict objects.

Use `python scripts/validate_input.py <input.json>`. Do not partially compose or automatically repair invalid A/B objects.

## Authority model

### User authority

Treat the user requirement as authoritative for what to design: business purpose, target page type, required content and text semantics, component counts, specified interactions, requested layout changes, requested visual changes, orientation, and resolution.

### A authority

Treat `layout_reference_analysis` as evidence for how the reference UI is organized: regions, hierarchy, spatial relationships, proportions, alignment, grouping, repetition, and layout-related focal structure. It is not target business content.

Prefer portable relations such as left of, below, inside, centered within, aligned with, dominant region, repeated horizontally, or repeated as a grid. Recalculate them for target content, count, orientation, resolution, and page meaning. Do not copy reference pixels.

Record every material A decision in `reference_application.layout` as `adopted`, `adapted`, `ignored`, or `rejected`, retaining stable source IDs.

### B authority

Treat `style_profile` as evidence for the target visual language: color, material, shape, rendering, lighting, decoration, surface treatment, and world visual cues. It is not layout authority or target content.

Record every material B decision in `reference_application.style`, retaining `trait_id`, dimension, classification, disposition, scope, application, and rationale.

## Conflict priority

Apply these rules in order:

1. Let explicit user requirements override A counts, content, page semantics, and layout changes. Preserve useful A organization only after adapting it.
2. Let explicit user visual requirements override B and record the deviation.
3. Use reliable B evidence when the user has not overridden it.
4. Let A govern formal layout relationships. Treat composition-like B traits only as weak visual tendencies that cannot override A.
5. Never use assumptions to override the user.

Inherit structure, not literal content or count.

## B2 classification handling

- `stable`: adopt by default when relevant and not contradicted by the user.
- `secondary`: adopt only when relevant to the target page; never promote automatically to a global rule.
- `local`: adopt only for a semantically matching target component or scope; never globalize it.
- `conflicting`: do not silently choose a side. Ignore it or expose the unresolved choice in warnings/assumptions when material.
- `uncertain`: do not convert it into a hard fact or target narrative. Ignore it or record a low-confidence assumption/warning.

## Prevent reference semantic leakage

Reference visual semantics are not user requirements.

Do not add a castle, battle, reward, character, original title, original copy, currency shape, or reference-specific red panel merely because it occurs in A or B. Transfer only the applicable layout or visual-language abstraction. Respect A `excluded_content` and B provenance/classification.

## Design synthesis workflow

1. Validate the complete v2 input and stop on failure.
2. Interpret the user's target before selecting reference material.
3. Extract applicable layout principles from A.
4. Select applicable visual traits from B by classification and semantic scope.
5. Resolve user/A/B conflicts and prevent semantic leakage.
6. Design a new page scope and component hierarchy from the target requirement.
7. Adapt A relationships to target content, counts, orientation, and resolution.
8. Derive a target-specific visual direction from selected B traits plus explicit user visual choices.
9. Define only necessary engine-neutral interactions and navigation.
10. Define structured generation constraints for downstream visual production.
11. Record reference application, assumptions, and warnings.
12. Validate and return exactly one `ui-compose-plan` v2 JSON object.

Do not start by copying A and filling it with user content.

## Output contract

Return exactly one object conforming to `schemas/ui-compose-plan.schema.json` with these fields:

- `schema_version`
- `project_context`
- `design_summary`
- `reference_application`
- `visual_direction`
- `pages`
- `component_tree`
- `layout_rules`
- `interactions`
- `navigation`
- `generation_constraints`
- `assumptions`
- `warnings`

Do not emit v1 `asset_usages` or `missing_assets`. A and B are reference evidence, not final target assets.

Build `component_tree` for the new target UI. Do not merely rename A IDs. Keep layout rules normalized, relational, safe-area-aware, content-adaptive, and engine-neutral.

Use `python scripts/validate_plan.py <plan.json>` when local execution is available.

Use `generation_constraints` to state must-include and must-not-include rules, exact counts, content zones, focal hierarchy, separability, overlap, readability, clean-boundary, cutout-friendly, and reference-fidelity constraints. Do not write a full image-generation prompt.

When only one page is requested, allow empty `navigation` and include only necessary interactions. Never add default home, result, gameplay, or other template pages without user justification.

## Validation failure

Return only a structured error and stop:

```json
{
  "status": "error",
  "error_code": "INPUT_VALIDATION_FAILED",
  "errors": [
    {
      "path": "$.style_profile.schema_version",
      "message": "Expected constant value '0.1'",
      "code": "CONST_MISMATCH"
    }
  ],
  "warnings": []
}
```

## Legacy resources

Treat `asset-analysis.schema.json`, `asset-analysis.example.json`, `assets/samples`, asset classifiers, old templates, engine-compatibility notes, and preview/prototype adapters as legacy or downstream material. They are not part of the Composer v2 core contract and must not override it.

## Final rule

Consume structure as reference, consume style as evidence, obey the user's target intent, and design a new UI rather than reconstructing either reference.
