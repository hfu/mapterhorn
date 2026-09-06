# Downsampling Optimization Report

**Date**: 2026-07-12  
**Status**: 🚀 In Production (Freetown deployment; see 2026-09 update below for
what changed for the Japan bridge project)

## 2026-09 update (Japan bridge project, `mapterhorn-japan-bridge` DECISIONS.md D93-D146)

The report below is a snapshot from the original Freetown deployment and is
kept as-is for that history. Running this same `downsampling_run.py` for
`hfu/mapterhorn-japan-bridge` since added several things this report
predates:

- **`pmtiles-store` now requires `generation_id`** (plus `layer`/`datatype`)
  — the flat `pmtiles-store/{z-x-y}/{z-x-y}-{child_z}.pmtiles` layout this
  report's "File Organization" section shows is specific to a single,
  never-restructured generation. See `FORK_NOTES.md` section D and
  `pipelines/README.md`'s fork note on `get_pmtiles_folder()`.
- **`DOWNSAMPLING_DATATYPE`** (`elevation` default, `lineage`) — a second,
  categorical tile type (per-pixel "which source tier won"), downsampled by
  majority vote instead of the 2×2 averaging this report describes. Building
  both means running `downsampling_run.py` twice, once per datatype.
- **`DOWNSAMPLING_STRICT`** (`0` off / `1` on, not mentioned below) — when
  on, an item with any missing child tile is skipped entirely (no `.done`,
  retried next run) instead of building from whatever children exist. Japan
  bridge production runs set this to `1`.
- **`PRIORITY_MODE=quadrans`** — an alternative to this report's
  Freetown-centered `CENTER_LAT`/`CENTER_LON` proximity sort, ordering by
  `utils.japan_quadrans_of()`'s North/South/East/West quadrant instead.
  Order only, no effect on the result; Japan bridge runs use this mode, so
  `CENTER_LAT`/`CENTER_LON` are not in effect there.
- **`.done` markers are no longer empty touch files** — since D124 they are
  `mjb-done-manifest/1` JSON, scoped per datatype and fingerprinted against
  their inputs, so a repaired child correctly invalidates the overviews
  built from it. The "Remove `.done` markers to restart" troubleshooting tip
  below still works mechanically (delete-and-reprocess), but treat any
  `.done`'s mere *existence* as "once succeeded", not "still valid" — see
  `mapterhorn-japan-bridge`'s `PIPELINE_DESIGN.md` §6.
