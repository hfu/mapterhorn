import subprocess
import csv
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from glob import glob
import math
import os
import hashlib
import re

import numpy as np

from rasterio.warp import transform_bounds
import mercantile
import imagecodecs
from pmtiles.tile import zxy_to_tileid, tileid_to_zxy, TileType, Compression
from pmtiles.writer import Writer

# Upstream's original value is 12. 4bf6e535 raised it to 17 globally as a
# safety cap for a ~4cm/px (maxzoom~21) Freetown orthophoto source, whose
# gap from macrotile_z=12 would have made aggregation_reproject.py try to
# materialize a ~256GiB raster per macrotile. That's the right fix for a
# z21 source, but applied unconditionally it also forces every 1m
# elevation source (maxzoom 17) into macrotile_z==maxzoom -- i.e. one
# macrotile per single output tile, with no room for
# aggregation_covering.py to group a sensible contiguous area into one
# reprojection. Each macrotile is warped independently with only a small
# (150-unit) edge buffer, and cubicspline's resampling kernel needs
# neighboring pixels outside that buffer for a truly seamless result --
# plausible root cause of the seam/staircase artifact seen only below the
# native zoom. terrarium mode restores upstream's 12 (still >=
# num_overviews below our own maxzoom 17, so the pyramid range is
# unaffected); rgb/orthophoto mode keeps the 17 safety cap unchanged.
macrotile_z = 12 if os.environ.get('TILE_ENCODING', 'terrarium') == 'terrarium' else 17
macrotile_buffer_3857 = 150
num_overviews = 6

X_MIN_3857, _, X_MAX_3857, __ = transform_bounds('EPSG:4326', 'EPSG:3857', -180, 0, 180, 0)

def run_command(command, silent=True, env=None, check=True):
    """Run a shell command, capturing stdout/stderr.

    `check=True` (the default, mapterhorn-japan-bridge DECISIONS.md D120
    Fable review item #3): a nonzero exit status raises RuntimeError
    instead of being silently swallowed. The old always-ignore behavior
    let e.g. source_to_cog.py delete its input file right after a FAILED
    gdal_translate conversion -- the exact "unconditional delete after a
    failed conversion" hazard the review flagged. Callers that genuinely
    tolerate failure must say so explicitly with check=False; none
    currently do.
    """
    if env is None:
        env = os.environ.copy()
    if not silent:
        print(command)
    p = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    stdout, stderr = p.communicate()
    err = stderr.decode()
    if err != '' and not silent:
        print(err)
    out = stdout.decode()
    if out != '' and not silent:
        print(out)
    if check and p.returncode != 0:
        raise RuntimeError(
            f'command failed (exit {p.returncode}): {command}\n'
            f'stderr (tail): {err[-2000:]}'
        )
    return out, err

def create_folder(path):
    folder_path = Path(path)
    folder_path.mkdir(parents=True, exist_ok=True)

def get_aggregation_ids():
    '''
    returns aggregation ids ordered from oldest to newest
    '''
    return list(sorted([path.split('/')[-1] for path in glob('aggregation-store/*')]))

def get_vertical_rounding_multiplier(z):
    return int(2 ** ((10 - z) / 2) / (1 / 256))

def get_rounded_elevation_data(data, z):
    """Quantize elevation to Terrarium's own per-zoom vertical resolution.
    Ported from upstream mapterhorn/mapterhorn commit 53e4d3d ("Fix
    rounding on downsampling bug", #308, Oliver Wipfli, 2026-09-05):
    save_terrarium_tile() below always rounded its input this way, but
    downsampling_run.py's create_tile() -- forked away from upstream
    before that fix (FORK_NOTES.md section, downsampling_run.py entry)
    -- never rounded its own 2x2/4x4-averaged output, leaving float noise
    baked into every overview tile's RGB encoding. That noise carries no
    real information (the averaged input was already quantized at the
    finer child zoom) and measurably hurts WebP compression. Extracted
    here so both callers share one definition."""
    # full terrarium resolution of 1/256 at `full_resolution_zoom`
    # multiples of 2 of full terrarium resolution at lower zooms
    full_resolution_zoom = 19
    factor = 2 ** (full_resolution_zoom - z) / 256
    # Without this cap, factor grows to unreasonable multi-hundred/
    # -thousand-meter steps at low zoom (2048m at z0) -- quantizing real
    # elevation to that coarse a grid would destroy far more than the
    # "already lost at the finer child zoom" noise this function exists to
    # clean up. Ported as-is from upstream mapterhorn/mapterhorn, matching
    # its own cap value at each point in time: originally 32 (commit
    # 53e4d3d, D148), tightened to 1 (commit e964a04, "Clamp vertical
    # rounding to 1 meter", #310, Oliver Wipfli, 2026-09-08 -- Oliver's own
    # follow-up after shipping 53e4d3d, on finding 32m was too aggressive)
    # -- see mapterhorn-japan-bridge DECISIONS.md D155 for the follow-up
    # port and its measured impact.
    if factor > 1:
        factor = 1
    return np.round(data / factor) * factor

