# Game UI Asset Repairer v0.1 (Stage2-C)

Deterministic repair relation building (C1) and repair input preparation (C1.5)
for assets extracted by Stage2-B (`game-ui-asset-extractor`).

**Scope of v0.1: repair-ready inputs only.** No repair algorithm, no CV
inpainting, no image models, no VLM, no SAM re-runs.

## Pipeline position

```
Stage2-A reviewed-direct-assets.json   (immutable, never modified)
        +
Stage2-B extraction-result.json        (frozen v0.1, never modified)
        ↓
C1  build_repair_relations.py          → repair-relations.json
        ↓
C1.5 prepare_repair_inputs.py          → repair/<target>/
                                             repair-mask.png
                                             repair-working-image.png
                                             repair-input.json
                                       → repair-preparation-result.json
```

## C1 — Repair Relation Builder

- Input: reviewed direct-assets JSON (`bbox_source` authoritative).
- Rule (v0.1, frozen): strict bbox containment —
  `intersection_area == small_bbox_area`. Large bbox = repair target, small
  bbox = occluder. No heuristic thresholds, no label/taxonomy reasoning.
  Geometry statistics (`intersection_area`, `occluder_containment_ratio`,
  `per_occluder`) are recorded for future evaluation only.
- Multiple contained assets aggregate into one relation per target
  (`occluder_asset_ids` list). No parent/child tree is built.
- Output schema: `schemas/repair-relations.schema.json`
  (`repair-relations-v0.1`).
- The reviewed JSON is never written back to.

## C1.5 — Repair Input Preparation

Per relation, for `target <- occluders`:

1. Target RGBA = its Stage2-B extracted asset (current visible state; may
   still contain occluder pixels — expected).
2. Occluder mask = Stage2-B **final postprocessed** segmentation mask
   (`mask_path` from the extraction record). Never the bbox rectangle.
3. Deterministic coordinate mapping (no resize, no VLM):

   ```
   source_x      = occluder_frame.x + occluder_local_x
   target_local_x = source_x - target_frame.x        (Y symmetric)
   ```

   The local frame of any extraction record is `extraction_roi` when present,
   otherwise `final_bbox` (see `repair_geometry.target_frame_from_extraction_record`).
4. `repair_mask = union(projected occluder masks)`, clipped only by the
   target frame boundary. **Since repair-input v0.2** the target's own
   segmentation mask is NOT a repair ownership boundary: an occluder known
   (via the reviewed bbox containment relation) to cover the target owns
   its full projected area inside the target frame. Rationale: Stage2-B
   target masks can legitimately exclude occluded regions (e.g. asset_027
   excludes the potion bottle area), so intersecting with the target mask
   would drop exactly the pixels that need repair (observed: 1142 → 622 px
   on asset_027 <- asset_028). The target mask is still loaded, recorded in
   `target_mask_path`, and size-checked; it is only used for the
   `repair_ratio_of_target_mask` diagnostic.
5. `repair-mask.png`: single-channel binary, WHITE = must repair,
   BLACK = preserve. No soft alpha, no blur, dilation = 0.
6. `repair-working-image.png`: byte-identical copy of the target RGBA with
   RGB set to pure black (0,0,0) inside the repair mask. The black is a
   missing-content placeholder, not asset content. Stage2-B originals are
   never overwritten. Alpha behavior is unchanged (the alpha channel is
   preserved byte-identically, including inside the repair mask).

## Status / diagnostics

Every target is reported with `status` ∈ {`success`, `skipped`, `failed`}
plus an explicit `reason_code` (`target_extraction_result_missing`,
`occluder_mask_file_missing`, `mask_size_mismatch`,
`asset_image_size_mismatch`, `invalid_bbox`, `no_coordinate_overlap`,
`empty_repair_mask`, …). Nothing is silently ignored.

Repair-input diagnostics (v0.2): `repair_pixel_count`,
`repair_ratio_of_target_mask`, `projected_pixels_before_frame_clip`,
`projected_pixels_after_frame_clip`. There are no
`pixels_after_target_intersection` / `pixels_removed_by_target_intersection`
fields — the target-mask intersection no longer exists.

## Production vs experiment boundary

- Production contract: the formal extraction-result.json emitted by
  `game-ui-asset-extractor/scripts/extract_assets.py` (ROI-local or
  bbox-local frames, normalized via `extraction_roi`).
- `experiments/batch_run_adapter.py` converts the frozen
  `20260904_sam_box_only_batch_001` experiment run (tight bbox-local
  winner/filtered masks, per-asset result.json) into that formal contract.
  It is experiment-only and never patched into production code.

## CLI

```bash
# C1
python build_repair_relations.py \
  --reviewed <reviewed-direct-assets.json> \
  --output repair-relations.json

# C1.5
python prepare_repair_inputs.py \
  --reviewed <reviewed-direct-assets.json> \
  --relations repair-relations.json \
  --extraction-result <extraction-result.json> \
  --repair-dir repair/ \
  --result repair-preparation-result.json
```

## Tests

```bash
python -m pytest tests/ -q
```

22 deterministic tests; no SAM checkpoint, no torch, no network.
