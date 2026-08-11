---
name: "stage1"
description: "Run the project's First Stage UI workflow: A1 layout analysis → B1/B2 style analysis → Composer."
---
# Stage 1

Execute the workflow defined in:

`runner/first-stage-runner.md`

First determine whether this is a NEW run or a RESUME of an existing run.

Before a New Run is initialized, split the invocation into two channels:

- `BusinessRequirement`: only the user's original business/design request;
- Runner control: `/stage1`, run selection, initialize-only, stage selection,
  stop-after, resume, and skip-stage instructions.

Preserve the business wording verbatim. Do not translate, summarize, or polish
it. Never put Runner control into `BusinessRequirement`. If the two cannot be
separated reliably, stop instead of contaminating `request.json`.

If the user provides an existing `runs/<run-id>` path, reuse that exact run and DO NOT call the initialization script.

On Resume, `00-input/request.json.user_requirement` is immutable business
input. Do not rewrite it, reconstruct it from the current conversation, or
replace it with the resume instruction. Runner control remains in the current
invocation and `run-manifest.json` only.

Only call:

`runner/scripts/init-stage1.ps1`

when a new run is actually required.

For a New Run, pass the separated business text through the script's
`BusinessRequirement` parameter. The initialization script rejects known Runner
control phrases as a final contamination guard.

Do not create multiple run namespaces for different stages of the same user task.

## A1 Execution Context Boundary

The current TRAE Stage 1 command does not launch A1 as an independent model
invocation. It executes inside the invoking model conversation, so this workflow
provides soft isolation only and must not claim hard context isolation.

When entering A1, construct a dedicated minimal A1 task. Its task inputs are
limited to:

- the current run's layout reference image paths from
  `00-input/input-metadata.json`;
- the corresponding deterministic layout metadata from that file;
- `game-ui-layout-reference-analyzer/SKILL.md`;
- the current A1 schema, required taxonomy/reference files, and validation
  contract.

Do not open `00-input/request.json` while constructing or executing the A1 task.
Do not copy the original `user_requirement`, B style information, Composer
intent, or new-page design instructions into the A1 task prompt. Although the
invoking conversation remains technically visible, treat those items as
unavailable evidence during A1 analysis.

This allowed-input list is also the exact payload boundary for a future
sub-agent or independent A1 invocation. Do not pass conversation history or
`request.json` to that future invocation.

The original `user_requirement` re-enters only at the Composer Input stage,
after A1 and B outputs have passed their gates.
