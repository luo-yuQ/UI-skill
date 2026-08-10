# Composer v2.1 Strict Workflow

## Contract boundary

Input is one valid `ui-compose-input` v2.1 object containing an ordinary-language user requirement plus immutable complete A final and B2 objects. Output is one valid `ui-compose-plan` v2.1 object. Raw images, legacy asset placement, previews, and implementation are outside Composer core.

## Authority

```text
Explicit User Requirement
> Derived User Intent
> A Layout Reference
> B Style Evidence
> Composer Assumptions
```

No lower source may alter a higher-source fact.

## Step 1: Validate immutable A/B inputs

Validate all three schemas. Reject legacy fields and compare embedded A/B with their original JSON values when source paths are available. Stop on any path mismatch.

## Step 2: Parse explicit user requirements

Extract the requested business/page type, named content, counts, grid dimensions, information, positions, actions, layout changes, and visual changes.

## Step 3: Build the hard-requirement ledger

Write `project_context.hard_requirements`. Each evidence string must occur verbatim in `user_requirement`.

## Step 4: Identify user target semantics

Set the page semantic before consulting A, B, or examples. Never rename it for design completeness.

## Step 5: Read A as layout evidence

Read only A's regions, relationships, groups, hierarchy, layout rules, excluded content, and uncertainty. Do not import its business content.

## Step 6: Read B as style evidence

Index traits by real `trait_id`, dimension, and classification. Preserve every confidence and classification without reinterpretation.

## Step 7: Select applicable A references

Adopt, adapt, ignore, or reject. Every cited source ID must exist in the matching A entity collection.

## Step 8: Select B traits by classification and scope

Use relevant stable traits; scope secondary traits; keep local traits on one matching component unless explicitly promoted; do not resolve conflicting or uncertain evidence by assumption.

## Step 9: Create the new component tree

Build target-semantic components. Copy every explicit count into repeat specifications. Grid count must equal columns multiplied by rows.

## Step 10: Adapt layout

Map only selected A relationships. Make required left/right/top/bottom facts machine-checkable through target layout anchors.

## Step 11: Derive visual direction

Use adopted B decisions and explicit user visual choices only. Directive trait IDs must exist, be adopted, and stay within scope.

## Step 12: Create interactions

Add only required or strictly necessary engine-neutral behavior. A requested refresh remains refresh; do not invent another business action.

## Step 13: Derive generation constraints

Derive them from user facts, the final tree/rules, and approved A/B decisions. They are not a new design stage or prompt.

## Step 14: Requirement preservation check

Check page semantic, every explicit count, grid, required element, position, information item, and action against the hard ledger.

## Step 15: Semantic drift check

Check target summary, pages, components, interactions, and constraints for business semantics absent from the user requirement.

## Step 16: Traceability validation

Verify all A `source_ids`, all B `trait_id` values, B classifications, decision scopes, and directive sources against the actual input objects.

## Step 17: Cross-section consistency check

Reconcile important facts across `design_summary`, `component_tree`, `layout_rules`, `interactions`, `generation_constraints`, and `reference_application`.

## Step 18: Return JSON

Return exactly one v2.1 plan and no adapter output.

## Commands

```powershell
python scripts/validate_input.py <input.json> --layout-source <a.json> --style-source <b2.json>
python scripts/validate_plan.py <plan.json> --input <input.json>
```

Examples show schema shape only and never supply target requirements.
