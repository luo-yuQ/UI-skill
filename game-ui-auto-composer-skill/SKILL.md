---
name: game-ui-auto-composer-skill
description: Synthesize one new engine-neutral game UI candidate from an immutable A layout analysis, an immutable B2 style profile, and an ordinary-language user requirement. Use when the caller supplies ui-compose-input v2.1.1 and needs one requirement-preserving candidate ui-compose-plan v2.1.1 with explicit decision origins for deterministic Python validation.
---

# Game UI Auto Composer v2.1.1

Design one new UI candidate. The user defines business semantics and invariants; A supplies the transferable layout skeleton; B supplies optional classified style evidence. Deterministic Python code decides whether cited evidence and the resulting contract are valid.

```text
validated input
-> design synthesis
-> candidate ui-compose-plan
-> END
```

Do not self-loop, retry, repair, or repeatedly rewrite the candidate. Validation and future repair are separate stages.

## Required resources

- `schemas/ui-compose-input.schema.json` - v2.1.1 input.
- `schemas/ui-compose-plan.schema.json` - v2.1.1 candidate plan.
- `scripts/build_compose_input.py` - deterministic UTF-8 input construction and source-integrity checks.
- `scripts/evidence_registry.py` - immutable Pydantic A/B registry.
- `scripts/validate_input.py` - schema and upstream-integrity validation.
- `scripts/validate_plan.py` - schema, requirements, origin, registry membership, and consistency validation.
- `references/input-schema.md` and `references/output-schema.md` - contract details.
- `references/workflow.md` - one-pass workflow and validation handoff.

Treat `schemas/ui-compose-plan.schema.json` and `references/output-schema.md` as the runtime output-shape authorities. Do not inspect test fixtures or any complete example plan during normal composition.

## Deterministic input construction

Always construct `ui-compose-input.json` with the builder before validation or composition:

```powershell
python scripts/build_compose_input.py `
  --request <request.json> `
  --layout <layout-analysis.json> `
  --style <style-profile.json> `
  --output <ui-compose-input.json>
```

The builder reads every source as UTF-8 JSON, takes `user_requirement` directly from `request.json`, projects only request fields allowed by the current schema, writes with `ensure_ascii=False`, and verifies the written requirement plus embedded A/B by JSON value deep equality. Do not have an Agent retype or reconstruct the requirement. Do not assemble the JSON with PowerShell string concatenation, `echo`, redirection, `Set-Content`, or `Out-File`.

## Boundary

Composer core does not inspect raw images, reopen A/B source art, generate previews or prompts, implement engine code, cut assets, operate Preview Adapter/GPT Image/FairyGUI/Runtime, or modify a runner Skill.

Never:

- change explicit user counts, page type, business semantics, content, explicitly locked position, or action;
- mutate or normalize upstream A/B JSON or confidence;
- invent an A source ID or B trait ID;
- force every design decision to cite A or B;
- promote a local B trait globally without an explicit matching user requirement;
- let `generation_constraints` create new design requirements;
- turn `conflicting` or `uncertain` B evidence into hard facts;
- create a runtime, repair loop, retry loop, cache, orchestrator, or state machine.

## Authority and hard requirements

Apply layout authority in this order:

```text
Explicit business semantics and required actions
> Explicit counts and grid dimensions
> Explicitly immutable user positions
> A layout skeleton
> Ordinary user position preferences
> Low-risk Composer completion
```

B remains style evidence and does not compete for layout authority.

Extract explicit facts into `project_context.hard_requirements` before designing:

- page semantic;
- exact counts;
- grid rows and columns;
- required elements, with positions only when the user explicitly locks them;
- required information and actions;
- must-include and must-not-include constraints.

Evidence strings must be exact user-requirement substrings. Preserve these facts through pages, components, layout, interactions, and generation constraints.

## Split requirement authority

Split each user phrase before building the hard-requirement ledger:

- Record page/business semantics, component existence, exact counts, grids, required information, and required actions as hard facts.
- Record a position as hard only when the user marks it immutable with wording such as `must`, `fixed`, `strictly remain`, `cannot move`, or equivalent explicit lock language in another language.
- Treat ordinary wording such as "categories on the left", "products on the right", or "refresh at the bottom" as a soft position preference. Keep the required element, but set its hard-requirement `position` to `null`.
- Let an explicit locked position override conflicting A geometry. Let applicable A geometry override an ordinary position preference.

Do not infer immutability merely because an evidence substring contains a direction word.

## Build from the A layout skeleton

When A exists, synthesize in this order:

```text
A major regions + relationships + hierarchy + proportions
-> transferable layout skeleton
-> requested business components
-> semantic region mapping
-> final component tree and layout rules
```

Do not finish a generic business template first and then cite only the A regions that happen to match it.

Extract and preserve, where supported by A:

- major regions and their approximate proportions;
- dominant central content and primary/secondary focal hierarchy;
- left and right edge rails;
- top and bottom horizontal bands;
- primary action bands and parent-child placement;
- adjacency, control, above/below, and containment relationships;
- repeated-region direction.

Map requested semantics into these spaces without copying A's characters, labels, icons, business functions, or brand assets. A central character showcase may become a primary product surface; a shortcut rail may become secondary information or preserved non-interactive layout space. If the user forbids additional business content, preserve the spatial relationship with restrained empty, decorative, or non-interactive support rather than inventing a function.