def save_terrarium_tile(data, filepath, valid_mask=None):
    """`valid_mask` (bool array, same shape as `data`, True = real data) is
    encoded as an alpha channel so gaps (no source coverage at all -- not
    the same as a small internal hole already filled by priority-merge)
    survive as nodata through downsampling's tile pyramid, instead of
    silently becoming a fake elevation of 0m that then contaminates a
    weighted average with neighboring real data. `None` means "fully
    valid" (whole-tile alpha=255), for callers with no gap information."""
    filename = filepath.split('/')[-1]
    z = int(filename.split('-')[0])

    data = get_rounded_elevation_data(data, z)

    data += 32768
    rgba = np.zeros((512, 512, 4), dtype=np.uint8)
    np.seterr(all='raise', under='ignore')
    try:
        rgba[..., 0] = data // 256
        rgba[..., 1] = data % 256
        rgba[..., 2] = (data - np.floor(data)) * 256
    except FloatingPointError:
        print(f'FloatingPointError raised in {filepath}')
        raise FloatingPointError()
    rgba[..., 3] = 255 if valid_mask is None else np.where(valid_mask, 255, 0).astype(np.uint8)
    with open(filepath, 'wb') as f:
        f.write(imagecodecs.webp_encode(rgba, lossless=True))

def save_rgb_tile(rgb_data, filepath, mask_data=None):
    """Save orthophoto RGB tile as WebP with optional alpha channel from mask.
    Black pixels (0,0,0) are treated as transparent/nodata."""
    if rgb_data.ndim == 2:
        rgb_data = np.stack([rgb_data, rgb_data, rgb_data], axis=2)

    rgb_data = np.nan_to_num(rgb_data, nan=0.0)
    rgb_data = np.clip(rgb_data, 0, 255).astype(np.uint8)

    # Create alpha channel: nodata from mask + black pixel transparency
    alpha = np.ones((rgb_data.shape[0], rgb_data.shape[1]), dtype=np.uint8) * 255

    if mask_data is not None:
        mask_data = np.clip(mask_data, 0, 1) * 255
        alpha = mask_data.astype(np.uint8)

    # Make black pixels (0,0,0) transparent (nodata)
    black_pixels = (rgb_data[:,:,0] == 0) & (rgb_data[:,:,1] == 0) & (rgb_data[:,:,2] == 0)
    alpha[black_pixels] = 0

    rgba_data = np.dstack([rgb_data, alpha])
    rgba_data = np.ascontiguousarray(rgba_data)
    encoded = imagecodecs.webp_encode(rgba_data, lossless=False, level=80)

    with open(filepath, 'wb') as f:
        if encoded:
            f.write(encoded)

def save_lineage_tile(category_data, filepath, valid_mask=None):
    """Save a categorical lineage/provenance tile as lossless WebP (D93/D94/
    D107): category index (0..6, lineage_provenance.GLOBAL_TIER) goes in the
    R channel, validity in alpha -- lossless() and losslessly round-trippable
    is mandatory here (unlike save_rgb_tile's lossy imagery path): a single
    off-by-one from lossy compression would silently relabel which source
    tier a pixel came from. G/B are unused (kept zero) -- one channel is
    enough for <=7 category values, no need for save_terrarium_tile's 3-byte
    fixed-point elevation encoding. Consumers (downsampling_run.py's lineage
    branch, lineage_downsample.majority_vote_downsample()) read the R channel
    back as `values` and the alpha channel back as `alpha`, matching that
    function's own (1024, 1024) values/alpha argument convention."""
    data = np.clip(np.nan_to_num(category_data, nan=0), 0, 255).astype(np.uint8)
    rgba = np.zeros((*data.shape, 4), dtype=np.uint8)
    rgba[..., 0] = data
    rgba[..., 3] = 255 if valid_mask is None else np.where(valid_mask, 255, 0).astype(np.uint8)
    with open(filepath, 'wb') as f:
        f.write(imagecodecs.webp_encode(rgba, lossless=True))

def create_archive(tmp_folder, out_filepath):
    # Write to a same-directory temp path and os.replace() into place at the very
    # end, rather than writing out_filepath directly -- this function streams tiles
    # in and only writes the pmtiles header/directory at finalize(), so a direct
    # write leaves out_filepath sitting on disk (isfile() == True) but incomplete/
    # unparseable for the whole duration of this call. A concurrent reader (bundle.py,
    # downsampling_run.py's own create_tile()) opening it during that window gets a
    # corrupt-read exception instead of a clean "not there yet" signal -- a second,
    # harder-to-catch variant of the same race documented in mapterhorn-japan-bridge
    # DECISIONS.md D37/D44. os.replace() on the same filesystem is atomic: readers
    # only ever see either the untouched old file (or nothing) or the fully-written
    # new one, never a partial state.
    tmp_out_filepath = f'{out_filepath}.tmp-{os.getpid()}'
    with open(tmp_out_filepath, 'wb') as f1:
        writer = Writer(f1)
        min_z = math.inf
        max_z = 0
        min_lon = math.inf
        min_lat = math.inf
        max_lon = -math.inf
        max_lat = -math.inf

        tile_ids = []
        for filepath in glob(f'{tmp_folder}/*.webp'):
            filename = filepath.split('/')[-1]
            z, x, y = [int(a) for a in filename.replace('.webp', '').split('-')]
            tile_ids.append(zxy_to_tileid(z=z, x=x, y=y))
        tile_ids = sorted(tile_ids)

        if not tile_ids:
            raise ValueError(f'No tiles found in {tmp_folder}. Parent tile processing may have skipped all tiles.')

        for tile_id in tile_ids:
            z, x, y = tileid_to_zxy(tile_id)
            filepath = f'{tmp_folder}/{z}-{x}-{y}.webp'
            with open(filepath, 'rb') as f2:
                writer.write_tile(tile_id, f2.read())

            max_z = max(max_z, z)
            min_z = min(min_z, z)
            west, south, east, north = mercantile.bounds(x, y, z)
            min_lon = min(min_lon, west)
            min_lat = min(min_lat, south)
            max_lon = max(max_lon, east)
            max_lat = max(max_lat, north)

        min_lon_e7 = int(min_lon * 1e7)
        min_lat_e7 = int(min_lat * 1e7)
        max_lon_e7 = int(max_lon * 1e7)
        max_lat_e7 = int(max_lat * 1e7)

        writer.finalize(
            {
                'tile_type': TileType.WEBP,
                'tile_compression': Compression.NONE,
                'min_zoom': min_z,
                'max_zoom': max_z,
                'min_lon_e7': min_lon_e7,
                'min_lat_e7': min_lat_e7,
                'max_lon_e7': max_lon_e7,
                'max_lat_e7': max_lat_e7,
                'center_zoom': int(0.5 * (min_z + max_z)),
                'center_lon_e7': int(0.5 * (min_lon_e7 + max_lon_e7)),
                'center_lat_e7': int(0.5 * (min_lat_e7 + max_lat_e7)),
            },
            {
                'attribution': '<a href="https://mapterhorn.com/attribution">© Mapterhorn</a>'
            },
        )

    os.replace(tmp_out_filepath, out_filepath)

