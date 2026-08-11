---
name: game-ui-image-provider-adapter
description: Generate one game UI preview image from an existing UTF-8 image-prompt.txt through the ToAPIs gpt-image-2 provider, then write preview.png (or the provider's actual image extension) and a minimal redacted result.json. Use when Prompt Compiler output must be sent unchanged to the configured image provider for a pure text-to-image preview without reference images, source_ref resolution, upstream JSON reads, prompt rewriting, visual scoring, or automatic regeneration.
---

# Game UI Image Provider Adapter v0.1

Convert one immutable text prompt into one provider-generated preview:

```text
image-prompt.txt -> ToAPIs gpt-image-2 -> preview image + result.json
```

## Run

Set credentials only through the environment:

```powershell
$env:TOAPIS_API_KEY = "<secret>"
# Optional: $env:TOAPIS_BASE_URL = "https://ai-api.youchu.work"

python game-ui-image-provider-adapter/scripts/generate_preview.py `
  --prompt <run>/image-prompt.txt `
  --output-dir <run>/preview
```

Use `--model` only for a provider-supported model override. Use `--size` only for a provider-supported explicit size override. Never accept an API key as a CLI argument or write credentials, authorization headers, provider response bodies, or image URLs to `result.json`.

## Preserve the prompt

Read only the path passed to `--prompt`, using UTF-8 with optional BOM handling. Remove trailing whitespace only. Pass the remaining text byte-for-text as the provider prompt value. Do not append quality keywords, translate, summarize, optimize, repair, or redesign it.

Infer a requested canvas only from text such as `Compose for a 1920 x 1080 px canvas.` Map landscape, portrait, and square targets to the closest supported ToAPIs size. Record both values in `result.json`; do not resize the returned image.

## Verified ToAPIs protocol

Use system `curl.exe` on Windows, falling back only to a system `curl` executable name on other hosts. Never use Python `requests` as a transport fallback. Invoke curl with an argument array and `shell=False`. Submit `type: "text"` with `images: []` to `POST /v1/images/generations`, placing the UTF-8 JSON payload in a temporary file and sending it with `--data-binary @file`. Read the submit task ID in priority order from top-level `id`, top-level `task_id`, then `data.id`. Poll `GET /v1/tasks/{id}/status` and read `task_status`; after completion fetch `GET /v1/tasks/{id}/result`. Prefer `items[0].url` and retain compatibility with `data.result.data[0].url`. Remove every temporary request file after use.

## Boundaries

Never read, resolve, inspect, or upload:

- reference images or reference manifests;
- `source_ref` values;
- A1/B1/B2 outputs;
- `ui-compose-plan.json`;
- `style-profile.json`;
- `preview-request.json`.

Do not modify upstream Skills, score the output, retry based on visual quality, rewrite the prompt, regenerate automatically, cut assets, or implement FairyGUI.

## Result contract

Write UTF-8 `result.json` for success or ordinary failure. Keep it minimal and redacted. On success record schema version, status, provider, model, prompt source, requested canvas, actual provider size, and output image name. On failure record schema version, status, provider, model, error type, and a sanitized message. Return a nonzero exit code on failure.

Prefer `preview.png`. If the provider returns JPEG or WebP bytes, preserve the true format and extension instead of transcoding.
