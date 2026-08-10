# UI Compose Input v2.1

## Contract

Accept only `schemas/ui-compose-input.schema.json`:

```text
schema_version: 2.1
request
layout_reference_analysis
style_profile
```

`request.user_requirement` is required and is the highest authority for business/page semantics, content, counts, grids, information, positions, actions, layout changes, and visual changes. Optional project, game, page, orientation, resolution, constraints, and visual preferences provide context only.

## Immutable A and B

`layout_reference_analysis` is the complete authoritative A final object. `style_profile` is the complete authoritative B2 object. Their schemas are referenced directly from the adjacent Skills.

Composer must not summarize, normalize, repair, mutate, or reinterpret embedded upstream values or confidence. With original files available, use JSON deep equality:

```powershell
python scripts/validate_input.py <input.json> `
  --layout-source <original-a.json> `
  --style-source <original-b2.json>
```

The validator reports leaf JSON paths with `UPSTREAM_INTEGRITY_MISMATCH`. JSON numeric equivalents compare equal.

## Strict rejection

Reject and stop when:

- Composer version is not 2.1;
- the user requirement is missing or blank;
- A or B fails its real schema;
- embedded A/B differs from supplied originals;
- `pics` occurs anywhere;
- legacy top-level `assets` occurs;
- a strict object has unexpected fields.

Do not partially compose or fall back to the legacy asset workflow.
