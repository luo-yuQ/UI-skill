# Stage2-A Strategy Contract v0.3

Use this reference only after completing asset decomposition. Decide strategy independently for every parent and child candidate. Do not infer strategy from `semantic_type` or from an issue name alone.

## Contents

- [Four strategies](#four-strategies)
- [Decision procedure](#decision-procedure)
- [Consistency rules](#consistency-rules)
- [Issues are diagnostic](#issues-are-diagnostic)
- [Required examples](#required-examples)

## Four strategies

Use exactly one of these four values.

### `direct_crop`

Use when all pixels required for the target asset exist and the candidate bbox already contains the correct reusable rectangular asset. No foreground/background separation, mask, alpha isolation, pixel recovery, or generation is needed.

Set `should_extract: true`.

Typical cases include a complete rectangular control, an independently rendered rectangular asset, or any candidate whose bbox itself is the desired final crop.

### `foreground_extract`

Use when all pixels required for the target asset exist in the screenshot, but the bbox also contains surrounding pixels that are not part of the asset. The asset can plausibly be isolated from existing pixels through foreground/background separation, mask, and alpha.

Set `should_extract: true`.

Treat this only as isolation of an existing complete foreground. It does not mean filling, redrawing, inpainting, recovering occluded pixels, removing content baked into the target, or guessing missing boundaries. It may have an empty `issues` array because the strategy already expresses the need for transparent foreground separation.

### `advanced_required`

Use when the candidate should become an independent image asset, but the screenshot lacks pixels required for the ideal target, or the target is fused with other content beyond reliable foreground separation in current Stage2.

Set `should_extract: true` and include at least one issue.

This value records an unsupported condition; it is not a repair or generation plan. Stage2 never generates missing pixels. Typical cases include an occluded or partially visible asset, an empty panel surface hidden by child content, a text-free button surface whose pixels contain baked dynamic text, or an unreliably fused neighbor.

### `do_not_extract`

Use when the element is recognized but should not become an independent image asset, such as ordinary runtime text, non-reusable layout structure, or an element retained only for semantic record.

Set `should_extract: false`.

## Decision procedure

Apply these questions in order to each candidate:

1. Should this candidate become an independent image asset?
   - No: use `do_not_extract`.
   - Yes: continue.
2. Are all pixels required by the intended reusable asset present in the screenshot?
   - No: use `advanced_required`.
   - Yes: continue.
3. Is the candidate bbox itself already the correct rectangular asset?
   - Yes: use `direct_crop`.
   - No: continue.
4. Can the complete target foreground be separated from surrounding existing pixels?
   - Yes: use `foreground_extract`.
   - No or unresolved: use `advanced_required`.

```text
should become an image asset?
|-- no  -> do_not_extract
`-- yes
    target pixels complete?
    |-- no  -> advanced_required
    `-- yes
        rectangular crop sufficient?
        |-- yes -> direct_crop
        `-- no
            foreground separable?
            |-- yes -> foreground_extract
            `-- no / unresolved -> advanced_required
```

Complete decomposition before this procedure. Do not assign `advanced_required` to a large parent and then omit its children. Evaluate every parent and child separately.

## Consistency rules

- `should_extract: false` requires `do_not_extract`.
- `do_not_extract` requires `should_extract: false`.
- `direct_crop` requires `should_extract: true`.
- `foreground_extract` requires `should_extract: true`.
- `advanced_required` requires `should_extract: true` and at least one issue.
- `foreground_extract` does not require an issue.

Write `reason` as a concise natural-language QA note. Never use it as machine input. Machine decisions may depend only on `semantic_type`, `bbox`, `should_extract`, `strategy`, `issues`, and optional `source_ref`.

## Issues are diagnostic

Use only these structured issue values:

- `text_baked_in`
- `complex_background`
- `occluded`
- `partial_visibility`
- `merged_with_neighbor`
- `uncertain_boundary`

Do not implement a fixed `issue -> strategy` mapping. An issue describes the observed visual condition; strategy describes whether and how the intended asset can be obtained from pixels that already exist.

### `complex_background`

Treat a complex background as an important signal to consider `foreground_extract`, not as an automatic reason for `advanced_required`. Use `foreground_extract` when the complete subject exists and only surrounding background must be removed. Use `advanced_required` when the background and target are fused beyond reliable separation or target pixels are missing.

Positive: a complete crystal cluster on a patterned card background can use `foreground_extract` with `issues: ["complex_background"]`.

Counterexample: a glow whose boundary and pixels are inseparably blended with the card may use `advanced_required` with the same issue.

### `merged_with_neighbor`

Distinguish a neighbor touching or surrounding a complete target from a neighbor covering or destroying required target pixels.

- Use `foreground_extract` when the complete target remains present and foreground separation can plausibly isolate it from the neighbor.
- Use `advanced_required` when another element covers target pixels or the two elements share unrecoverable pixels. An empty panel covered by icons, text, and artwork is the canonical case.

### `text_baked_in`

First define the intended asset. If fixed text is part of the desired complete artwork, the candidate may still use `direct_crop` or `foreground_extract`. If the desired asset is a reusable text-free button or panel surface and text has replaced those surface pixels, use `advanced_required` because the required clean pixels do not exist in the screenshot.

Never expand a candidate bbox merely because text is present. Record the issue on the already identified candidate.

### `occluded` and `partial_visibility`

Use `advanced_required` when the intended asset is complete but the screenshot contains only its visible portion. State that the screenshot lacks sufficient pixels for the ideal asset. Do not describe Stage2 as repairing or generating the missing portion.

### `uncertain_boundary`

Use `foreground_extract` when the subject pixels are complete and foreground separation has a reasonable boundary basis despite a complex background. Use `advanced_required` when the asset boundary itself cannot be defined reliably. Keep the issue diagnostic rather than mapping it automatically.

## Required examples

### A. Crystal illustration on a card background

All crystal pixels exist; only the surrounding card must be removed.

```json
{"semantic_type":"illustration","should_extract":true,"strategy":"foreground_extract","issues":["complex_background"]}
```

### B. Empty card panel covered by children

The desired empty surface is covered by artwork, text, and a button, so its required pixels are absent.

```json
{"semantic_type":"panel","should_extract":true,"strategy":"advanced_required","issues":["merged_with_neighbor"]}
```

### C. Price button with baked dynamic price

The intended asset is the reusable text-free button surface, whose pixels are replaced by price text.

```json
{"semantic_type":"button","should_extract":true,"strategy":"advanced_required","issues":["text_baked_in"]}
```

If the intended asset were instead the complete fixed-price artwork exactly as shown, `direct_crop` or `foreground_extract` could be correct; the issue string alone does not decide.

### D. Independent rectangular UI element

The bbox is already the complete reusable asset.

```json
{"semantic_type":"decoration","should_extract":true,"strategy":"direct_crop","issues":[]}
```

### E. Runtime text

The text should remain runtime content rather than an image asset.

```json
{"semantic_type":"text","should_extract":false,"strategy":"do_not_extract","issues":[]}
```
