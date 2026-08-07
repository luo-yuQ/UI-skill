# UI Compose Plan Output Contract

## Authoritative contract

The single successful core output is one JSON object conventionally named:

```text
ui-compose-plan.json
```

It must conform to:

- `schemas/ui-compose-plan.schema.json`

Use this complete example:

- `references/examples/example-ui-compose-plan.json`

Do not use the former multi-deliverable bundle as the default output.

## Required top-level fields

All 12 fields below are required.

### `schema_version`

Identifies the compose-plan contract version.

### `project_context`

Records the project name, game description, orientation, target resolution, requested page scope, and governing constraints.

### `visual_direction`

Defines the engine-neutral visual intent: summary, style keywords, palette roles, visual hierarchy principles, and asset-style notes.

### `pages`

Defines every planned page, its purpose, root component, states, entry conditions, and exit conditions.

### `asset_usages`

Maps analyzed assets to page components and records their actual composition role, use intent, fit/crop/resizing policy, visual priority, visible states, confidence, and opaque `source_ref`.

### `component_tree`

Defines a stable engine-neutral component hierarchy using component IDs, page IDs, parent IDs, semantic types, ordering, asset-usage references, state references, and content intent.

### `layout_rules`

Defines reference space, semantic anchors, normalized position and pivot, dimensions, stack order, safe-area policy, and constraint notes.

### `interactions`

Defines triggers, semantic actions, conditions, state changes, feedback intent, and optional navigation references.

### `navigation`

Defines page-to-page flow, triggering interaction, transition intent, duration intent, and history behavior.

### `missing_assets`

Lists requested visual needs without supplied analyzed assets, their severity, affected IDs, and conservative fallback.

### `assumptions`

Records optional ambiguity resolved during planning, its impact, and confidence.

### `warnings`

Records non-fatal risks and the plan entities they affect.

## Output rules

- Return one JSON object and no surrounding prose.
- Preserve each used asset's `source_ref` unchanged.
- Keep component and layout semantics engine-neutral.
- Represent missing visuals explicitly instead of inventing assets.
- Use `assumptions` for low-impact interpretation.
- Use `warnings` for uncertainty that does not block the plan.
- Stop on invalid input rather than emitting an incomplete plan.

## Not part of the core output

The core plan must not include:

- Markdown reports
- prototype prompts
- image-generation prompts or request payloads
- implementation component classes
- implementation resource formats
- generated project files or source code

## Future parallel adapters

Two future adapters may independently consume the same valid `ui-compose-plan.json`:

```text
ui-compose-plan.json
├─ GPT Image Preview Adapter
└─ Laya New UI Adapter
```

These adapters are downstream consumers. Their request fields, implementation mappings, files, and API behavior are not part of the core output contract and are not produced by this workflow.

## Validation failure

When input validation fails, return a structured validation error rather than `ui-compose-plan.json`. Do not mix an error response with partial plan fields.
