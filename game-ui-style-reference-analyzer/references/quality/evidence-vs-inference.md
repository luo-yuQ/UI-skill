# Evidence vs. Inference

Every material B1 claim must use one of these source labels.

## `observed`

Use for information directly visible in the image: occupied color areas, contours, highlights, objects, composition, or repeated motifs. State what is visible without adding an unsupported cause or identity.

## `inferred`

Use for the model's interpretation of visual evidence: a likely material, theme tendency, mood, era cue, or rendering label. Phrase inference as image-scoped and assign confidence that reflects ambiguity. Do not present inference as confirmed fact.

## `user_provided`

Use for identity, intent, role, or context supplied by the user but not independently established by pixels. Preserve user intent; never relabel it as `observed` merely because it is plausible.

## Uncertainty rule

When neither visual evidence nor user context supports a reliable statement, add an entry to `uncertainties` instead of guessing. A complete output may contain explicit uncertainty; it must not contain fabricated certainty.

## No-purpose rule

Keep `observed`, `inferred`, and `user_provided` statements descriptive. Evidence provenance never permits suitability, recommendation, or usage judgments such as "suitable for," "recommended as," or "can be used as." Store explicitly supplied intent only in `user_intended_use`.

Evidence labels describe the source of a claim, not its importance. Keep statements concise and avoid UI recommendations in every source category.
