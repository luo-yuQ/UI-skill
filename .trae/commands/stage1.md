---
name: "stage1"
description: "Run the project's First Stage UI workflow: A1 layout analysis → B1/B2 style analysis → Composer."
---
# Stage 1

This command is a thin entry point. Workflow selection is deterministic code,
not an LLM judgment.

1. Pass only the current invocation text to:

   ```powershell
   python runner/scripts/parse-stage1-invocation.py --text "<current invocation>"
   ```

2. If the parser exits non-zero, report its JSON error and stop. Do not inspect
   conversation history or `runs/` to recover or guess a run.
3. Pass the successful parser JSON unchanged to the workflow contract in
   `runner/first-stage-runner.md`.

For `mode = new`, initialize exactly one run with
`runner/scripts/init-stage1.ps1`, passing only the parser's
`user_requirement` as `BusinessRequirement`. For `mode = resume`, use only the
parser's exact `run_path`, do not initialize a run, and do not write
`00-input/request.json.user_requirement`.

Honor `stage_control.stop_after` only at the supported validated stage boundary.
The command must not reinterpret, supplement, or override parser fields.

The execution-context boundary, stage dependencies, validators, and output paths
are defined only in `runner/first-stage-runner.md`.