def get_aggregation_item_string(aggregation_id, filename):
    result = ''
    filepath = f'aggregation-store/{aggregation_id}/{filename}'
    if not os.path.isfile(filepath):
        return None
    
    with open(filepath) as f:
        result = ''.join([l.strip() for l in f.readlines()])
    
    return result.strip()

def get_dirty_aggregation_filenames(current_aggregation_id, last_aggregation_id):
    filepaths = sorted(glob(f'aggregation-store/{current_aggregation_id}/*-aggregation.csv'))

    if last_aggregation_id is None:
        return [filepath.split('/')[-1] for filepath in filepaths]

    dirty_filenames = []
    for filepath in filepaths:
        filename = filepath.split('/')[-1]
        current = get_aggregation_item_string(current_aggregation_id, filename)
        last = get_aggregation_item_string(last_aggregation_id, filename)
        if current != last:
            dirty_filenames.append(filename)
    return dirty_filenames

LAYERS = ('aggregation', 'downsampling')
DATATYPES = ('elevation', 'lineage')

def get_required_datatypes():
    """mapterhorn-japan-bridge DECISIONS1.md D164: the single source of
    truth for "which datatypes does this run need", read from EMIT_
    LINEAGE. Before this, aggregation_run.py and aggregation_covering.py
    each independently re-derived this same expression at different
    times in different modules (`['elevation', 'lineage'] if os.environ.
    get('EMIT_LINEAGE', '0') == '1' else ['elevation']`) -- harmless
    while the two copies stayed byte-identical, but nothing enforced
    that; a future change to this rule (a third datatype, a different
    env var) would only need updating here once instead of drifting
    between the two call sites."""
    return ['elevation', 'lineage'] if os.environ.get('EMIT_LINEAGE', '0') == '1' else ['elevation']

# 1-go (the first full national generation) predates both the D95/D107
# layer/datatype split and the generation_id level below -- its entire
# production dataset lives in the old flat `pmtiles-store/{z7bucket}/...`
# layout. This is the ONLY generation the legacy-flat fallback below may
# ever resolve to; every other generation (1.5-go onward) lives strictly
# inside its own `pmtiles-store/{layer}/{datatype}/{generation_id}/`
# subtree. See PLAN.md section 0 for the generation_id <-> label table.
FLAT_LEGACY_GENERATION_ID = '01M0MWK852631SHCHPA66F21WQ'

# --- 1.6-go land-area maxzoom upsampling (mapterhorn-japan-bridge
# DECISIONS1.md D149-D151/D166, design finalized 2026-09-13 after an
# Opus review found the original design's two proposed fixes would
# have been actively harmful -- see D166 for the full history) ---
#
# LAND_UPSAMPLE_ZOOM_BY_GENERATION deliberately keys the "does this
# generation upsample land items, and to what zoom" policy by
# generation_id, the same pattern FLAT_LEGACY_GENERATION_ID above
# already uses. This is NOT a stylistic choice: whether a leaf's
# EFFECTIVE child_z differs from its covering CSV's own (native)
# filename value is a per-GENERATION policy decision, not something
# derivable from a covering's own content alone -- the exact same
# covering (same source files, same native maxzoom) must resolve to
# its native child_z for 1-go/1.5-go (built before this feature
# existed) and to the upsampled target for 1.6-go (built with it
# active). A global on/off switch (an env var, a module-level constant)
# would get this right for whichever generation is currently being
# built, but wrong for every OTHER generation anyone later reads
# without remembering to flip it back -- exactly the kind of config-
# drift bug this project has been bitten by before (worker-count env
# vars, EMIT_LINEAGE). Keying by generation_id makes the policy a fact
# about the DATA, permanently, regardless of what env vars happen to
# be set when some later tool reads it.
#
# Empty until 1.6-go's own generation_id is actually minted (see
# PLAN.md section 0's generation table) -- add it here, alongside the
# target zoom, before that generation's own aggregation_covering.py/
# aggregation_run.py run for real. Never remove a generation's entry
# once added, even after that generation is superseded -- anything
# that later reads 1.6-go's own aggregation-store (an audit tool, a
# repair script) still needs this to resolve child_z correctly.
LAND_UPSAMPLE_ZOOM_BY_GENERATION = {}

