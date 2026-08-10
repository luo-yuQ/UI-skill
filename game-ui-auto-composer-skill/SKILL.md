---
name: game-ui-auto-composer-skill
description: Synthesize one new engine-neutral game UI candidate from an immutable A layout analysis, an immutable B2 style profile, and an ordinary-language user requirement. Use when the caller supplies ui-compose-input v2.1.1 and needs one requirement-preserving candidate ui-compose-plan v2.1.1 with explicit decision origins for deterministic Python validation.
---

# Game UI Auto Composer v2.1.1

Design one new UI candidate. The user defines the target; A supplies optional layout evidence; B supplies optional classified style evidence. Deterministic Python code decides whether cited evidence and the resulting contract are valid.

```text
validated input
→ design synthesis
→ candidate ui-compose-plan
→ END
```

Do not self-loop, retry, repair, or repeatedly rewrite the candidate. Validation and future repair are separate stages.

## Required resources

- `schemas/ui-compose-input.schema.json` — v2.1.1 input.
- `schemas/ui-compose-plan.schema.json` — v2.1.1 candidate plan.
- `scripts/build_compose_input.py` — deterministic UTF-8 input construction and source-integrity checks.
- `scripts/evidence_registry.py` — immutable Pydantic A/B registry.
- `scripts/validate_input.py` — schema and upstream-integrity validation.
- `scripts/validate_plan.py` — schema, requirements, origin, registry membership, and consistency validation.
- `references/input-schema.md` and `references/output-schema.md` — contract details.
- `references/workflow.md` — one-pass workflow and validation handoff.
- `references/examples/example-ui-compose-input.json` and `example-ui-compose-plan.json` — regression fixtures and schema shape only.

Never inherit example counts, page semantics, IDs, hierarchy, actions, or copy into another run.

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

- change explicit user counts, page type, business semantics, content, position, or action;
- mutate or normalize upstream A/B JSON or confidence;
- invent an A source ID or B trait ID;
- force every design decision to cite A or B;
- promote a local B trait globally without an explicit matching user requirement;
- let `generation_constraints` create new design requirements;
- turn `conflicting` or `uncertain` B evidence into hard facts;
- create a runtime, repair loop, retry loop, cache, orchestrator, or state machine.

## Authority and hard requirements

Apply:

```text
Explicit User Requirement
> Derived User Intent
> A Layout Reference
> B Style Evidence
> Composer Assumptions
```

Extract explicit facts into `project_context.hard_requirements` before designing:

- page semantic;
- exact counts;
- grid rows and columns;
- required elements and positions;
- required information and actions;
- must-include and must-not-include constraints.

Evidence strings must be exact user-requirement substrings. Preserve these facts through pages, components, layout, interactions, and generation constraints.

## Decision origins

Every layout decision has one origin:

- `layout_reference` — directly cites applicable A evidence. Set a real `source_kind`, at least one `source_id`, and `source_meaning`.
- `user_requirement` — comes from the user. Set `source_kind: null`, `source_ids: []`, and `source_meaning: null`.
- `composer_derived` — low-risk UI completion such as padding, spacing, containers, internal card zones, or simple selected state. Use the same empty A fields.

Every style decision has one origin:

- `style_reference` — cites one real B `trait_id` and copies its dimension and classification.
- `user_requirement` — explicit user visual direction; use `trait_id: null` and `classification: null`.
- `composer_derived` — low-risk visual completion not asserted as B evidence; use null trait/classification.

Composer-derived decisions may not invent business systems or alter user semantics. Do not attach an A/B citation merely to make traceability look complete.

## A and B usage

Select useful A layout organization only when relevant. A is not target business content. For `origin=layout_reference`, copy the exact ID and kind visible in the validated input. Python later performs definitive membership validation.

Use B classification exactly as provided:

- `stable`: may be primary/global when relevant.
- `secondary`: conditionally adopt and scope.
- `local`: ignore or apply locally; global promotion requires exact user evidence.
- `conflicting`: reject or warn; do not select a default side.
- `uncertain`: ignore or warn; do not make it a hard fact.

For `origin=style_reference`, copy the exact B trait, dimension, and classification. Python later performs definitive membership and classification validation.

The behavioral rule “do not invent IDs” remains, but factual truth is not delegated to the model.

## One-pass synthesis workflow

1. Consume one builder-created, already validated v2.1.1 input.
2. Parse the explicit user requirement.
3. Build the hard-requirement ledger and target page semantic.
4. Select only relevant A evidence.
5. Select only relevant B traits under their original classifications.
6. Label every material layout and style decision with its true origin.
7. Build the target component tree, layout, interactions, and visual direction.
8. Derive generation constraints from user facts, the final design, and approved evidence.
9. Emit one candidate `ui-compose-plan` and end.

Do not run a generate/check/rewrite cycle inside this Skill.

## Candidate output

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
