# Fork Notes

This checkout is a fork of [`mapterhorn/mapterhorn`](https://github.com/mapterhorn/mapterhorn), 99 commits ahead of `upstream/main` as of 2026-09-06 (the "20 commits" figure below predates section D's Japan bridge work). It was created to process an aerial orthophoto (Freetown, Sierra Leone — see attribution in `pipelines/merge_bundles.py`) through Mapterhorn's tiling pipeline, which upstream is built for elevation (DEM) sources.

Individual commit messages already document root cause and verification for each change; this file groups them by how they relate to upstream.

## A. Generic bug fixes (apply to upstream regardless of orthophoto/RGB use)

These are correctness fixes an upstream user with a plain DEM source would also hit:

- `f9ee614` — `NameError` in `aggregation_tile.py` (missing numpy import) crashed every aggregation item right after its expensive reproject step, silently leaving orphaned tmp folders instead of failing loudly.
- `6758cf0` — `merge_source()` produced an empty coverage polygon for any single-source project (the first file's ogr2ogr copy never got renamed to the layer name the union SQL expects).
- `4bf6e53` — `macrotile_z` had no ceiling relative to a source's native maxzoom; a high-resolution source could make `aggregation_reproject.py` try to materialize a ~256GiB raster per tile. Also fixed `source_polygonize.py`'s mask step to respect the source's real validity mask instead of marking every pixel valid.
- `ef47cc2` — `AGGREGATION_WORKERS=0` (or negative) crashed `Pool(processes=0)` instead of falling back to the documented default.
- `b43c373` — `sort_files_by_proximity()` sorted by the wrong zoom variable, so downsampling could silently process a coarser pyramid level before the finer level it depends on, producing permanently incomplete tiles with no retry.
- `9302d82` — `monitor_progress.py` mislabeled seconds as minutes in its rate display.
- `aa1b2f4` (partial) — `validate_pixels.py` sampled tile coordinates near the world origin instead of the archive's real coordinate range, so it always reported "no tiles found" on real data; `rgb_viewer.html`'s hash-param parser split on every `=` instead of just the first, truncating any value containing one.
- `afae572` — corrected a wrong comment about `bundle.py`'s `child_z<=12` branch (claimed dead code that is actually live for low-zoom downsampling overviews).

## B. Orthophoto/RGB mode (scope question for upstream)

- `5eaa737`, `a510dde`, `aacfd5b` — added an alternate encoding path: Lanczos resampling + RGB WebP encoding, instead of Terrarium elevation encoding. This is a genuine purpose extension, not a bug fix — upstream's stated scope is "public terrain tiles." Whether this belongs upstream as an optional mode, or should stay a specialized fork, is a design question for the maintainer rather than something to just PR.

## C. Freetown/deployment-specific tooling (fork-only, not generalized)

Useful for this deployment but hardcoded to this project or not generically applicable without more work:

- `5a6cd0d`, `732b420`, `0fef43d`, `517ab5e` — parallelism tuning, geographic clustering, PMTiles file-grouping for I/O locality (downsampling-stage performance work, tuned for this dataset's shape).
- `067de4a` — PMTiles validation/repair CLI flags (`--validate`, `--fix`, `--regenerate`).
- `3b7d54f` — geographic proximity processing order, defaulting to Freetown's coordinates (overridable via `CENTER_LAT`/`CENTER_LON`).
- `dc0df21` — `DOWNSAMPLING_OPTIMIZATION.md`, documenting the above four items only (not a full fork overview — see this file for that).
- `aa1b2f4` (remainder) — unattended orchestration scripts (`auto_aggregation.sh`, `auto_downsampling.sh`, `disk_safety_guard.sh`, `monitor_running_aggregation.sh`, `check_progress.py`) for multi-hour unattended runs on this machine.
- `fda4137` — `merge_bundles.py`, a workaround for `go-pmtiles merge` (v1.28.0) panicking despite being documented in `--help`; streams tiles directly via the `pmtiles` Python library instead.
- `5bc2b2d` — attribution fix specific to the Freetown archive's actual source metadata (OpenAerialMap record), not a generic pipeline change.

## D. Japan bridge mission-specific extensions (fork-only, not applicable to a plain-DEM upstream deployment)

Built for [`hfu/mapterhorn-japan-bridge`](https://github.com/hfu/mapterhorn-japan-bridge)'s "1.5-go" staging/regression generation (that repo's `DECISIONS.md` D93-D146) — a structural pipeline rewrite plus a new tile datatype, both scoped to that project's multi-generation, multi-source-tier setup rather than upstream's single-generation plain-DEM model. Unlike sections A-C above (Freetown/2026-07), this batch landed 2026-08-30 through 2026-09-06:

- `62c592e` — `lineage_downsample.py`: majority-vote downsampling for categorical rasters (as opposed to numeric averaging), prep work not yet wired into production at the time.
- `8545a12`, `2cdd5ed`, `291e0c3` — `EMIT_LINEAGE` wired end-to-end: `aggregation_run.py` optionally emits a per-pixel "which source tier won this pixel" category tile (new `lineage_provenance.py`/`lineage_tile.py`) alongside the elevation tile, and `downsampling_run.py`'s `create_tile()` downsamples it via majority vote instead of averaging.
- `8545a12`, `750b237`, `56d3cec` — `pmtiles-store` restructured from a flat, generation-agnostic tree to `{layer}/{datatype}/{generation_id}/{z7bucket}/...` (`layer` = aggregation vs downsampling, `datatype` = elevation vs lineage), closing the D74-D76 class of cross-layer file collisions. `get_pmtiles_folder()` gained required `layer`/`datatype`/`generation_id` parameters; `bundle.py`/`merge_japan_bundles.py` gained matching `*_DATATYPE`/`BUNDLE_GENERATION` env vars; `.done` markers became datatype-scoped, inputs-fingerprinted JSON manifests instead of empty touch files; `remove_dangling_pmtiles.py` rewritten to take an explicit generation and refuse 1-go.
- `0fb8bdb`, `56d3cec` — transitional flat-layout fallback for reading 1-go's pre-restructure data (added, then hard-gated to 1-go only so no other generation can ever alias into it — see the bridge repo's D115/D124 for why this needed two passes).
- `abcdc45` — `publish_cycle.py` hard-disabled (`sys.exit(1)`) once the restructure was found to break its assumptions in a way that would have deleted the live published archive; not yet repaired (bridge repo D115).
- `1b6e4e1`, `ade4bce` — merge/bundle correctness fixes found while auditing the restructure (a coastal-tile erosion-gate regression; refusing to merge a bundle-store with missing pieces).
- `484a86d` — `min_output_zoom=8`: downsampling's own zoom pyramid stops at z8 by design (Hidenori's call) rather than z0, because the bridge's z0-7 has structural deep-ocean gaps meant to be filled by splicing in upstream Mapterhorn's own global z0-7 tiles instead.
- `933d0e9`, `6b11542`, `8b19b17` — pre-launch hardening found during 1.5-go rehearsal: a missing `TMPDIR` override in `downsampling_run.py`, disk-headroom monitoring extended to `pmtiles-store`, `AGGREGATION_WORKERS` default fixed at 3.
- `4b0603e` — `merge_japan_bundles.py` now runs `./pmtiles cluster` on its own output unconditionally, for every datatype, so nobody needs to remember this as a separate runbook step (measured ~19% dedup for elevation, ~83.5% for lineage's categorical data — bridge repo D143/D144).
- `2577a7f` — `lineage_extend_low_zoom.py`: a standalone script extending *only* the lineage pyramid down to z4 (reusing the existing majority-vote downsampler against already-published z8 output) without touching `downsampling_covering.py`/`downsampling_run.py` at all — avoids dragging elevation's 314GB archive into an unrelated low-zoom rebuild (bridge repo D146).

None of this generalizes to a plain-DEM upstream deployment the way section A's fixes do — `datatype`, `generation_id`, and the lineage tile type only exist because the Japan bridge project runs multiple source-tier generations against the same store and wanted a per-pixel provenance layer. Full narrative and rationale: `hfu/mapterhorn-japan-bridge`'s own `DECISIONS.md` D93-D146.

## Status

Published as [`hfu/mapterhorn`](https://github.com/hfu/mapterhorn) (`origin`); `upstream` remote tracks `mapterhorn/mapterhorn`.

Merged upstream's subsequent 2 commits (`93075b4`, `6cdf66b` — Manager/Worker addition + the aggregation stage's `.todo`/`.done` checkpoint refactor) into this fork. One deliberate divergence from that merge: **`pipelines/downsampling_run.py` was kept on this fork's own version** rather than adopting upstream's parallel refactor there, because this fork's dirty-tile-tracking + geographic-proximity processing order is what actually produced the completed Freetown archive and upstream's new approach is untested against the RGB code path. Upstream's `downsampling_covering.py` (merged in unmodified, since this fork never touched it) now also writes `*.csv.todo` marker files as part of its own dirty-tile tracking — this fork's `downsampling_run.py` ignores them and does its own equivalent filtering directly against `*.csv`, so the extra marker files are harmless but redundant. Revisit this if/when adopting upstream's Manager/Worker model.
