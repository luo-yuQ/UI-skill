# Lighting Language

## Analyze

Record exposure, ambient character, observable direction, hardness, rim light, bloom, local emission, atmospheric lighting, highlight intensity, and shadow depth.

## Direct observation

Visible highlights, shadows, halos, fog illumination, and emissive regions are observable. Light direction is recorded only when their spatial pattern supports it.

## Describe

Use stable tendencies such as `low_key`, `soft`, or `deep`, plus one concise ambient description. Use `null` or `uncertain` when direction or presence cannot be judged.

## Common errors

- Guessing a precise light direction from ambiguous atmospheric light.
- Confusing bright object color with emitted light.
- Recommending lighting for later assets or UI.

## Do not analyze

Do not prescribe scene lighting, bloom settings, engine values, or image-generation parameters.
