# Composer v2.1.1 Workflow

## One-pass Composer responsibility

```text
validated A/B + user requirement
→ design synthesis
→ candidate plan
→ END
```

1. Consume one validated immutable input.
2. Parse explicit user requirements.
3. Split business/count facts, explicitly locked positions, and soft position preferences.
4. Build the hard-requirement ledger and target semantic; leave soft positions null.
5. Extract A major regions, relationships, hierarchy, approximate proportions, and repeat directions into a layout skeleton.
6. Map requested business components into that skeleton. Locked user positions override A; A overrides soft positions.
7. Record adopted/adapted/ignored disposition for every high-confidence A major region.
   Map a central primary action region to a separate lower-central action band, not to the right auxiliary rail; keep the central content dominant and the right rail narrow. An unrelated bottom navigation band may remain ignored with an explicit rationale.
8. Read B as optional classified style evidence.
9. Label layout origins as `layout_reference`, `user_requirement`, or `composer_derived`.
10. Label style origins as `style_reference`, `user_requirement`, or `composer_derived`.
11. Build the target tree, layout, visual direction, and required interactions.
12. Check vertical/row/grid repeat consistency against design intent and A direction.
13. Derive generation constraints.
14. Emit one candidate and stop.

Composer does not self-validate by repeatedly rewriting its output.

## Deterministic handoff

After generation, Python builds the Evidence Registry directly from the input:

```text
A JSON → valid A IDs + type/path
B JSON → valid B trait IDs + dimension/classification/path
candidate plan
→ deterministic validator
→ PASS / FAIL
```

Only reference origins require registry evidence. User and composer-derived layout decisions use empty source IDs; user and composer-derived style decisions use null trait/classification.

The validator reports exact errors and never guesses replacements or runs a repair loop.

```powershell
python scripts/validate_plan.py <candidate-plan.json> --input <input.json>
```

Hard-requirement preservation and B classification rules from V2.1 remain active.

## Known issue

Required-position checking does not yet fully inherit left/right placement from parent-relative layouts. Leave this for a focused later change.
