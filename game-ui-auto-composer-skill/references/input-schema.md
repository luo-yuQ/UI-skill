# UI Compose Input v2

## Authoritative contract

Accept only a JSON object conforming to `schemas/ui-compose-input.schema.json`.

```text
schema_version
request
layout_reference_analysis
style_profile
```

`schema_version` is fixed to `2.0`. This is a breaking change; do not silently accept v1.

## Request

Require only a non-whitespace `request.user_requirement`. This ordinary-language field is the highest authority for the target business goal, content, counts, interactions, explicit layout changes, and explicit visual changes.

Allow optional:

- `project_name`
- `game_context`
- `page_context`
- `orientation`
- `target_resolution`
- `constraints`
- `visual_preferences`

Do not require users to provide a professional UI decomposition before Composer can work.

## A layout reference

`layout_reference_analysis` contains the complete A final object and resolves directly to the upstream `$id`:

```text
https://example.com/schemas/layout-reference-analysis.schema.json
```

The local authority is `../game-ui-layout-reference-analyzer/schemas/layout-reference-analysis.schema.json`. Use A only for portable structure: regions, hierarchy, relationships, relative proportions, alignment, grouping, repetition, and layout-related focal relationships. Do not treat its business content as a target requirement.

## B2 style profile

`style_profile` contains the complete B2 object and resolves directly to:

```text
https://example.com/schemas/style-profile.schema.json
```

The local authority is `../game-ui-style-reference-analyzer/schemas/style-profile.schema.json`. Use B only for classified visual-language evidence. Do not treat it as layout authority or target content.

## Strict rejection

Reject with JSON-path errors and stop when:

- Composer `schema_version` is not `2.0`;
- `user_requirement` is absent or whitespace-only;
- A or B fails its authoritative schema;
- `pics` occurs anywhere;
- legacy top-level `assets` occurs;
- a strict object contains unexpected fields.

Do not partially compose, automatically repair A/B, inspect source images, or fall back to the v1 asset workflow.

## Validation

```powershell
python scripts/validate_input.py references/examples/example-ui-compose-input.json
```

The validator resolves both upstream schemas from their adjacent Skill directories. When the third-party `jsonschema` package is unavailable, it reports that fact and uses the bundled keyword-limited validator over all three real contracts; it never substitutes private A/B summaries.

## Legacy

`asset-analysis.schema.json`, `asset-analysis.example.json`, `assets/samples`, `pics`, `assets[].asset_analysis`, `source_ref` propagation, and `request_notes` are not Composer v2 input semantics.
