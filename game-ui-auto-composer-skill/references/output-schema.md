# UI Compose Plan v2

## Authoritative contract

Return one JSON object conventionally named `ui-compose-plan.json` and conforming to `schemas/ui-compose-plan.schema.json`. `schema_version` is fixed to `2.0`.

## Required top-level fields

- `schema_version` — contract version.
- `project_context` — normalized user target, scope, resolution, and constraints.
- `design_summary` — what is being designed and the main synthesis decisions.
- `reference_application` — traceable A and B decisions.
- `visual_direction` — target-page interpretation of selected B traits plus user overrides.
- `pages` — only pages justified by the user requirement.
- `component_tree` — the new target UI hierarchy.
- `layout_rules` — normalized, relational, content-adaptive layout intent.
- `interactions` — necessary engine-neutral triggers and state changes.
- `navigation` — only justified page-to-page flow; may be empty for one page.
- `generation_constraints` — structured requirements for later visual generation.
- `assumptions` — low-risk decisions required to complete the design.
- `warnings` — conflicts, uncertainty, risky trait use, or major adaptation.

`asset_usages` and `missing_assets` are not v2 fields. A and B are evidence, not a final target asset library.

## Reference application

For A decisions, retain stable source IDs and record source kind, source meaning, `adopted`/`adapted`/`ignored`/`rejected`, target application, and rationale.

For B decisions, retain `trait_id`, dimension, classification, disposition, target scope, target application, and rationale. Do not copy the complete A or B object into the plan.

## Visual direction

Emit target-specific directives for supported dimensions such as color, material, shape, rendering, lighting, decoration, world visual cues, and surface treatment. Link directives to B `trait_id` values. An empty source list is allowed only for an explicit user override.

Do not invent unsupported visual facts. Do not turn local, conflicting, or uncertain evidence into global rules.

## New component and layout intent

Build `component_tree` from the user business requirement, applicable A structure, and conservative design completion. Use target IDs and target semantics; do not rename A IDs mechanically.

Use `layout_rules` for semantic anchors, normalized positions, relative dimensions, stack order, safe-area policy, spatial relationships, spacing, and content-dependent adaptation. Do not emit engine implementation fields.

## Generation constraints

Structure:

- must include / must not include;
- exact counts;
- key content zones;
- focal hierarchy;
- component separability;
- overlap restrictions;
- readability requirements;
- clean boundaries;
- cutout-friendly requirements;
- reference-fidelity boundaries.

This section is not a GPT Image prompt. Prompt compilation belongs to a later adapter.

## Successful response

Return only the valid JSON object. Do not append Markdown, prompts, implementation mappings, source code, or secondary artifacts.
