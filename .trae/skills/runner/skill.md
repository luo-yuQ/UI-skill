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

## Important

Runner decides WHEN and WHERE.

Individual Skills decide HOW and WHAT.