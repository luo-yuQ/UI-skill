# Workflow

## Contract boundary

Input:

- one JSON object conforming to `schemas/ui-compose-input.schema.json`
- structured user requirements in `request`
- authoritative upstream asset facts in `assets[].asset_analysis`
- opaque downstream references in `assets[].source_ref`

Output:

- one JSON object conforming to `schemas/ui-compose-plan.schema.json`

No alternate input or successful output mode exists in the core workflow.

## Core flow

```text
validate
→ understand
→ consume asset facts
→ plan
→ compose
→ define behavior
→ review
→ output
```

## Phase 1: Validate

Input:

- candidate `ui-compose-input` JSON object

Checks:

- reject `pics`
- contract `schema_version`
- required `request` structure
- non-empty `assets`
- required `asset_analysis` and `source_ref`
- non-empty, unique `asset_id` values
- valid `primary_page_id`

Output:

- validated input, or
- structured validation error and immediate stop

Do not continue with partial data when a required check fails.

## Phase 2: Understand

Input:

- validated `request`

Process:

- establish project context
- read the requested page scope and flow
- identify orientation and target resolution
- collect explicit requirements, constraints, and visual preferences

Output:

- `project_context`
- initial page goals
- low-impact interpretation candidates for `assumptions`

## Phase 3: Consume asset facts

Input:

- validated `assets[].asset_analysis`
- opaque `assets[].source_ref`

Process:

- index assets by stable `asset_id`
- retain engine-neutral visual and technical facts
- retain confidence and uncertainty
- ignore implementation-specific upstream fields
- detect conflicts between requested use and upstream facts

Output:

- engine-neutral asset fact index
- unchanged source-reference map
- candidate usage constraints and warnings

Do not reopen `source_ref`, guess from file names, or override upstream asset identity and category.

## Phase 4: Plan

Input:

- project context
- requested pages and flow
- engine-neutral asset fact index

Process:

- determine justified page scope
- define page purposes
- define page states
- define entry and exit conditions
- identify missing assets

Output:

- `pages`
- initial `missing_assets`
- page-level assumptions

## Phase 5: Compose

Input:

- planned pages
- asset fact index
- source-reference map

Process:

- decide actual asset use per page
- map asset usages to components
- define component parent relationships and semantic roles
- define visual priority
- define engine-neutral layout constraints

Output:

- `visual_direction`
- `asset_usages`
- `component_tree`
- `layout_rules`

The composition may decide not to use an analyzed asset. It must not redefine what the asset is.

## Phase 6: Define behavior

Input:

- pages and component tree
- requested user flow

Process:

- define semantic triggers and actions
- define page and component state changes
- define navigation relationships
- define transition and feedback intent

Output:

- `interactions`
- `navigation`

Keep behavior free of implementation class names, callback signatures, and file formats.

## Phase 7: Review

Input:

- complete draft plan

Checks:

- every page has a valid root component
- every used asset maps to a known component and page
- every `source_ref` is passed through unchanged
- layout respects safe areas and upstream protected regions
- interactions and navigation reference planned entities
- missing assets have severity and fallback
- assumptions are explicit
- warnings reflect uncertainty and confidence
- no implementation-specific fields entered the core output

Output:

- finalized `missing_assets`
- finalized `assumptions`
- finalized `warnings`
- reviewed plan candidate

## Phase 8: Output

Input:

- reviewed plan candidate

Process:

- validate against `schemas/ui-compose-plan.schema.json`

Output:

- exactly one valid `ui-compose-plan` JSON object

Do not append a report, prompt, implementation mapping, code, or secondary artifact.

## Failure behavior

If input validation fails:

1. return a structured error object
2. include machine-readable error paths and messages
3. include non-fatal warnings separately
4. stop before planning

If optional information is uncertain after validation:

1. choose the most conservative structural interpretation
2. record it in `assumptions`
3. record material risk in `warnings`
4. use `missing_assets` when a required visual resource is absent
