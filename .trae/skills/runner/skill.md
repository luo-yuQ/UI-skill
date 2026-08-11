# Runner

## Purpose

Execute predefined project workflows.

This skill performs workflow orchestration. It does not redefine the internal behavior of A1, B1, B2, or Composer.

## First Stage Workflow

When Stage 1 is invoked:

1. Read the repository workflow definition:

   `runner/first-stage-runner.md`

2. Treat that file as the authoritative First Stage workflow contract.

3. Execute the workflow defined there, including:
   - run workspace creation
   - original input persistence
   - stage execution order
   - required output paths
   - validation gates
   - run manifest updates
   - stop-after behavior

4. Before entering each stage, read that stage's current `SKILL.md`.

5. Required workflow files must actually be written to the repository.

6. Do not replace execution with a natural-language summary.

7. If the user requests stopping after a stage, stop only after that stage's required files have been written and the run manifest has been updated.

## Business Input and Runner Control

For a New Run, preserve two separate channels:

- business input: the user's verbatim business/design requirement;
- Runner control: command name, run path, initialize/resume choice, selected
  stages, stop-after, and skip instructions.

Call `runner/scripts/init-stage1.ps1` with `BusinessRequirement` containing only
the business input. Do not include `/stage1`, initialize-only, resume, stage
selection, stop-after, or “do not execute Composer” language. Keep control in
the invocation and manifest state.

After initialization, `00-input/request.json.user_requirement` is immutable for
the lifetime of that run unless the user explicitly requests a business-input
change. On Resume, never write the `user_requirement` field, never derive a
replacement from the current message, and never pass resume control to Composer
as a requirement. Deterministic input sync may still update only the two
reference-path arrays defined by the Runner contract.

## A1 Task Construction

The current TRAE Runner does not create a separate model context for A1. A1 is
therefore soft-isolated within the current invocation.

Immediately before A1:

1. Run `runner/scripts/sync-stage1-inputs.py --run <run-path>`.
2. Use `00-input/input-metadata.json`, not `request.json`, to resolve the layout
   reference paths and deterministic metadata.
3. Construct a minimal A1-only task containing exactly:
   - the selected layout reference image or images;
   - the matching `layout-*` metadata records;
   - `game-ui-layout-reference-analyzer/SKILL.md`;
   - the current A1 schema;
   - only the taxonomy, reference, workflow, and validation files required by
     the current A1 Skill.
4. Do not read `00-input/request.json` while constructing or executing A1.
5. Do not include or quote the original `user_requirement`, B style evidence,
   Composer information, or instructions describing how the new target page
   should be designed.
6. Analyze only evidence supported by the allowed A1 inputs.

The invoking conversation may still contain the user's design request. This is
not hard isolation: the Runner must ignore that request for A1 and must not use
it as evidence, focus guidance, or a source of semantic conclusions.

If a future Runner can launch an independent sub-agent/model invocation, pass
only the inputs listed above and omit conversation history plus `request.json`.
That future invocation boundary can provide hard context isolation without
changing the A1 contract.

The original `user_requirement` remains intact in `00-input/request.json` and
may be read again only when building Composer Input.

## Important

Runner decides WHEN and WHERE.

Individual Skills decide HOW and WHAT.
