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
3. Build the hard-requirement ledger and target semantic.
4. Read A as optional layout evidence.
5. Read B as optional classified style evidence.
6. Select relevant evidence without forcing citations.
7. Label layout origins as `layout_reference`, `user_requirement`, or `composer_derived`.
8. Label style origins as `style_reference`, `user_requirement`, or `composer_derived`.
9. Build the target tree, layout, visual direction, and required interactions.
10. Derive generation constraints.
11. Emit one candidate and stop.

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