def get_land_upsample_target_zoom(generation_id):
    """None if this generation doesn't upsample land items at all (every
    generation before 1.6-go); otherwise the target zoom land items are
    upsampled to (16, as designed) for this specific generation."""
    return LAND_UPSAMPLE_ZOOM_BY_GENERATION.get(generation_id)

def is_land_item_covering(filepath):
    """True if this *-aggregation.csv covering references any source
    other than jpnationalsea -- the same "not sea-only" predicate
    D149's design settled on (open ocean gets no value from 1m detail).
    Reused by every place that needs to know whether an item is a
    1.6-go upsampling candidate: aggregation_reproject.py's own warp
    target, leaf_child_z() below, and remove_dangling_pmtiles.py's
    expected-output-filename set -- deliberately ONE function so these
    can't drift apart the way D149's own scattered notes never named a
    single shared predicate to call."""
    return any(source != 'jpnationalsea' for source, _filename, _maxzoom in read_aggregation_csv_rows(filepath))

_LEAF_CHILD_Z_CACHE = {}

def get_leaf_child_z_map(aggregation_id):
    """{(z, x, y): effective_child_z} for every native aggregation leaf
    in this generation -- "effective" meaning the REAL child_z this
    leaf's own pmtiles-store output actually has (or will have), which
    only differs from the covering CSV filename's own (native/planned)
    child_z for a land item in a generation that upsamples (see
    LAND_UPSAMPLE_ZOOM_BY_GENERATION above). Memoized per process
    (mirrors _SOURCE_MD5_CACHE's own reasoning: this project's
    multiprocessing.Pool workers each pay this cost once, not once
    truly-shared, which is the established and accepted pattern here).

    Deliberately a pure function of the covering CSV's own content plus
    this static per-generation policy table -- NOT a scan of real
    pmtiles-store output files. An Opus design review (D166) found that
    scanning real files would make downsampling_covering.py's own plan
    silently incomplete for any leaf aggregation_run.py hasn't finished
    yet (instead of DOWNSAMPLING_STRICT loudly catching a real missing
    child, as it does today), and would leave stale downsampling
    outputs behind whenever a position's effective child_z changes
    between covering runs into the same generation. A pure function of
    the covering plus a static policy table has neither problem: the
    plan is always complete and deterministic the moment coverings
    exist, independent of build progress."""
    if aggregation_id in _LEAF_CHILD_Z_CACHE:
        return _LEAF_CHILD_Z_CACHE[aggregation_id]
    target_zoom = get_land_upsample_target_zoom(aggregation_id)
    mapping = {}
    for filepath in glob(f'aggregation-store/{aggregation_id}/*-aggregation.csv'):
        filename = filepath.split('/')[-1]
        z, x, y, native_child_z = [int(a) for a in filename.replace('-aggregation.csv', '').split('-')]
        effective_child_z = native_child_z
        if target_zoom is not None and is_land_item_covering(filepath):
            effective_child_z = target_zoom
        mapping[(z, x, y)] = effective_child_z
    _LEAF_CHILD_Z_CACHE[aggregation_id] = mapping
    return mapping

def leaf_child_z(aggregation_id, z, x, y):
    """The effective (real) child_z of the native aggregation leaf at
    this exact position, or None if no leaf exists there at all (a
    downsampling-only position)."""
    return get_leaf_child_z_map(aggregation_id).get((z, x, y))

def get_pmtiles_folder(x, y, z, layer, datatype='elevation', generation_id=None):
    """mapterhorn-japan-bridge DECISIONS.md D95/D107 (+ generation_id,
    2026-09-04): pmtiles-store is split by `layer` (aggregation leaf
    output vs. downsampling pyramid output), by `datatype` (elevation vs.
    lineage), and by `generation_id` (the aggregation-store ULID this
    file belongs to) so that no single filename pattern is shared between
    things that get created, renamed, or deleted independently -- the
    root cause of D74-D76's 3,344-item aggregation loss. The
    generation_id level closes the last gap: without it, 1.5-go and 2-go
    would both write into the same layered tree and the D74-D76 pattern
    would recur the moment two layered generations coexist (the
    "structure difference was only accidental protection" finding in the
    1.5-go prep plan). Every caller must know which layer/datatype/
    generation it is locating; when it doesn't know the layer
    (downsampling_run.py resolving a *child* reference, which may itself
    be either layer), use resolve_layer() first rather than guessing.

    `generation_id` is deliberately required (no default): a partial
    update of call sites -- exactly the D74-D76 failure mode -- fails
    loudly as a TypeError/ValueError instead of silently writing into a
    shared location.
    """
    assert layer in LAYERS, f'unknown layer {layer!r}'
    assert datatype in DATATYPES, f'unknown datatype {datatype!r}'
    if not generation_id:
        raise ValueError(
            'get_pmtiles_folder() now requires generation_id (the '
            'aggregation-store ULID) -- see D74-D76/D95 and PLAN.md '
            'section 0. Refusing to guess.')
    prefix = f'pmtiles-store/{layer}/{datatype}/{generation_id}'
    if z < 7:
        bucket = prefix
    elif z == 7:
        bucket = f'{prefix}/{z}-{x}-{y}'
    else:
        parent = mercantile.parent(mercantile.Tile(x=x, y=y, z=z), zoom=7)
        bucket = f'{prefix}/{parent.z}-{parent.x}-{parent.y}'

    # D115 LEGACY-FLAT FALLBACK, now hard-gated to 1-go only: 1-go's
    # production dataset (14,590 files, ~579GB as of 2026-09-03) still
    # lives in the old flat `pmtiles-store/{z7bucket}/...` layout (no
    # layer/datatype/generation prefix at all). Tools pointed at 1-go
    # (audits, monitoring, any residual repair) keep working through this
    # branch; for ANY other generation_id this branch is unreachable, so
    # a 1.5-go/2-go write or cleanup glob can never land in (or delete
    # from) 1-go's flat tree -- the exact hazard the pre-generation_id
    # version of this fallback carried (a fresh 1.5-go write at a
    # position whose new bucket didn't exist yet would have fallen back
    # into 1-go's live flat bucket, and aggregation_tile.py's stale-
    # cleanup glob would then have deleted 1-go production files).
    if generation_id == FLAT_LEGACY_GENERATION_ID and z >= 7 and not os.path.isdir(bucket):
        flat_bucket = f'pmtiles-store/{bucket[len(prefix) + 1:]}'
        if os.path.isdir(flat_bucket):
            return flat_bucket

    return bucket

