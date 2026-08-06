---
name: game-ui-auto-composer-skill
description: Plan game UI pages from user requirements and precomputed structured asset-analysis data. Use when the user has supplied a JSON object conforming to schemas/ui-compose-input.schema.json and needs a single engine-neutral ui-compose-plan JSON describing pages, asset usages, component hierarchy, layout intent, interactions, navigation, missing assets, assumptions, and warnings.
---

# Game UI Auto Composer Skill

Produce one implementation-oriented, engine-neutral UI composition plan from structured user requirements and authoritative upstream asset analysis.

## Use this skill when

Use this skill only when:

- the user has supplied structured asset-analysis entries inside a `ui-compose-input` object
- the input conforms to `schemas/ui-compose-input.schema.json`
- the task is to decide page scope and how analyzed assets should be used in those pages
- the requested result is an engine-neutral `ui-compose-plan` JSON object

Do not use this skill for asset discovery, visual recognition, image inspection, or engine implementation output.

## Required local resources

Treat these files as authoritative:

- `schemas/ui-compose-input.schema.json` - only valid input contract
- `schemas/ui-compose-plan.schema.json` - only core output contract
- `references/input-schema.md` - input usage rules
- `references/output-schema.md` - output field responsibilities
- `references/workflow.md` - phase inputs, outputs, and stop conditions
- `references/examples/example-ui-compose-input.json` - valid input example
- `references/examples/example-ui-compose-plan.json` - valid output example

## Hard responsibility boundary

The upstream image-analysis agent owns:

- visual content recognition
- dimensions, aspect ratio, format, and transparency
- asset category and visual identity
- intended role and role candidates
- visual description
- stretching or slicing suitability
- confidence and analysis notes

This skill owns:

- project and request interpretation
- page planning
- the actual use of each asset in each page
- component hierarchy
- layout intent and visual hierarchy
- page and component states
- interactions and navigation
- missing-asset detection
- assumptions, warnings, and conservative fallbacks
- the final engine-neutral composition plan

## Input contract

Accept exactly one JSON object conforming to `schemas/ui-compose-input.schema.json`.

The top-level object must contain:

- `schema_version`
- `request`
- `assets`

Reject the input and stop when schema validation or required semantic validation fails.

### Forbidden input behavior

- Reject any object containing `pics`.
- Do not accept raw images as current-skill input.
- Do not guess asset categories from file names.
- Do not open, fetch, resolve, inspect, or decode `source_ref`.
- Do not use multimodal capabilities to reinterpret an asset.
- Do not fall back to any legacy raw-image workflow when asset analysis is absent.
- Do not silently repair missing required fields by inventing asset facts.

## Asset-analysis consumption rules

Treat each `asset_analysis` object as authoritative upstream facts.

Use engine-neutral facts such as:

- `asset_id`
- file identity and `visual_identity`
- `asset_category`
- `intended_role` and `role_candidates`
- objective dimensions, aspect ratio, format, and transparency
- `visual_description`
- stretch, scale, crop, protected-region, and slicing suitability
- confidence and notes

Do not reclassify what an asset is. Decide only how, where, and whether the analyzed asset should be used for the current request.

Ignore and never copy or depend on implementation-specific data embedded in upstream analysis, including:

- `laya_new_ui`
- Unity, Cocos, or FairyGUI fields
- engine component class names
- engine resource formats
- engine serialization details

### `source_ref` rule

Treat `source_ref` as opaque.

- Copy it unchanged only into the matching `asset_usages[].source_ref` output entry.
- Never use it as evidence for asset identity, category, style, dimensions, or suitability.
- Never test whether its path, URI, attachment, or opaque identifier exists.

## Workflow

### Step 1: Validate structured input

Validate the input against `schemas/ui-compose-input.schema.json` and run semantic checks for:

- `schema_version`
- required `request` fields
- non-empty `assets`
- required `asset_analysis` and `source_ref` per asset
- non-empty, unique `asset_id` values
- `primary_page_id` membership in `request.page_request.pages`
- explicit rejection of `pics`

Use `python scripts/validate_input.py <input.json>` when local execution is available.

If validation fails, return a structured error and stop. Do not generate a partial plan.

### Step 2: Understand request and project context

Read only the structured `request` object.

Determine:

