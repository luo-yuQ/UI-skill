# UI Compose Plan v2.1

## Contract

Return one `ui-compose-plan.json` conforming to `schemas/ui-compose-plan.schema.json`. The existing v2 top-level structure remains:

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

## V2.1 additions

`project_context.hard_requirements` records machine-checkable user facts with verbatim evidence: page semantic, explicit counts, grid requirements, required elements, and include/exclude rules.

Grid repeats add `columns` and `rows`. `generation_constraints.grid_specs` repeats the final grid facts for downstream consistency.

B decisions add `promoted_by_user_requirement` and `promotion_evidence`. A local trait is ignored or kept on one matching component unless exact user evidence authorizes wider promotion.

## Traceability and preservation

Every A `source_id` must exist in the matching A entity kind. Every B `trait_id`, dimension, and classification must match B2. Visual directives may cite only adopted decisions and must respect local scope.

The plan page semantic, counts, grid, required positions, and actions must match the hard ledger. Cross-check `component_tree`, `layout_rules`, `interactions`, and `generation_constraints`.

```powershell
python scripts/validate_plan.py <plan.json> --input <input.json>
```

## Generation constraints

They may contain only facts derived from explicit user requirements, the final component tree, final layout rules, and approved A/B decisions. They are not a prompt and cannot invent a new design requirement.

V1 `asset_usages` and `missing_assets` remain forbidden.