def resolve_layer(aggregation_id, z, x, y, child_z):
    """Which layer produced (or should produce) the pmtiles-store file
    named `{z}-{x}-{y}-{child_z}.pmtiles`? downsampling_covering.py's
    write_downsampling_items() writes downsampling.csv coverings whose
    own {z}-{x}-{y}-{child_z} fields can coincide with a *native*
    aggregation.csv covering at the same (z,x,y) position but a
    DIFFERENT child_z (the pyramid is recursive: an overview built from
    a deep leaf keeps that leaf's own (z,x,y) as its extent tile at
    several shallower zooms in a row -- confirmed on real 1.5-go data,
    3,344 of 6,373 positions carry both a leaf and one or more
    overviews at the same (z,x,y)) -- so neither a referenced child
    filename NOR a bare "does (z,x,y) have any aggregation.csv at all"
    check tells you which layer wrote a specific child_z.

    D165/D166 (2026-09-13): this used to check for an EXACT filename
    match (`{z}-{x}-{y}-{child_z}-aggregation.csv`), which broke the
    moment leaf_child_z() below could differ from a covering's own
    filename (1.6-go's land-upsampling). The FIRST attempted fix
    (matching by (z,x,y) position alone, ignoring child_z) was verified
    against real 1.5-go data to flip 49.9% of all real child references
    (7,226/14,489) -- it would have broken the downsampling pyramid for
    every generation, upsampling or not, by conflating "a leaf exists at
    this position" with "child_z belongs to that leaf" when the 3,344
    positions above prove those are different questions. The correct
    fix compares child_z against the leaf's own EFFECTIVE child_z
    (leaf_child_z() -- native for every existing generation, upsampled
    for a land item in a generation that upsamples): a match means this
    exact child_z is the leaf itself (aggregation layer); anything else
    at this position (including a DIFFERENT child_z, e.g. an overview
    sitting at the same (z,x,y) as its own leaf) is downsampling.
    Behavior is byte-identical to the old exact-filename-match check
    for every generation not in LAND_UPSAMPLE_ZOOM_BY_GENERATION, since
    leaf_child_z() there always returns the covering filename's own
    native value."""
    return 'aggregation' if leaf_child_z(aggregation_id, z, x, y) == child_z else 'downsampling'

# --- .done manifest machinery (mapterhorn-japan-bridge DECISIONS.md D119
# P2.B design + D120 Fable review item #6, implemented 2026-09-04) ---
#
# The old `.done` markers were empty touch files: no record of WHICH
# datatype finished (so an elevation pass made the later lineage pass
# silently skip every item -- the hard blocker for 1.5-go) and no record
# of WHAT inputs it was built from (so a repaired leaf never invalidated
# the overviews above it -- 949/8,223 items, 11.5%, measurably stale in
# 1-go's published archive, D119). A `.done` file is now a small JSON
# manifest carrying both: the datatypes it certifies and a fingerprint of
# the inputs it was built from. Legacy empty markers (1-go) parse as "{}"
# and are treated as elevation-only with unknown freshness, so nothing in
# 1-go churns.

DONE_MANIFEST_FORMAT = 'mjb-done-manifest/1'

def stat_input_entry(path):
    """Fingerprint entry for a large binary input (a child .pmtiles):
    size + mtime_ns, no content read. A missing input gets an explicit
    marker entry -- so an item completed with a hole (non-STRICT mode)
    automatically reads as stale once the missing child appears."""
    try:
        st = os.stat(path)
        return {'path': path, 'size': st.st_size, 'mtime_ns': st.st_mtime_ns}
    except OSError:
        return {'path': path, 'missing': True}

