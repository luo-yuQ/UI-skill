---
name: game-ui-auto-composer-skill
description: Synthesize one new engine-neutral game UI design intent from an immutable authoritative A layout-reference final analysis, an immutable authoritative B2 style profile, and an ordinary-language user requirement. Use when the caller supplies schemas/ui-compose-input.schema.json v2.1 and needs a strictly requirement-preserving, traceable ui-compose-plan v2.1.
---

# Game UI Auto Composer v2.1

Create a new UI design intent. The user defines the target; A is layout evidence; B is classified style evidence. Composer does not reconstruct a reference and does not make examples into requirements.

## Required resources

Treat these files as authoritative:

- `schemas/ui-compose-input.schema.json` — v2.1 input contract referencing the real A/B schemas.
- `schemas/ui-compose-plan.schema.json` — v2.1 output contract.
- `references/input-schema.md` — input authority and immutability.
- `references/output-schema.md` — output responsibilities.
- `references/workflow.md` — the strict 18-step workflow.
- `references/examples/example-ui-compose-input.json` — complete fixture with unchanged A/B.
- `references/examples/example-ui-compose-plan.json` — schema-shape and regression fixture only.

Examples demonstrate schema shape only. Never inherit their numbers, page semantics, component IDs, hierarchy, actions, or copy into another run.

## Hard boundary

Do not inspect raw images, reopen A/B source art, redo multimodal analysis, generate images or prompts, implement engine code, cut assets, or run Preview Adapter/GPT Image behavior as part of Composer core.

Never:

- change an explicit user count;
- replace or rename the requested page or business type;
- invent an A `source_id` or B `trait_id`;
- mutate, normalize, summarize in place, or reinterpret upstream A/B JSON or confidence;
- promote a local B trait to global scope without an explicit matching user requirement;
- let `generation_constraints` introduce a new requirement;
- treat examples as target content;
- rename user business semantics merely to make a design feel more complete.

## Input contract and immutability

Accept exactly one JSON object conforming to `schemas/ui-compose-input.schema.json`:

```text
schema_version: 2.1
request
layout_reference_analysis
style_profile
```

Require non-whitespace `request.user_requirement` plus the complete A final object and complete B2 object. Reject legacy `pics`, top-level `assets`, invalid schemas, and unexpected fields.

A and B are immutable evidence. When original upstream files are available, validate JSON deep equality before composing:

```powershell
python scripts/validate_input.py <input.json> `
  --layout-source <original-a.json> `
  --style-source <original-b2.json>
```

JSON numeric equivalents such as `0` and `0.0` compare equal. Every mismatch reports its exact JSON path and stops composition. Never edit A/B to make validation pass.

## Authority and conflict priority

Apply this priority without exception:

```text
Explicit User Requirement
> Derived User Intent
> A Layout Reference
> B Style Evidence
> Composer Assumptions
```

Explicit user authority includes page/business semantics, content, names, counts, grid dimensions, required information, element positions, actions, layout changes, and visual changes. Derived intent may fill only low-risk gaps and may not override an explicit fact.

A governs only portable layout evidence: regions, hierarchy, adjacency, containment, proportions, grouping, repetition, alignment, and layout focal order. B governs only classified visual evidence. Assumptions are last and cannot alter user facts.

## Hard-requirement ledger

Before designing, extract explicit facts into `project_context.hard_requirements`:

- `page_semantic`
- `explicit_counts`
- `grid_requirements`
- `required_elements`
- `must_include`
- `must_not_include`

Every evidence field must be an exact substring of `user_requirement`. Counts and grid dimensions must use target component IDs that later exist. This ledger is the preservation baseline, not a summary that may be reinterpreted.

Maintain an internal facts ledger as well:

```text
USER_FACTS
LAYOUT_FACTS
STYLE_FACTS
DERIVED_DECISIONS
```

Never allow A, B, or an example to overwrite `USER_FACTS`.

## A traceability

