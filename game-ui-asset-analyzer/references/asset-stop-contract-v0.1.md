# Stage2-A Asset / Stop Contract v0.1

Status: **FROZEN**. Future behavior changes require a new version.

This is a deterministic engineering contract for deciding whether the current node is a recursive leaf. It is not a VLM strategy: do not create a stop prompt, agent, skill, or model call.

## Terminal state

`terminal` means that the current node cannot or should not recurse further.

| Evidence | Deterministic result |
| --- | --- |
| Router `node_role: asset` | `node_role: asset`, `terminal: true`, `next_action: stop`, `requires_router: false` |
| Router `node_role: structural_group` | `terminal: false`, `next_action: structural_split`, `requires_router: false` |
| Router `node_role: repeated_group` | `terminal: false`, `next_action: expand_instances`, `requires_router: false` |
| Router `node_role: component_instance` | `terminal: false`, `next_action: semantic_decompose`, `requires_router: false` |

Never call a VLM after a valid `asset` role merely to confirm the stop decision.

## Provenance shortcuts

- A child `produced_by: semantic_decompose` with a taxonomy from the frozen ten-category semantic-decomposition schema is directly an `asset`: stop without calling the Router again. A missing or invalid taxonomy is a contract error.
- A child `produced_by: expand_instances` is directly a `component_instance`: continue with `semantic_decompose` without calling the Router again.
- A child `produced_by: structural_split` is not terminal by provenance alone. Without an existing `node_role`, return `terminal: false` and `requires_router: true`; do not invent a role or `next_action`.

If provenance implies a role that conflicts with a supplied `node_role`, fail explicitly. Do not silently choose one input. Do not introduce an `unknown` role.

Use `scripts/resolve_terminal_state.py`. It reuses the frozen `ROLE_ACTION_MAP` from `scripts/validate_node_route.py` and reads the frozen taxonomy directly from `schemas/semantic-decomposition.schema.json`. Its output conforms to `schemas/asset-stop-result.schema.json`. It does not read images, call a VLM, traverse children, or execute the returned action.

## Deferred

`retain_composite` and the composite asset retention policy are deferred to later asset-retention or Stage2-B extraction planning. They are not part of Asset / Stop Contract v0.1.

Recursive Runtime, traversal, queues, persistence, child crops, depth limits, cycle guards, retries, and verifiers remain unimplemented.