def content_input_entry(path, canonical_path=None):
    """Fingerprint entry for a small text input (a covering .csv), hashed
    by CONTENT, not mtime -- downsampling_covering.py regenerates every
    .csv (identical bytes, fresh mtime) each publish cycle, and an
    mtime-based entry would mark the whole pyramid stale every cycle.

    `canonical_path` (D163): the recorded 'path' field defaults to `path`
    itself, which is fine for every EXISTING (within-generation) caller.
    But `path` for an aggregation covering CSV is `aggregation-store/
    {generation_id}/{filename}` -- embeds the generation_id -- so two
    byte-identical CSVs in different generations would still produce
    DIFFERENT fingerprints purely because their 'path' string differs,
    making any cross-generation comparison of inputs_fingerprint always
    mismatch even when nothing actually changed. Callers that build
    entries for a cross-generation reuse check (aggregation_covering.py's
    try_reuse_from_previous_generation) must pass a generation-agnostic
    `canonical_path` (just the filename) so the same item in any
    generation hashes to the same entry."""
    h = hashlib.sha256()
    recorded_path = canonical_path if canonical_path is not None else path
    try:
        with open(path, 'rb') as f:
            h.update(f.read())
        return {'path': recorded_path, 'sha256': h.hexdigest()}
    except OSError:
        return {'path': recorded_path, 'missing': True}

_SOURCE_MD5_CACHE = {}

def get_source_md5_map(source):
    """mapterhorn-japan-bridge DECISIONS1.md D163: {filename: md5} for one
    source, read via open_manifest() (D14/D26's own manifest opener,
    already handles the `../source-catalog/{source}/file_list.csv[.gz]`
    path and the gzip-vs-plain fallback) and csv.DictReader (D164: the
    first version hand-rolled this with `line.rsplit(',', 2)`, which
    source_download.py's own load_manifest() already avoided by using
    DictReader -- reused here rather than reimplementing the fragile
    version a second time) from that source's own download manifest
    (`url,size,md5` columns; md5 is the S3 ETag, not a fresh local
    hash). Memoized per process: this is looked up once per (source,
    filename) pair for every aggregation item nationwide, and the
    manifest itself can be hundreds of thousands of rows (jpnational1
    alone is 291,779)."""
    if source in _SOURCE_MD5_CACHE:
        return _SOURCE_MD5_CACHE[source]
    md5_map = {}
    with open_manifest(source) as f:
        for row in csv.DictReader(f):
            filename = row['url'].split('/')[-1]
            md5_map[filename] = row['md5']
    _SOURCE_MD5_CACHE[source] = md5_map
    return md5_map

def md5_input_entries_for_aggregation_csv(filepath):
    """mapterhorn-japan-bridge DECISIONS1.md D163: one fingerprint entry
    per (source, filename) referenced by an *-aggregation.csv covering,
    keyed by that source's own manifest MD5 -- NOT the covering CSV's own
    text (which only records filename+maxzoom, not file content). This
    closes a real, documented gap: D18/D35 found a same-filename,
    same-byte-size file whose CONTENT silently changed after a
    corruption fix (`aws s3 sync --size-only` would have missed it too).
    A content_input_entry() of the covering CSV alone cannot detect that
    class of change; comparing this function's own entries across
    generations can, since a changed file gets a new MD5 in its source's
    manifest once re-uploaded, regardless of filename or byte size.
    Deliberately does NOT touch aggregation.csv's own on-disk format
    (still exactly `source,filename,maxzoom` -- D18/D20's own warning
    about partial-application accidents applies here: read_aggregation_
    csv_rows() and every other reader of this file unpacks exactly 3
    comma-separated fields and would break on a 4th column)."""
    entries = []
    for source, filename, _maxzoom in read_aggregation_csv_rows(filepath):
        md5_map = get_source_md5_map(source)
        entry_path = f'{source}/{filename}'
        if filename in md5_map:
            entries.append({'path': entry_path, 'md5': md5_map[filename]})
        else:
            # Should not happen (covering was built from this same
            # manifest) -- but a lookup miss must make the fingerprint
            # visibly different, never silently absent, so a stale
            # manifest can't accidentally look unchanged.
            entries.append({'path': entry_path, 'missing': True})
    return entries

def aggregation_fingerprint_entries(csv_path, canonical_filename):
    """mapterhorn-japan-bridge DECISIONS1.md D164: the full .done-manifest
    `entries` list for one aggregation item -- the covering CSV's own
    content plus every referenced source file's MD5. Three call sites
    (aggregation_run.py's run(), aggregation_covering.py's try_reuse_
    from_previous_generation(), and backfill_aggregation_md5_
    fingerprints.py) used to each hand-construct this same two-part list
    independently; factored here so a future change to what an
    aggregation item's fingerprint covers only needs updating once."""
    return [content_input_entry(csv_path, canonical_path=canonical_filename)] + md5_input_entries_for_aggregation_csv(csv_path)

def compute_inputs_fingerprint(entries):
    h = hashlib.sha256()
    for e in sorted(entries, key=lambda e: e['path']):
        if e.get('missing'):
            h.update(f"{e['path']}\tMISSING\n".encode())
        elif 'sha256' in e:
            h.update(f"{e['path']}\tsha256:{e['sha256']}\n".encode())
        elif 'md5' in e:
            h.update(f"{e['path']}\tmd5:{e['md5']}\n".encode())
        else:
            h.update(f"{e['path']}\t{e['size']}\t{e['mtime_ns']}\n".encode())
    return f'sha256:{h.hexdigest()}'

