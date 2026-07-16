# Fork Notes

This checkout is a fork of [`mapterhorn/mapterhorn`](https://github.com/mapterhorn/mapterhorn), currently 20 commits ahead of `origin/main`. It was created to process an aerial orthophoto (Freetown, Sierra Leone — see attribution in `pipelines/merge_bundles.py`) through Mapterhorn's tiling pipeline, which upstream is built for elevation (DEM) sources.

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

## Status

Not yet pushed to a public GitHub fork — `origin` still points directly at `mapterhorn/mapterhorn`. See project owner for current plans on publishing and upstream contribution.