- **The PMTiles validation/auto-repair feature below is not generation-aware
  — use with care.** `check_and_fix_pmtiles()`'s default `glob('**/*.pmtiles')`
  against `pmtiles-store` crosses every generation, layer, and datatype at
  once, which is exactly the class of cross-generation mixup that caused a
  real data-loss incident on this project (`mapterhorn-japan-bridge`
  DECISIONS.md D74-D76). Confirmed still unscoped as of 2026-09-06 — do not
  run `--fix` against a `pmtiles-store` holding more than one generation
  without scoping it first.
- **`min_output_zoom=8`**: the zoom pyramid downsampling builds now stops at
  z8 by design (not z0) for `elevation`, because Japan bridge's own z0-7 has
  structural deep-ocean gaps meant to be filled by splicing in upstream
  Mapterhorn's own global z0-7 tiles instead. `lineage` has no such external
  z0-7 counterpart to splice in, so a separate one-off script
  (`lineage_extend_low_zoom.py`, D146) extends *only* lineage's pyramid down
  to z4 without touching this script or its covering step.

## Overview

Mapterhorn downsampling pipeline optimization focusing on:
- **Performance**: Parallelism tuning + geographic clustering
- **Reliability**: PMTiles validation & auto-repair
- **Usability**: Geographic-center-based processing priority

## Optimizations Implemented

### 1. Parallelism Optimization (Worker Count)

**Problem**: Default parallelism caused CPU bottleneck (90%+ usage)

**Solution**: 
- Base: 3 workers (conservative)
- Current: 5 workers (optimized for 8-core M-series Apple Silicon)
- Control: `DOWNSAMPLING_WORKERS` environment variable

```bash
# Use default (5 workers)
python downsampling_run.py

# Override
DOWNSAMPLING_WORKERS=8 python downsampling_run.py
```

**Expected Impact**: 20-30% throughput improvement  
**Actual Gain**: ~2x speedup (13.7 → 6.7 min/CSV)

---

### 2. Geographic Clustering (Cache Optimization)

**Problem**: Random disk access pattern caused L3 cache misses

**Solution**:
- Sort parent tiles by geographic proximity: `(x, y)` coordinate ordering
- Clusters reads within same PMTiles file regions
- Improves CPU cache hit rate + OS disk I/O batching

**Implementation**:
```python
def sort_parents_by_geographic_proximity(parents):
    return sorted(parents, key=lambda p: (p.x, p.y))
```

**Expected Impact**: 15-20% throughput improvement

---

### 3. Geographic Proximity-Based Processing Order

**Problem**: Processing starts from Z0 (global) → Z15 (Freetown detail) is inefficient

**Solution**:
- Prioritize high-zoom-level tiles near specified geographic center
- Default center: **Freetown, Sierra Leone** (8.465°N, 13.234°W)
- Processing order: Z15 (closest first) → outward expanding radius
- Ensures high-value imagery completes first

**Implementation**:
```bash
# Default: Freetown
python downsampling_run.py

# Custom center
CENTER_LAT=0 CENTER_LON=0 python downsampling_run.py
```

**Constants** (environment-variable overridable):
- `DEFAULT_CENTER_LAT = 8.465`  
- `DEFAULT_CENTER_LON = -13.234`

**Processing Priority**:
1. 15-15179-15610-20 (Freetown center)
2. 15-15179-15611-20 (adjacent)
3. 15-15178-15610-20 (adjacent)
4. ... (expanding outward)
5. 14-* (lower zoom levels)

---

### 4. PMTiles Validation & Auto-Repair

**Problem**: Incomplete/corrupted PMTiles files could corrupt downsampling

**Solution**:
- Validate all existing PMTiles files before/during processing
- Auto-detect and remove broken files
- Regenerate broken files automatically

**Commands**:

```bash
# Validate all files (dry-run, show issues)
python downsampling_run.py --validate

# Auto-fix broken files
python downsampling_run.py --fix

# Preview changes without applying
python downsampling_run.py --fix --dry-run

# Selective regeneration
python downsampling_run.py --regenerate 15-15179-15610-20
```

**Validation Criteria**:
- ✅ File signature valid (`PMTiles` header)
- ✅ File size ≥ 16KB (minimum PMTiles archive)
- ✅ PMTiles Reader can load file successfully

---

## Performance Metrics

### Before Optimization
- Processing speed: **13.7 min/CSV**
- Parallelism: 3 workers
- Processing order: Sequential (Z0 → Z15)

### After Optimization
- Processing speed: **6.7 min/CSV**
- Parallelism: 5 workers + geographic clustering
- Processing order: Geographic proximity (Z15 → Z0)
- **Total gain: 2.05x speedup**

### Expected Final Gain
With all three optimizations combined:
- Parallelism: +30% (3→5 workers)
- Clustering: +15-20% (cache locality)
- Geographic priority: +5-10% (reduced wasteful processing)
- **Cumulative: 2.4-3.2x total improvement expected**

---

## Usage Examples

### Basic Downsampling
```bash
cd mapterhorn/pipelines
source .venv/bin/activate
python downsampling_run.py
```

### With Custom Geographic Center
```bash
# Process Mount Kilimanjaro area (-3.065°S, 37.353°E)
CENTER_LAT=-3.065 CENTER_LON=37.353 python downsampling_run.py
```

### Validation & Repair Workflow
```bash
# 1. Validate current state
python downsampling_run.py --validate

# 2. Preview fixes (dry-run)
python downsampling_run.py --fix --dry-run

# 3. Apply fixes
python downsampling_run.py --fix

# 4. Regenerate specific files if needed
python downsampling_run.py --regenerate 15-15179-15610-20

# 5. Resume processing
python downsampling_run.py
```

### Parallel Processing Tuning
```bash
# Check current setting
echo $DOWNSAMPLING_WORKERS  # empty = uses default (5)

# Aggressive parallelism (16-core system)
DOWNSAMPLING_WORKERS=16 python downsampling_run.py

# Conservative (2-core system)
DOWNSAMPLING_WORKERS=2 python downsampling_run.py
```

---

## Architecture Notes

### Downsampling Process Flow
```
aggregation-store/{id}/*.csv
    ↓ (read downsampling instructions)
    ↓
Tile list (sorted by geographic proximity)
    ↓ (parallel processing, 5 workers)
    ↓
create_tile() × 814 files
    ↓ (reads from pmtiles-store, generates WebP)
    ↓
{z-x-y}-tmp/*.webp (intermediate tiles)
    ↓ (serial: merge → compress → finalize)
    ↓
pmtiles-store/{z-x-y}/{z-x-y}-{child_z}.pmtiles
    ↓ (cleaned up via utils.create_archive)
    ↓
✅ Final PMTiles file
```

### File Organization
```
aggregation-store/
  {aggregation_id}/
    {z-x-y}-downsampling.csv     (work instructions)
    {z-x-y}-downsampling.done    (completion marker)
    {z-x-y}-tmp/                 (intermediate WebP tiles)
      {z-x-y}.webp               (512px resampled tiles)
      {z-x-y}.webp
      ...

pmtiles-store/
  {z-x-y}/
    {z-x-y}-{child_z}.pmtiles    (final output)
```

---

## Configuration Reference

### Environment Variables
| Variable | Default | Purpose |
|----------|---------|---------|
| `DOWNSAMPLING_WORKERS` | 5 | Parallelism level (1-16) |
| `CENTER_LAT` | 8.465 | Geographic center latitude |
| `CENTER_LON` | -13.234 | Geographic center longitude |

### Constants (in `downsampling_run.py`)
```python
DEFAULT_CENTER_LAT = 8.465      # Freetown
DEFAULT_CENTER_LON = -13.234    # Freetown
```

---

## Troubleshooting

### Issue: "All PMTiles files small/incomplete"
**Cause**: Downsampling still in progress  
**Solution**: Wait for processing to complete (typically 3-4 days for full dataset)

### Issue: Slow processing despite optimization
**Cause 1**: Parallelism too high (CPU thrashing)
```bash
DOWNSAMPLING_WORKERS=3 python downsampling_run.py
```

**Cause 2**: Source PMTiles files missing
```bash
python downsampling_run.py --validate
# Check for missing referenced files
```

### Issue: Need to restart from specific files
**Solution**: Remove `.done` markers
```bash
find aggregation-store -name "15-15179-*-downsampling.done" -delete
python downsampling_run.py --regenerate 15-15179
```

---

## Future Improvements

### Potential Optimizations
1. **Memory-map caching**: Keep recently accessed PMTiles files in memory
2. **Tile prioritization**: Process tiles with more source coverage first
3. **Adaptive worker scaling**: Dynamically adjust workers based on CPU/memory load
4. **Incremental validation**: Only validate newly-completed files

### Monitoring
- Real-time progress dashboard
- Performance metrics logging
- Automatic error alerts

---

## Related Code

- `downsampling_run.py`: Main orchestrator
- `utils.py`: PMTiles creation & validation
- `aggregation_tile.py`: Work generation
- `bundle.py`: Final packaging

---

## References

- **Parallelism**: Python `multiprocessing.Pool`
- **Resampling**: scikit-image `pyramid_reduce` → Lanczos (scipy)
- **Encoding**: imagecodecs `webp_encode`
- **Format**: PMTiles spec v3, Web Mercator (EPSG:3857)