def write_done_manifest(done_path, datatypes, generation_id, entries, extra=None):
    """Atomically write a .done manifest (same same-directory-tmp +
    os.replace pattern as create_archive(), so a reader never sees a
    half-written marker)."""
    manifest = {
        'format': DONE_MANIFEST_FORMAT,
        'datatypes': sorted(set(datatypes)),
        'generation_id': generation_id,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'inputs': sorted(entries, key=lambda e: e['path']),
        'inputs_fingerprint': compute_inputs_fingerprint(entries),
    }
    if extra:
        manifest.update(extra)
    tmp_path = f'{done_path}.tmp-{os.getpid()}'
    with open(tmp_path, 'w') as f:
        json.dump(manifest, f, indent=1)
    os.replace(tmp_path, done_path)

def read_done_manifest(done_path):
    """None = no marker at all. {} = legacy empty marker (1-go's touch
    files -- always exactly 0 bytes, from `touch`). False = corrupt/
    truncated/unparseable marker -- deliberately NOT the same as a
    legitimate legacy marker (D165, Opus code review finding #4, 2026-
    09-13): before this distinction, a marker truncated by a crash mid-
    write (APFS committing an os.replace() rename before its data
    blocks are actually flushed -- write_done_manifest() has no fsync,
    nor does anything else in this codebase) read identically to a
    genuine pre-D119 completion marker and got silently certified as
    'done and current' by done_covers()/done_is_current() with zero
    verification, for elevation-only runs specifically (a lineage
    requirement never matched the legacy elevation-only bypass). dict =
    a real manifest."""
    if not os.path.isfile(done_path):
        return None
    if os.path.getsize(done_path) == 0:
        return {}
    try:
        with open(done_path) as f:
            manifest = json.load(f)
    except (ValueError, OSError):
        return False
    if not isinstance(manifest, dict) or manifest.get('format') != DONE_MANIFEST_FORMAT:
        return False
    return manifest

def done_covers(done_path, required_datatypes):
    """Does this marker certify all of `required_datatypes`? (No
    freshness check -- see done_is_current() for that.) Legacy empty
    markers certify elevation only: they predate lineage entirely. A
    corrupt/truncated marker (read_done_manifest() returning False, not
    a genuine {}) never certifies anything -- see that function's own
    docstring for why this distinction matters."""
    manifest = read_done_manifest(done_path)
    if manifest is None or manifest is False:
        return False
    if not manifest:
        return set(required_datatypes) <= {'elevation'}
    return set(required_datatypes) <= set(manifest.get('datatypes', []))

def done_is_current(done_path, required_datatypes, entries):
    """done_covers() plus the D119 freshness gate: False when the
    recorded inputs fingerprint no longer matches `entries` (the same
    entry list the caller would record on completion), i.e. an input was
    repaired/replaced/added since this marker was written -- the caller
    should rebuild. Legacy empty markers have no fingerprint to compare;
    they stay 'current' for elevation (deliberate: never churn 1-go). A
    corrupt/truncated marker (read_done_manifest() returning False) is
    never current -- see that function's own docstring (D165)."""
    manifest = read_done_manifest(done_path)
    if manifest is None or manifest is False:
        return False
    if not manifest:
        return set(required_datatypes) <= {'elevation'}
    if not set(required_datatypes) <= set(manifest.get('datatypes', [])):
        return False
    return compute_inputs_fingerprint(entries) == manifest.get('inputs_fingerprint')

def downsampling_done_path(csv_path, datatype):
    """Datatype-scoped .done marker path for one downsampling item (D120
    Fable review item #6). elevation keeps the historical
    '-downsampling.done' name (1-go compat, and every existing audit
    glob); lineage gets '-downsampling.lineage.done' -- distinct
    filenames, so one datatype's pass can never make the other's
    silently skip. (The lineage name deliberately does NOT match the
    `*-downsampling.done` glob pattern, keeping legacy tooling
    elevation-only rather than double-counting.)"""
    assert datatype in DATATYPES, f'unknown datatype {datatype!r}'
    suffix = '-downsampling.done' if datatype == 'elevation' else '-downsampling.lineage.done'
    assert csv_path.endswith('-downsampling.csv'), csv_path
    return csv_path[:-len('-downsampling.csv')] + suffix

# GSI's own DEM naming embeds a product-type letter after the resolution
# digits (e.g. ...-DEM5A-, ...-DEM10B-): A = airborne laser (LiDAR,
# highest accuracy), B/C = photogrammetry-derived fallbacks used where no
# LiDAR survey exists yet (20cm/40cm GSD respectively for the 5m tier).
# Lower rank = higher accuracy = should be tried first. Sources with no
# such suffix (e.g. jpnationalsea's Copernicus GLO-30 files) get rank 0,
# since there is only ever one product type per cell for those -- rank
# is meaningless as a priority signal there, only used to keep the group
# key well-defined.
PRODUCT_TYPE_RANK = {'A': 0, 'B': 1, 'C': 2}
# Case-insensitive: most GSI filenames use uppercase (DEM5A/DEM10B), but
# some older (pre-~2018) vintages use lowercase (dem5b/dem10b) -- 6,676
# such files found across source-store this session. The pattern used to
# be uppercase-only, silently falling through to rank 0 (the same rank as
# tier-A/highest-accuracy) for every lowercase file -- misclassifying
# real tier-B/C data as if unranked. See mapterhorn-japan-bridge
# DECISIONS.md D28.
PRODUCT_TYPE_PATTERN = re.compile(r'-DEM\d+([A-C])-', re.IGNORECASE)

def get_product_type_rank(filename):
    m = PRODUCT_TYPE_PATTERN.search(filename)
    if m:
        return PRODUCT_TYPE_RANK[m.group(1).upper()]
    return 0