Record every material A decision in `reference_application.layout` as adopted, adapted, ignored, or rejected.

For every `source_ids[]` entry:

1. select the allowed ID set by `source_kind`;
2. verify the ID exists in the input A object;
3. fail output validation with `UNKNOWN_A_SOURCE_ID` if it does not.

Do not accept plausible-looking IDs. Do not use a source ID from the wrong A entity kind.

## B classification and scope

Record each material B decision with the original `trait_id`, dimension, classification, disposition, target scope, application, promotion flag/evidence, and rationale. The declared dimension/classification must match B exactly.

- `stable`: adopt when relevant and unopposed.
- `secondary`: adopt only when relevant and scoped; never auto-promote.
- `local`: ignore unless semantically useful. If adopted without explicit promotion, scope it to exactly one matching existing component.
- `conflicting`: reject or leave unresolved; never silently choose.
- `uncertain`: ignore or warn; never turn it into a hard fact.
- `promoted_by_user_requirement: true`: allowed only when `promotion_evidence` is an exact user-requirement substring.

Every visual directive trait must exist in B, have a recorded adopted decision, and respect the decision scope.

## Semantic preservation

The final `pages[].page_type` must equal the hard ledger page semantic. Business vocabulary and actions absent from the user requirement must not appear as target content. A source semantics, examples, and “design completion” cannot replace a shop with another business flow or invent an unrequested primary action.

A semantic drift check covers `design_summary`, `pages`, `component_tree`, `interactions`, and `generation_constraints`. On drift, fail instead of returning a polished but different page.

## Component, layout, and interaction rules

Build a new target component tree; do not mechanically rename A. Preserve explicit counts in `component_tree.repeat.count`. For grids, record `columns` and `rows`, and require:

```text
count == columns * rows
```

Use normalized engine-neutral layout rules. Required position facts must resolve to compatible anchors. Required actions must have a matching trigger component and interaction action. Add only necessary behavior and allow empty navigation for a single page.

## Generation constraints

`generation_constraints` is derived only from:

```text
explicit user requirements
+ final component_tree
+ final layout_rules
+ approved A/B decisions
```

It is a constraint summary, not a second design stage and not a GPT Image prompt. It must never introduce a new component, count, semantic, action, style fact, or prohibition.

Record exact counts, grid specs, key zones, focal hierarchy, separability, overlap, readability, clean boundaries, cutout suitability, and reference-fidelity limits.

## Strict 18-step workflow

1. Validate immutable A/B inputs.
2. Parse explicit user requirements.
3. Build the hard-requirement ledger.
4. Identify user target semantics.
5. Read A as layout evidence.
6. Read B as style evidence.
7. Select applicable A references.
8. Select B traits according to classification and scope.
9. Create the new component tree.
10. Adapt layout.
11. Derive target visual direction.
12. Create only required interactions.
13. Derive generation constraints.
14. Run requirement preservation checks.
15. Run semantic drift checks.
16. Run A/B traceability validation.
17. Run cross-section consistency checks.
18. Return exactly one valid JSON object.

## Cross-section consistency

These sections must agree:

```text
design_summary
component_tree
layout_rules
interactions
generation_constraints
reference_application
```

Check at least product/item count, category count, grid rows/columns, page semantic, required positions, and bottom action. A fact cannot be 6 in one section and 8 in another. A refresh action cannot become a different business action.

Run strict cross-validation:

```powershell
python scripts/validate_plan.py <plan.json> --input <input.json>
```

Stop on any schema, requirement, semantic, traceability, local-scope, or consistency error.

## Output contract

Return exactly one `schemas/ui-compose-plan.schema.json` v2.1 object with the existing v2 top-level structure:

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

Do not emit v1 `asset_usages` or `missing_assets`. Do not emit downstream adapter output.

## Final rule

Obey the user first, preserve explicit facts exactly, use only real A/B evidence, and fail validation rather than silently designing a different UI.
