---
name: game-ui-prompt-compiler-skill
description: Compile a Composer ui-compose-plan JSON and a B2 style-profile JSON into one English image-prompt.txt in text-only or reference-guided mode for a game UI image model. Use when validated UI structure must become a natural-language image-generation brief while preserving exact counts and layout, optionally making a separately supplied reference image the downstream model's primary style authority, without reading image files or paths, calling an image API, or modifying upstream A/B/Composer outputs.
---

# Game UI Prompt Compiler v0.2

Compile one image prompt from two immutable structured inputs:

```text
ui-compose-plan.json + style-profile.json -> image-prompt.txt
```

## Run the compiler

Use the bundled deterministic script:

```powershell
python game-ui-prompt-compiler-skill/scripts/compile_image_prompt.py `
  --compose-plan <ui-compose-plan.json> `
  --style-profile <style-profile.json> `
  --mode <text-only|reference-guided> `
  --output <image-prompt.txt>
```

Default `--mode` to `text-only` for v0.1 compatibility. Never add or accept a reference-image path argument.

Treat both inputs as UTF-8 JSON text. Read only the two paths supplied on the command line. Never open paths found inside either JSON, including `source_ref`, image paths, asset paths, or historical provenance fields.

## Preserve authority

Apply these rules in order:

1. Preserve Composer page semantics, component hierarchy, exact counts, grid dimensions, required positions, and visible content zones.
2. Translate structural fields into natural UI language; never expose JSON paths, IDs, confidence values, provenance, or debug metadata.
3. Emit English only. Translate supported Chinese visual descriptions into natural English; use descriptive English trait IDs as a deterministic fallback, and never write untranslated CJK text.
4. Remove internal provenance and agent instructions such as "Use A as layout evidence and B as style evidence" instead of presenting them to the image model.
5. Convert engineering labels to visual-generation labels: remove suffixes such as `template`, `component`, `node`, `prefab`, and `prototype` before count and layout descriptions.
6. In `text-only` mode, use B2 `stable` traits first.
7. Use `secondary` traits when Composer adopted them; if Composer has no style-disposition records, include supported secondary traits conservatively.
8. Use a `local` trait only when Composer explicitly adopts it for the selected page or one of that page's components.
9. Omit `conflicting` and `uncertain` traits instead of choosing a side or turning low-confidence evidence into a requirement.
10. In `reference-guided` mode, keep Composer as the structure authority, make the downstream reference image the primary visual style authority, and use B2 only for generic secondary guidance that cannot overpower the reference.
11. Add only restrained, general production constraints that improve readable, front-facing, separable game UI generation.

Do not redesign the page, add functions, change component counts, resolve `missing_assets`, inspect images, call GPT Image, build provider adapters, validate generated images, cut assets, or handle FairyGUI.

## Output contract

Emit English plain text with these sections in `text-only` mode:

```text
GOAL

CANVAS AND PAGE TYPE

COMPOSITION

VISUAL STYLE

HARD REQUIREMENTS

PRODUCTION CONSTRAINTS
```

Insert `REFERENCE USAGE` after `VISUAL STYLE` in `reference-guided` mode. State that the reference controls transferable palette, rendering, material, shape, decoration, and overall visual character only. Explicitly forbid copying its characters, scenes, layout, text, business content, or gameplay functions. Never claim to have observed any specific reference color, shape, or content.

Keep the result like a concise design brief, not a keyword pile. Reinforce exact counts and grids with `exactly`, `must`, and `do not add` language. Keep layout and visible hierarchy in `COMPOSITION`, evidence-backed appearance in `VISUAL STYLE`, and count/position invariants in `HARD REQUIREMENTS`.

## Fail only when compilation is impossible

Fail with a nonzero exit code when either JSON cannot be parsed, no valid page exists, no usable UI structure exists, or no usable style description exists. Do not fail only because `warnings`, `assumptions`, `missing_assets`, or low-confidence fields are present.
