# GPT Image Preview Adapter v0.1

## Purpose

Build a provider-neutral `preview-request.json` for one page in an existing UI compose plan.

The adapter prepares structured instructions for later human review or a separate image-generation submission layer. It does not call a model, access a network, or inspect source assets.

## Inputs

The adapter requires all four CLI arguments:

- `--input`: JSON conforming to `schemas/ui-compose-input.schema.json`
- `--plan`: JSON conforming to `schemas/ui-compose-plan.schema.json`
- `--page`: target `page_id`
- `--output`: destination path for the generated JSON

Example:

```powershell
python scripts/build_preview_request.py `
  --input references\examples\example-ui-compose-input.json `
  --plan references\examples\example-ui-compose-plan.json `
  --page login `
  --output preview-request.json
```

## Authority rules

- Treat the plan as authoritative for pages, visual direction, component hierarchy, layout, asset usage, behavior, missing assets, assumptions, and warnings.
- Treat the input as authoritative for the mapping from `asset_id` to `source_ref`.
- Preserve every selected `source_ref` unchanged.
- Never use file names or source references to infer visual facts.

## Reference ordering

Select only `asset_usages` belonging to the target page.

Walk them in their existing plan-array order. The first occurrence of each `asset_id` creates one `reference_assets` entry. Later uses of the same asset do not create another reference-image entry.

Assign consecutive order values beginning at 1. Prompt labels must use the same order:

```text
参考图 1
参考图 2
...
```

## Prompt construction

Write natural-language instructions. Do not serialize source JSON into the prompt.

Extract and summarize:

- project context and game description
- visual direction
- target page type, purpose, and states
- target-page component hierarchy
- target-page layout rules
- target-page asset usages
- relevant interactions and navigation intent
- relevant missing assets
- assumptions
- relevant warnings

The prompt must explain:

1. page type and purpose
2. orientation and target resolution
3. each numbered reference image and its use
4. primary parent-child hierarchy
5. critical anchor and position intent
6. relative dimensions
7. features that must be preserved
8. permitted atmospheric or presentation enhancement
9. content and behavior that must not be added or changed
10. that the result is a concept preview, not a pixel-accurate implementation screenshot

Interactions may be described as intent, but must not become extra visible controls in a static preview.

## No-reference mode

When the page has no `asset_usages`:

- emit `reference_assets: []`
- add `NO_REFERENCE_ASSETS` to `warnings`
- explicitly state in the prompt that no reference assets are available
- continue using only the plan's structured visual and composition intent

## Validation and failure

Fail with a structured error and non-zero exit code when:

- target `page_id` does not exist
- an asset usage references an unknown `asset_id`
- an asset usage or layout rule references an unknown page component
- the matching input asset lacks `source_ref`
- input asset IDs are duplicated
- required top-level input or plan structures are missing
- the output path aliases the input or plan path

Do not write an output file after validation failure.

## Hard boundaries

The adapter must not:

- open or resolve `source_ref`
- check whether a referenced path exists
- inspect or reanalyze source images
- infer assets from file names
- call a network
- call an image-generation API
- modify input or plan
- copy upstream implementation-specific fields
- emit credentials, secrets, transport configuration, or provider-specific request parameters

## Output

Write one JSON object conforming to:

- `schemas/preview-request.schema.json`

See:

- `references/examples/example-preview-request.json`