For every high-confidence A major region, write an explicit `reference_application.layout` decision with `adopted`, `adapted`, or `ignored`. Prefer `adapted` when only source content conflicts but its spatial role transfers. Use `ignored` only for a genuine conflict with a higher authority, and state that conflict precisely. "Source business content was not requested" is not by itself sufficient reason to discard the region's spatial structure.

Preserve transferable A relationships even when source and target business names differ. Cite the real region, relationship, hierarchy, group, or layout-rule IDs that justify each skeleton decision.

When A exposes a dedicated primary action region below the dominant central region, such as `primary_mode_action_region`, map it to a separate `central_lower_action_band`. Place the requested primary action, including a refresh action, inside that central band rather than inside the right auxiliary rail. Keep the right rail narrow, secondary, and non-dominant, and keep the central content visibly dominant by proportion and focal hierarchy. A bottom navigation region or bottom band may remain `ignored` when the target has no compatible navigation or business need; record that adding it would invent an unrelated control surface instead of silently discarding it.

## Decision origins

Every layout decision has one origin:

- `layout_reference` - directly cites applicable A evidence. Set a real `source_kind`, at least one `source_id`, and `source_meaning`.
- `user_requirement` - comes from the user. Set `source_kind: null`, `source_ids: []`, and `source_meaning: null`.
- `composer_derived` - low-risk UI completion such as padding, spacing, containers, internal card zones, or simple selected state. Use the same empty A fields.

Every style decision has one origin:

- `style_reference` - cites one real B `trait_id` and copies its dimension and classification.
- `user_requirement` - explicit user visual direction; use `trait_id: null` and `classification: null`.
- `composer_derived` - low-risk visual completion not asserted as B evidence; use null trait/classification.

Composer-derived decisions may not invent business systems or alter user semantics. Do not attach an A/B citation merely to make traceability look complete.

## A and B usage

Use A as the starting layout skeleton when relevant, not as optional decoration after layout completion. A is not target business content. For `origin=layout_reference`, copy the exact ID and kind visible in the validated input. Python later performs definitive membership validation.

Use B classification exactly as provided:

- `stable`: may be primary/global when relevant.
- `secondary`: conditionally adopt and scope.
- `local`: ignore or apply locally; global promotion requires exact user evidence.
- `conflicting`: reject or warn; do not select a default side.
- `uncertain`: ignore or warn; do not make it a hard fact.

For `origin=style_reference`, copy the exact B trait, dimension, and classification. Python later performs definitive membership and classification validation.

The behavioral rule "do not invent IDs" remains, but factual truth is not delegated to the model.

## One-pass synthesis workflow

1. Consume one builder-created, already validated v2.1.1 input.
2. Parse the explicit user requirement.
3. Split semantic/count facts, explicit locked positions, and soft position preferences.
4. Build the hard-requirement ledger and target page semantic.
5. Extract A's major-region skeleton, relationships, hierarchy, proportions, and repeat directions.
6. Map requested business components into that skeleton, applying locked user positions first and soft positions after A.
7. Record every A major-region disposition and every material layout/style origin honestly.
8. Select only relevant B traits under their original classifications.
9. Build the target component tree, layout, interactions, and visual direction.
10. Check repeat direction and layout intent consistency.
11. Derive generation constraints from user facts, the final design, and approved evidence.
12. Emit one candidate `ui-compose-plan` and end.

Do not run a generate/check/rewrite cycle inside this Skill.

## Candidate output

Use `schemas/ui-compose-plan.schema.json` for the machine contract and `references/output-schema.md` for the human-readable output contract. Do not use a complete prior plan as a runtime template.

Keep the existing v2 top-level structure:

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

For every `repeat`, emit all five required fields: `count`, `arrangement`, `columns`, `rows`, and `content_variation`. Apply the arrangement contract exactly:

- `row` or `column`: set both `columns` and `rows` to `null`; `count` carries the repeat quantity.
- `grid`: set both `columns` and `rows` to positive integers and require `count == columns * rows`.
- `list`, `carousel`, or `custom`: these are also non-grid arrangements, so set both `columns` and `rows` to `null`.

Never infer numeric grid dimensions from a non-grid repeat count. A single-page request may have empty navigation. Add only necessary actions.

Keep repeat metadata consistent with spatial intent:

- a vertical stack uses `arrangement: column` with null grid dimensions;
- a horizontal band uses `arrangement: row` with null grid dimensions;
- a true grid uses `arrangement: grid` with positive columns/rows and matching count;
- design intent, component notes, A repeat direction, and layout relationships must not contradict the repeat arrangement.

`generation_constraints` is a derived summary, not a new design stage or image prompt.

## Deterministic validation handoff

Composer proposes design and evidence choices. It does not certify A/B membership, upstream immutability, complete schema legality, or overall consistency.

Run after candidate generation:

```powershell
python scripts/validate_plan.py <candidate-plan.json> --input <validated-input.json>
```

The validator builds an Evidence Registry from the actual input and returns PASS or FAIL. It reports exact paths and invalid values. It never guesses a replacement ID and never repairs or re-runs Composer.

For original upstream integrity:

```powershell
python scripts/validate_input.py <input.json> `
  --layout-source <original-a.json> `
  --style-source <original-b2.json>
```

## Known issue

Parent-relative child anchors can be misinterpreted by the existing position validator when the parent establishes left/right placement. V2.1.1 records this for a later focused fix and does not redesign layout-parent validation.

## Final rule

Generate one requirement-preserving candidate, label origins honestly, and let deterministic code decide evidence truth and contract validity.
