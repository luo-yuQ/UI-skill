# UI Compose Input v2.1.1

Accept only `schemas/ui-compose-input.schema.json`:

```text
schema_version: 2.1.1
request
layout_reference_analysis
style_profile
```

`request.user_requirement` is the highest authority for page/business semantics, content, counts, grids, positions, information, actions, and explicit visual changes.

A and B are complete immutable upstream artifacts. Composer must not summarize, normalize, repair, mutate, or reinterpret their values or confidence.

```powershell
python scripts/validate_input.py <input.json> `
  --layout-source <original-a.json> `
  --style-source <original-b2.json>
```

The validator performs schema validation and JSON deep equality. It reports exact leaf paths with `UPSTREAM_INTEGRITY_MISMATCH`; JSON numeric equivalents compare equal.

Reject blank requirements, invalid A/B, upstream differences, legacy `pics`, legacy top-level `assets`, and unexpected fields. Do not partially compose or fall back to a legacy workflow.
