# UI Compose Plan v2.1.1

Return one candidate `ui-compose-plan.json` conforming to `schemas/ui-compose-plan.schema.json`. The v2 top-level structure remains unchanged.

## Decision origins

Layout decisions:

- `layout_reference`: real `source_kind`, one or more real A `source_ids`, and source meaning are required.
- `user_requirement`: `source_kind: null`, `source_ids: []`, `source_meaning: null`.
- `composer_derived`: the same empty A fields; limited to low-risk UI completion.

Style decisions:

- `style_reference`: real B `trait_id`, dimension, and classification are required.
- `user_requirement`: null trait/classification.
- `composer_derived`: null trait/classification and no claim of B evidence.

Not every new design decision needs A/B evidence. The origin must describe where it actually came from.

## Evidence Registry validation

`scripts/evidence_registry.py` recursively reads the actual A/B objects into frozen Pydantic records containing IDs, types/classifications, and JSON paths. It never mutates upstream data.

`scripts/validate_plan.py` enforces:

- source ID membership only for `origin=layout_reference`;
- trait membership and exact B classification only for `origin=style_reference`;
- exact error paths and invalid values;
- no automatic correction or semantic ID mapping;
- hard requirements, local scope, and cross-section consistency.

```powershell
python scripts/validate_plan.py <candidate-plan.json> --input <input.json>
```

The result is PASS or FAIL. Repair and retry loops are outside V2.1.1.

## Known issue

Parent-relative child anchors may be misread by the existing required-position check when the parent owns left/right placement. This version records the issue without redesigning layout-parent validation.
