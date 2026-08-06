# UI Compose Input Contract

## Authoritative contract

The only supported input is a JSON object conforming to:

- `schemas/ui-compose-input.schema.json`

Use this complete example:

- `references/examples/example-ui-compose-input.json`

Do not use legacy raw-asset input examples in the current workflow.

## Top-level structure

The input requires exactly three top-level fields:

```text
schema_version
request
assets
```

`schema_version` must match the version required by the Schema.

## `request`

`request` contains the user's project and composition requirements.

Required sections:

- `game_description`
  - project title
  - game genre
  - gameplay summary
  - optional core interaction, audience, and style keywords
- `page_request`
  - `primary_page_id`
  - requested `pages`
  - optional flow description
- `orientation`
- `target_resolution`

Optional request-level fields such as `constraints` and `visual_preferences` guide composition but do not replace asset-analysis facts.

Every requested page must provide:

- stable `page_id`
- semantic `page_type`
- page goal
- at least one requirement

`primary_page_id` must match one of the requested page IDs.

## `assets`

`assets` is a non-empty array of analyzed asset entries.

Each item requires:

- `asset_analysis`
- `source_ref`

Optional `request_notes` may constrain how that analyzed asset is used in the requested UI.

## `asset_analysis`

Each `asset_analysis` must conform to the existing `asset-analysis.schema.json` contract and must contain a stable, non-empty `asset_id`.

Treat the following engine-neutral fields as authoritative upstream facts when present:

- file and visual identity
- category and intended role
- dimensions, aspect ratio, format, and transparency
- visual description
- scale, crop, stretching, protected-region, and slicing suitability
- confidence and notes

Do not infer a different category from a file name. Do not repeat visual analysis.

Implementation-specific fields embedded in an upstream payload are outside the compose contract. Ignore them and do not copy them into `ui-compose-plan.json`.

## `source_ref`

`source_ref` is an opaque downstream retrieval reference.

Allowed reference kinds are defined by `schemas/ui-compose-input.schema.json`.

Within this skill:

- preserve the object unchanged
- associate it with the same `asset_id`
- copy it only to the matching output asset usage
- do not open it
- do not fetch it
- do not decode it
- do not check whether a referenced path or URI exists
- do not use it to confirm or revise asset-analysis facts

## Unsupported legacy input

The following are invalid for the current workflow:

- a `pics` field
- raw images as the input to this skill
- a top-level legacy `game_description` object
- file-name-only asset lists
- assets without `asset_analysis`
- automatic fallback to old input behavior

Files that still demonstrate the old format are legacy repository material and must not be used as main-flow examples.

## Validation

Run:

```bash
python scripts/validate_input.py references/examples/example-ui-compose-input.json
```

Validation must stop composition when required structure or semantic identity checks fail. Optional recommendations may produce warnings but must not be promoted to required-field errors.