- project name and game context
- requested page scope
- orientation and target resolution
- page goals and explicit requirements
- user constraints and visual preferences

Do not add unrelated default pages. Record reasonable low-impact interpretation as assumptions.

### Step 3: Consume upstream asset facts

For every asset:

- retain its stable `asset_id`
- consume only engine-neutral analysis facts
- note confidence and uncertainty
- identify conflicts between requested use and upstream facts
- preserve `source_ref` as an opaque downstream reference

Do not perform visual recognition or override upstream classification.

### Step 4: Plan pages

Build `pages` from `request.page_request`.

For each page define:

- page purpose
- root component
- initial and alternate states
- entry conditions
- exit conditions

Add a page only when the request or required navigation flow justifies it.

### Step 5: Compose asset usages and component tree

Decide which analyzed assets are useful for each requested page.

For each use define:

- page and component assignment
- semantic role in the composition
- usage intent
- fit, crop, and resizing policy
- visual priority
- visible states
- analysis confidence and risk notes

Build an engine-neutral `component_tree` with stable component IDs, page IDs, parent relationships, semantic types, ordering, asset-usage references, states, and content intent.

### Step 6: Define layout rules

Define one or more engine-neutral layout rules for every relevant component:

- reference space
- semantic anchor
- normalized pivot and position
- relative or logical dimensions
- stack order
- safe-area policy
- protected-region notes

Prefer constraints and normalized relationships over engine-specific coordinates.

See:

- `references/layout-rules/anchor-and-sizing.md`
- `references/layout-rules/safe-area.md`
- `references/layout-rules/gameplay-exclusion-zones.md`
- `references/layout-rules/click-targets.md`

### Step 7: Define interactions and navigation

Define:

- interaction trigger and triggering component
- semantic action
- conditions
- state changes
- feedback intent
- navigation source and destination
- transition intent and history behavior

Do not emit event-handler class names, engine callbacks, or implementation code.

### Step 8: Review missing assets, assumptions, and warnings

Before output:

- list requested visuals or functions without supplied assets
- state whether each missing asset blocks the plan
- define conservative structural fallback where allowed
- record every inferred decision as an assumption
- record asset-confidence, resizing, hierarchy, interaction, and navigation risks as warnings

Never fabricate an asset or claim to have verified `source_ref`.

### Step 9: Output ui-compose-plan JSON

Return exactly one JSON object conforming to `schemas/ui-compose-plan.schema.json`.

Validate the completed object before returning it.

The successful core response must contain no surrounding prose, Markdown report, code fence, or secondary deliverable.

## Output contract

The core output must contain all 12 required top-level fields:

- `schema_version`
- `project_context`
- `visual_direction`
- `pages`
- `asset_usages`
- `component_tree`
- `layout_rules`
- `interactions`
- `navigation`
- `missing_assets`
- `assumptions`
- `warnings`

### Forbidden core output

Do not add:

- Markdown design reports
- HTML/CSS prompts or prototype output
- GPT Image prompts or image-generation request fields
- Laya, Unity, Cocos, or FairyGUI implementation fields
- TypeScript class names
- engine component class names
- engine resource or project file formats

Future adapters may consume `ui-compose-plan.json`, but adapter output is outside this skill's core response.

## Structured validation error

On invalid input, return only an error object shaped like:

```json
{
  "status": "error",
  "error_code": "INPUT_VALIDATION_FAILED",
  "errors": [
    {
      "path": "$.assets[0].asset_analysis",
      "message": "Missing required field"
    }
  ],
  "warnings": []
}
```

Do not continue composition after returning this error.

## Question and fallback policy

- Treat missing required contract fields as validation errors, not clarification opportunities.
- Use assumptions only for optional, low-impact ambiguity.
- Use `missing_assets` when a requested visual has no analyzed asset.
- Use warnings when a plan remains possible but carries uncertainty.
- Keep conservative fallback structural; never invent upstream visual facts.

## Legacy resources

Legacy raw-asset examples, templates, classifiers, prototype helpers, and engine compatibility notes may remain in the repository for historical or future adapter work. They are not part of this workflow and must not override the two core Schemas.

## Final rule

Consume structured facts. Plan usage and behavior. Return one valid engine-neutral `ui-compose-plan` JSON object.
