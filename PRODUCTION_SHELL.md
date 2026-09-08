# Production Shell — Direct Asset Pipeline (Phase 5, frozen v0.1)

Frozen end-to-end production chain for the game-UI asset pipeline. Each stage
consumes only the frozen contract of the previous stage. Human review is the
only interactive gate; everything else is deterministic given its inputs.

## Chain order (frozen, do not reorder)

```text
Raw UI screenshot
  |
  v
[Stage 1] Text Cleaner  (game-ui-text-cleaner)
  |    ui_text_extractor.py -> {stem}_texts.json
  |    ui_vlm_region_mask.py --texts-json --image --output-dir
  |    ui_image_clean_repair.py --source-image --mask-overlay --output-dir
  v
Clean UI (clean.png)
  |
  v
[Stage 2-A1] Direct Asset Discovery  (game-ui-asset-analyzer)
  |    direct_asset_discovery.py --image clean.png --output-dir <run>
  |    VLM: Chat Completions, strict JSON Schema, max_tokens=12000
  v
direct-assets.json  (schema 0.1, IMMUTABLE - human review never edits this)
  |
  v
[Stage 2-A2] Asset Admission  (game-ui-asset-analyzer)
  |    asset_admission.py --image clean.png --candidates-json direct-assets.json \
  |        --output-dir <run>
  |    VLM: KEEP/DROP gate only, no geometry changes
  v
accepted-assets.json
  |
  v
[Stage 2-A3] Human Review  (interactive, out of pipeline process)
  |    Human edits bbox / KEEP-DROP / adds manual assets
  |    Output: review-overrides.json (direct-asset-review-overrides-v0.1)
  v
[Stage 2-A4] Apply Review  (game-ui-asset-analyzer, deterministic)
  |    apply_direct_asset_review.py --assets-json direct-assets.json \
  |        --overrides-json review-overrides.json \
  |        --output-json reviewed-direct-assets.json
  v
reviewed-direct-assets.json  (direct-assets-reviewed-v0.1)
  |    bbox_source is AUTHORITATIVE for all downstream stages.
  v
[Stage 2-B0] Build Extraction Request  (game-ui-asset-extractor, deterministic)
  |    build_extraction_request.py --reviewed-json reviewed-direct-assets.json \
  |        --output-json extraction-request.json [--extraction-mode direct_crop]
  |    frozen mapping v0.1:
  |      asset_id        <- id            (validated, never rewritten)
  |      asset_type      <- taxonomy      (enum member, else "unknown")
  |      final_bbox      <- bbox_source   (byte-for-byte)
  |      extraction_mode <- default "direct_crop" (global CLI override only)
  v
extraction-request.json  (schema 0.1)
  |
  v
[Stage 2-B1] Extract Assets  (game-ui-asset-extractor, deterministic)
  |    extract_assets.py --request extraction-request.json --output-dir <dir> \
  |        [--backend pillow|sam1_vit_b]
  v
assets/<asset_id>.png + extraction-result.json
  |
  v
[Stage 2-C] Repair  (game-ui-asset-repairer, deterministic)
  |    build_repair_relations.py -> repair-input.json (per asset)
  |    repair_asset_cv.py --repair-input repair-input.json --output-dir <dir>
  |    Frozen algorithm: linear surface fit + 5% proportional dilation +
  |    ring consensus alpha. TELEA/NS and quadratic fits are forbidden.
  v
repaired RGBA assets
  |
  v
[Stage 2-E] Recompose  (game-ui-asset-analyzer experiments / Stage0)
       ui_recompose_poc.py (bbox_source authoritative)
```

## VLM boundaries (the ONLY stages allowed to call a model)

| Stage | Script | Transport | Model contract |
|---|---|---|---|
| Stage 1 text mask | `ui_vlm_region_mask.py` | Chat Completions (local client) | region plan JSON |
| Stage 1 image repair | `ui_image_clean_repair.py` | Image2 provider | pixel repair |
| Stage 2-A1 discovery | `direct_asset_discovery.py` | Chat Completions (`chat_completions_client.py`) | strict JSON schema, `max_tokens=12000` |
| Stage 2-A2 admission | `asset_admission.py` | Chat Completions (`chat_completions_client.py`) | strict JSON schema, `max_tokens=12000` |

Every other stage is deterministic JSON/NumPy/Pillow code. Network calls
anywhere else are forbidden.

## NO_OVERRIDE invariants (frozen v0.1)

1. **direct-assets.json is immutable.** All human corrections go into
   `review-overrides.json`; `reviewed-direct-assets.json` is produced only by
   the deterministic apply step.
2. **`bbox_source` is authoritative.** Stage2-B `final_bbox` is a byte-for-byte
   copy; no stage may scale, shift, snap, or re-derive it.
3. **No stage re-discovers or re-crops assets.** Discovery happens exactly
   once (Stage 2-A1); extraction happens exactly once (Stage 2-B1).
4. **Dropped assets stay dropped.** Assets absent from
   `reviewed-direct-assets.json` never reach the extraction request.
5. **Repair never inflates scope.** Stage2-C runs the frozen CV algorithm
   (`cv-repair-result-v0.1`) on per-asset `repair-input.json`; no VLM, no
   TELEA/NS inpainting, no quadratic surface fits.

## One-time golden chain run (evidence)

`runs/20260902_direct-asset-discovery-007-production-client/` contains a real
end-to-end run of the VLM stages plus the deterministic bridge outputs. The
Phase 5 bridge check `e2e-bridge-check/extraction-request.json` was produced by
`build_extraction_request.py` from that run's `reviewed-direct-assets.json`
(30 assets, `validate_request` clean, bboxes byte-identical to `bbox_source`).

## Regression tests

- `game-ui-asset-extractor/tests/test_build_extraction_request.py` — builder
  contract (16 tests).
- `tests/test_e2e_contract_freeze.py` — deterministic chain freeze
  (apply_review -> build_request -> extract, inline fixtures, zero network).
