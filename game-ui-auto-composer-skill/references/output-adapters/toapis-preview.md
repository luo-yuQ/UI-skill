# ToAPIs Preview Provider Adapter v0.1

## Purpose

Execute the provider-neutral `preview-request.json` through the confirmed ToAPIs protocol and produce:

- one downloaded preview image
- one redacted result metadata JSON

This is a downstream Provider Adapter. It does not change the UI Composer, compose-plan contract, or provider-neutral Preview Adapter.

## Runtime requirements

- Python 3.10 or newer
- `requests`
- `TOAPIS_API_KEY` environment variable for real requests
- optional `TOAPIS_BASE_URL`; defaults to `https://ai-api.youchu.work`

If needed, install the HTTP dependency without placing credentials on the command line:

```powershell
python -m pip install requests
```

There is intentionally no `--api-key` argument. Never store the key in source files, result JSON, command history, or logs.

## CLI

```powershell
python scripts\toapis_preview_adapter.py `
  --request preview-request.json `
  --asset-root D:\first_ex `
  --output output\login-preview.png `
  --result-json output\login-preview.result.json
```

Required:

- `--request`: input `preview-request.json`
- `--output`: final downloaded image
- `--result-json`: redacted execution metadata

Optional:

- `--asset-root`: base directory for relative `workspace_path` references
- `--poll-interval`: fallback poll interval; default `3`
- `--max-wait`: fallback maximum wait; default `300`
- `--upload-timeout`: upload timeout; default `120`
- `--request-timeout`: submit, poll, and result timeout; default `120`
- `--download-timeout`: image download timeout; default `120`
- `--dry-run`: validate, resolve local paths, and build the payload summary without network calls

Submission response values for `task_status_url`, `poll_interval`, and `max_wait` take priority over CLI fallback values.

## Prompt mapping

The adapter does not send the long mixed-language `preview-request.prompt` directly. It constructs a concise English prompt with these sections:

```text
Project:
...

Page:
...

Purpose:
...

Reference assets:
1. ...
   Role: ...
   Preserve:
   - ...

Layout:
- ...

Constraints:
- ...
- This is a concept preview, not an engineering screenshot.
```

Mappings:

- Project: `source.project_name`
- Page: `source.page_id`
- Purpose: concise page purpose from `composition_requirements`
- reference numbering: sorted `reference_assets.order`
- Role: concise interpretation of each reference `usage`
- Preserve: matching numbered entries in `preserve_requirements`
- Layout: visible placement and relative sizing from `composition_requirements`
- Constraints: no account form, social login, menus, extra panels, unplanned buttons, features, or regions

Clicks, navigation, loading, component IDs, Schema relationships, and other invisible engineering details are not turned into visible controls.

## Source reference resolution

References are processed in ascending `order`. This order is shared by:

```text
Prompt reference number = reference_assets.order = images[] position + 1
```

Resolution rules:

1. A complete `http://` or `https://` value is used unchanged and is not uploaded, regardless of reference type.
2. `workspace_path` values are local files.
3. A relative local value resolves under `--asset-root`; omitting the root is an error.
4. An absolute local value is used directly.
5. Local paths must exist and be ordinary files.
6. `attachment_id`, `asset_uri`, and `opaque_id` are rejected unless their value is already a complete public URL.
7. Identical stable `source_ref` values are uploaded once per run and reused without changing business ordering or item count.
8. Neither the image output nor result JSON may overwrite a resolved local reference file.

Dry-run checks local file existence but uses a redacted `<local-upload-required:filename>` placeholder. It never invents a public URL.

## Confirmed ToAPIs protocol

### Upload

```text
POST /api/upload
Authorization: Bearer <environment key>
multipart/form-data field: file
```

The adapter lets `requests` generate the multipart boundary and does not manually set `Content-Type`. It reads the uploaded URL from top-level `data["url"]`. Complete public URLs are preserved without base-URL concatenation.

### Submit

```text
POST /v1/images/generations
```

Exact generated body:

```json
{
  "model": "gpt-image-2",
  "prompt": "Project:\n...",
  "type": "image",
  "images": [
    "ordered reference URL 1",
    "ordered reference URL 2"
  ],
  "size": "1024x1536",
  "n": 1,
  "response_format": "url"
}
```

### Poll

Use the returned `task_status_url`, or fall back to:

```text
GET /v1/tasks/{task_id}/status
```

Continue for `pending` and `in_progress`. Stop for `completed`. Treat `failed` as an error. Stop with `POLL_TIMEOUT` after the effective maximum wait.

### Fetch result

```text
GET /v1/tasks/{task_id}/result
```

Image URL extraction priority:

1. `items[0].url`
2. `data.result.data[0].url`

If a completed result contains neither, fail with `stage=fetch_result` and `error_code=RESULT_IMAGE_URL_MISSING`.

### Download

The image is streamed into a temporary file in the destination directory. The adapter atomically replaces `--output` only after the full download completes. A failed download does not leave a partial target disguised as a complete image.

## Dry-run

```powershell
python scripts\toapis_preview_adapter.py `
  --request preview-request.json `
  --asset-root D:\first_ex `
  --output output\login-preview.png `
  --result-json output\login-preview.result.json `
  --dry-run
```

Dry-run writes result metadata and a structured stdout summary. It does not upload, submit, poll, download, create the output image, or require `TOAPIS_API_KEY`.

## Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | success or successful dry-run |
| 2 | input, validation, path conflict, or source-resolution error |
| 3 | upload error |
| 4 | generation submission error |
| 5 | polling failure or timeout |
| 6 | result fetch or image-URL extraction error |
| 7 | download or output-write error |
| 8 | missing dependency or runtime configuration |

Expected failures produce one structured JSON summary on stdout and, when path-safe, the same failure metadata in `--result-json`. Provider responses are deliberately cropped; API keys, Authorization headers, binary image data, and HTTP library objects are never stored.

## Result example

See `references/examples/example-toapis-result.json`.
