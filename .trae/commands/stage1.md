---
name: "stage1"
description: "Run the project's First Stage UI workflow: A1 layout analysis → B1/B2 style analysis → Composer."
---
# Stage 1

Execute the workflow defined in:

`runner/first-stage-runner.md`

First determine whether this is a NEW run or a RESUME of an existing run.

If the user provides an existing `runs/<run-id>` path, reuse that exact run and DO NOT call the initialization script.

Only call:

`runner/scripts/init-stage1.ps1`

when a new run is actually required.

Do not create multiple run namespaces for different stages of the same user task.