def read_aggregation_csv_rows(filepath):
    """mapterhorn-japan-bridge DECISIONS1.md D164: (source, filename,
    maxzoom) triples from a *-aggregation.csv covering, in file order --
    the one place that knows this file's `source,filename,maxzoom`
    layout. Shared by get_grouped_source_items() (below) and
    md5_input_entries_for_aggregation_csv() (D163), which used to each
    hand-roll their own copy of this same parse -- a real risk if the
    format ever changed and only one of the two got updated."""
    rows = []
    with open(filepath) as f:
        f.readline() # skip header
        for line in f:
            line = line.strip()
            if not line:
                continue
            source, filename, maxzoom = line.split(',')
            rows.append((source, filename, int(maxzoom)))
    return rows

# Group source items by (maxzoom, source, product-type rank), in that
# priority order -- e.g. for a tile covered by jpnational1/jpnational5
# (a mix of DEM5A/5B/5C)/jpnational10 (DEM10A/10B)/jpnationalsea, this
# produces up to seven groups in priority order: 1, 5a, 5b, 5c, 10a,
# 10b, sea (DECISIONS.md D20). Each group is now guaranteed a single
# product type, so aggregation_reproject.py's per-group gdalbuildvrt
# call never has to arbitrate between different-accuracy files anymore
# -- that arbitration now happens via aggregation_merge.py's existing
# per-pixel nodata-fill + Gaussian-blurred-seam compositing across
# groups (the same mechanism already used for 1m vs 5m vs 10m vs sea),
# reused as-is since it already handles an arbitrary number of groups.
def get_grouped_source_items(filepath):
    line_tuples = []
    for source, filename, maxzoom in read_aggregation_csv_rows(filepath):
        line_tuples.append((
            -maxzoom,
            source,
            get_product_type_rank(filename),
            filename
        ))
    line_tuples = sorted(line_tuples)
    grouped_source_items = []

    first_line_tuple = line_tuples[0]
    last_group_signature = (first_line_tuple[0], first_line_tuple[1], first_line_tuple[2])
    current_group = [{
        'maxzoom': -first_line_tuple[0],
        'source': first_line_tuple[1],
        'filename': first_line_tuple[3],
    }]
    for line_tuple in line_tuples[1:]:
        current_group_signature = (line_tuple[0], line_tuple[1], line_tuple[2])
        if current_group_signature != last_group_signature:
            grouped_source_items.append(current_group)
            current_group = []
            last_group_signature = current_group_signature
        current_group.append({
            'maxzoom': -line_tuple[0],
            'source': line_tuple[1],
            'filename': line_tuple[3],
        })
    grouped_source_items.append(current_group)
    return grouped_source_items

class HashWriter:
    def __init__(self, f):
        self.f = f
        self.md5 = hashlib.md5()
    def write(self, data):
        self.md5.update(data)
        return self.f.write(data)
    def tell(self):
        return self.f.tell()
    def flush(self):
        return self.f.flush()
    def close(self):
        return self.f.close()


# Japan-specific quadrant classifier for downsampling priority ordering
# (mapterhorn-japan-bridge DECISIONS.md D25). Translates
# japan-geotiff-dem/scripts/quadrans_script.rb's mesh-code-based
# North/East/South/West split into direct lon/lat thresholds, using the
# JIS 1st-order mesh grid's own defining formula (mesh code y -> latitude
# = y * 2/3 deg; mesh code x -> longitude = x + 100 deg), since aggregation
# items here are indexed by mercator tile z/x/y, not GSI mesh codes, and
# have no mesh code of their own to classify directly.
JAPAN_QUADRANS_PRIORITY = {'north': 0, 'south': 1, 'east': 2, 'west': 3}

def japan_quadrans_of(lon, lat):
    if lat >= 62 * (2.0 / 3.0):  # ~41.333 deg N (quadrans_script.rb: y >= 62)
        return 'north'
    if lon >= 38 + 100:  # 138 deg E (quadrans_script.rb: x >= 38)
        return 'east'
    if lon <= 32 + 100:  # 132 deg E (quadrans_script.rb: x <= 32)
        return 'south'
    return 'west'


# Manifest source-catalog file_list.csv[.gz] opener (mapterhorn-japan-bridge
# DECISIONS.md D26). Prefers the gzip-compressed form -- large national-scope
# manifests (jpnational1/5, hundreds of thousands of rows) exceed GitHub's
# 50MB recommended file-size threshold as plain CSV; gzip shrinks this
# highly-repetitive URL-list text dramatically. Falls back to a plain .csv
# for any source not yet converted, so this is backward compatible.
def open_manifest(source):
    gz_path = Path(f'../source-catalog/{source}/file_list.csv.gz')
    csv_path = Path(f'../source-catalog/{source}/file_list.csv')
    if gz_path.is_file():
        return gzip.open(gz_path, 'rt', newline='')
    return open(csv_path, newline='')

def manifest_path_glob():
    """All source-catalog dirs with either manifest form, source name only."""
    names = set()
    for p in Path('../source-catalog').glob('*/file_list.csv.gz'):
        names.add(p.parent.name)
    for p in Path('../source-catalog').glob('*/file_list.csv'):
        names.add(p.parent.name)
    return sorted(names)

def manifest_exists(source):
    return (Path(f'../source-catalog/{source}/file_list.csv.gz').is_file()
            or Path(f'../source-catalog/{source}/file_list.csv').is_file())
