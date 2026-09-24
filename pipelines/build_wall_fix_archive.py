"""D174/D176 (mapterhorn-japan-bridge DECISIONS1.md): builds synthetic
0m/nodata fill archives for positions this project's own elevation
pyramid genuinely lacks at every source -- not a downstream processing
bug, but Copernicus GLO-30's own global inventory (24,674 1-degree
cells) simply not covering some remote/small-island-adjacent cells at
all (verified: absent from our own source-catalog/glo30/file_list.txt
snapshot AND a live 404 against the real upstream bucket). MapLibre GL
JS decodes a missing (204) raster-dem tile as RGB(0,0,0) = -32768m
(pre-v6) or otherwise glitches (upstream PR #5392/#8207) -- so any
present/absent tile boundary is a visible "wall" in 3D terrain view.
This can't be fixed by re-downloading (there's nothing to download)
or by changing the aggregation/downsampling pipeline (it's behaving
correctly given its real inputs) -- the fix is a synthetic archive,
spliced in via `pmtiles merge` the same way build_global_overview.py's
z0-7 output already is.

Three fill targets, run separately (each is a distinct pmtiles merge
input):
  z0-7    completes the existing global-overview archive's own gaps
          (build_global_overview.py's own z0-7 pyramid has real holes
          over deep ocean -- see that script's own docstring). All
          8,321 of these are confirmed 100% GLO-30-mask-eligible.
  z8-z12  the staged fill for mapterhorn-japan-bridge's own elevation
          archive, scoped to z8-z12 only (not deeper) because the sea
          source's own native resolution tops out at z12.
  z13-z16 D182 (2026-09-24): the z13+ extension. Empirically confirmed
          necessary (a live check near Takeshima found the exact same
          wall D174 fixed at z8-z12, one zoom level deeper) AND found to
          be far more widespread than a GLO-30-gap-only story -- ANY
          z12 "leaf" position (real z12 tile, no real z13 child) shows
          the same 204-beyond-z12 pattern, whether or not it's a GLO-30
          inventory gap, because sea-only aggregation items never build
          past z12 by design (D149: no value in 1m detail on open
          seafloor). Real count: 214,090 z12 leaves nationwide, of which
          152,437 are GLO-30-inventory-absent (D174's own criterion,
          just extended in zoom range) and 61,653 are confirmed real
          ocean via jpnationalsea's own 275-cell download (a SEPARATE,
          new safety criterion -- see sea_fill_eligible()'s own
          docstring for why the global GLO-30 inventory alone is not
          safe enough here). 340 descendant tiles per eligible leaf
          (z13..z16) puts the total at ~72.8M tiles -- reviving the
          exact scale D174's first design review originally avoided,
          but confirmed cheap in practice: pmtiles' own content-dedup +
          Hilbert run-length coalescing collapsed the z8-z12 fill's
          152,267 tiles into 796 directory entries (~191x), and the
          same mechanism applies here (Hidenori's own call, D182: "同じ
          空タイルならそれほど容量もとるまいね").

The z0-7 and z8-z12 targets share one safety rule (D174's second design
review, "Blocker 1"): a position is fill-eligible ONLY when EVERY
1-degree cell it touches is itself absent from GLO-30's global
inventory -- this is what keeps the fill from stamping fake 0m sea
level over real foreign land (Luzon, Sakhalin, Kamchatka, Beijing are
all present in that inventory and were confirmed excluded by this
rule). z13-z16 reuses this criterion for its own "narrow" category
unchanged, and adds a second, independent criterion (sea_fill_eligible)
for positions the global inventory has real (but foreign, unverified)
data for -- see build_z13_z16()'s own docstring for the full reasoning
and the monotonicity argument that makes per-descendant re-checking
unnecessary.

And a second, less obvious safety rule ("Blocker 3", found the hard
way during D176's own implementation): mask-eligibility must be
checked TOP-DOWN with each position's PARENT already resolved (real,
or already included in this same fill), not independently per zoom.
A fine child tile's small footprint can be 100% GLO-30-absent (mask-
eligible) while its own coarser parent's larger footprint touches ONE
additional cell that DOES have upstream data -- making the parent
mask-INeligible -- and if that parent is also genuinely absent from
the real archive, the child becomes an orphan (found 759 real cases
the first time this was implemented without the top-down gate).
Positions whose parent can't be resolved are simply left unfilled
(stay a genuine 204) rather than risk an orphan -- this can only
shrink a fill's scope, never grow it, relative to the naive
per-zoom-independent check.

Usage:
  uv run python3 build_wall_fix_archive.py z0-7 \\
      --overview bundle-store/global-overview.pmtiles \\
      --out bundle-store/wall-fix-z0-7.pmtiles

  uv run python3 build_wall_fix_archive.py z8-z12 \\
      --elevation bundle-store/mapterhorn-japan-bridge.pmtiles \\
      --overview bundle-store/global-overview.pmtiles \\
      --z0-7-fill bundle-store/wall-fix-z0-7.pmtiles \\
      --out bundle-store/wall-fix-z8-z12.pmtiles

  uv run python3 build_wall_fix_archive.py z13-z16 \\
      --elevation bundle-store/mapterhorn-japan-bridge.pmtiles \\
      --out bundle-store/wall-fix-z13-z16.pmtiles

  (z13-z16 reads real z12/z13 tile presence directly from --elevation,
  so it must be the z8-z12-fill-spliced archive, not the pre-fill one --
  otherwise every currently-GLO-30-gap z12 position would look like a
  missing z12 tile rather than a resolvable leaf, and get silently
  skipped rather than filled.)

No mode modifies its inputs. Splice the result(s) into the final
archive via a single `pmtiles merge` alongside the existing z0-7
overview splice (real elevation archive first, so its own metadata is
what `pmtiles merge` copies -- it only copies the FIRST input's JSON
metadata, D174's own second review found this the hard way too).

NOT YET wired into merge_japan_bundles.py's own runbook -- run by hand
until DECISIONS1.md's own open items (the z13+ empirical question,
check_pmtiles_integrity.py's tile-count-scale readiness) are resolved
and a production run is explicitly approved.
"""
import os

# Same TMPDIR-before-tempfile-resolves override as build_global_overview.py/
# bundle.py/merge_japan_bundles.py/aggregation_run.py (D104/D105/D120 #2) --
# pmtiles.writer.Writer's tempfile.TemporaryFile() would otherwise land on
# the small boot volume.
os.environ['TMPDIR'] = os.path.abspath('pmtiles-store/tmp-store/writer-scratch/')
os.makedirs(os.environ['TMPDIR'], exist_ok=True)
import tempfile
tempfile.tempdir = None

import argparse
import math
import re

import numpy as np
import imagecodecs

import utils
from pmtiles.reader import Reader, MmapSource, deserialize_header, deserialize_directory, tileid_to_zxy
from pmtiles.tile import zxy_to_tileid, TileType, Compression
from pmtiles.writer import Writer

GLO30_LIST = os.path.join(os.path.dirname(__file__), '..', 'source-catalog', 'glo30', 'file_list.txt')

# D182: jpnationalsea's own bounds.csv -- the 275 1-degree cells this
# project has ACTUALLY downloaded real Copernicus bathymetry for, a
# strict subset of GLO30_LIST's 24,674-cell global inventory. Loading
# THIS (not the global list) is what makes the z13-z16 "confirmed real
# ocean" fill category safe -- the global inventory also has real
# entries for foreign land (Korea, China, Russia, Taiwan) inside the
# same 116-160E/15-52N BOX, which this project never downloaded and
# knows nothing about. "Present in the global inventory" is NOT "safe
# to flat-fill 0m"; "present in jpnationalsea's own file list" is.
JPNATIONALSEA_BOUNDS = os.path.join(os.path.dirname(__file__), 'source-store', 'jpnationalsea', 'bounds.csv')

# generous enough that its own edge sits well offshore of any Japanese
# land at every zoom in the staged z8-z12 range (D174's second design
# review's own choice, checked directly: 15N/116E puts the box's own
# edge >300km from the nearest Japanese land)
BOX = dict(west=116, south=15, east=160, north=52)

_CELL_RE = re.compile(r'([NS])(\d+)_00_([EW])(\d+)_00')


def load_glo30_cells():
    """Set of (lat, lon) integer-degree southwest corners GLO-30's own
    global inventory actually has a tile for."""
    cells = set()
    with open(GLO30_LIST) as f:
        for line in f:
            m = _CELL_RE.search(line)
            if not m:
                continue
            ns, lat_s, ew, lon_s = m.groups()
            lat = int(lat_s) * (1 if ns == 'N' else -1)
            lon = int(lon_s) * (1 if ew == 'E' else -1)
            cells.add((lat, lon))
    return cells


def load_jpnationalsea_cells():
    """Set of (lat, lon) integer-degree southwest corners this project's
    OWN jpnationalsea source actually downloaded (275 of GLO-30's 24,674
    global cells) -- see JPNATIONALSEA_BOUNDS's own comment for why this,
    not load_glo30_cells(), is the safe check for D182's z13-z16 'confirmed
    real ocean' fill category."""
    cells = set()
    with open(JPNATIONALSEA_BOUNDS) as f:
        next(f)  # header: filename,left,bottom,right,top,width,height
        for line in f:
            filename = line.split(',')[0]
            m = _CELL_RE.search(filename)
            if not m:
                continue
            ns, lat_s, ew, lon_s = m.groups()
            lat = int(lat_s) * (1 if ns == 'N' else -1)
            lon = int(lon_s) * (1 if ew == 'E' else -1)
            cells.add((lat, lon))
    return cells


def sea_fill_eligible(x, y, z, jpnationalsea_cells):
    """True iff every 1-degree cell this tile touches is one this project
    has ITSELF downloaded real Copernicus bathymetry for (jpnationalsea's
    own 275-cell set) -- confirms genuine, already-verified ocean, not
    merely 'present somewhere in GLO-30's global inventory' (which also
    covers foreign land this project never looked at -- see
    JPNATIONALSEA_BOUNDS's own comment)."""
    return all(c in jpnationalsea_cells for c in touched_cells(x, y, z))


def enumerate_zoom_positions(pmtiles_path, zooms):
    """Fast directory-only walk (never fetches tile bytes, same technique
    as check_pmtiles_integrity.py) -- returns {zoom: set-of-(x,y)} for
    just the requested zooms. ~16s for this project's whole 273GB/3.6M-tile
    archive, regardless of which zooms are requested, since the directory
    tree itself must be walked in full either way."""
    positions = {z: set() for z in zooms}
    zoom_set = set(zooms)
    with open(pmtiles_path, 'rb', buffering=8 * 1024 * 1024) as f:
        def get_bytes(offset, length):
            f.seek(offset)
            return f.read(length)

        def walk(dir_offset, dir_length):
            entries = deserialize_directory(get_bytes(dir_offset, dir_length))
            for entry in entries:
                if entry.run_length > 0:
                    for i in range(entry.run_length):
                        z, x, y = tileid_to_zxy(entry.tile_id + i)
                        if z in zoom_set:
                            positions[z].add((x, y))
                else:
                    walk(header['leaf_directory_offset'] + entry.offset, entry.length)

        header = deserialize_header(get_bytes(0, 127))
        walk(header['root_offset'], header['root_length'])
    return positions


def tile_bounds_deg(x, y, z):
    n = 2 ** z
    west = x / n * 360 - 180
    east = (x + 1) / n * 360 - 180
    def lat(yy):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))
    return west, lat(y + 1), east, lat(y)  # west, south, east, north


def touched_cells(x, y, z):
    west, south, east, north = tile_bounds_deg(x, y, z)
    lon0, lon1 = math.floor(west), math.ceil(east) - 1
    lat0, lat1 = math.floor(south), math.ceil(north) - 1
    return [(lat, lon) for lat in range(lat0, lat1 + 1) for lon in range(lon0, lon1 + 1)]


def fill_eligible(x, y, z, glo30_cells):
    """True iff every 1-degree cell this tile touches is absent from the
    GLO-30 global inventory -- i.e. genuinely no data exists upstream
    anywhere near this position."""
    return all(c not in glo30_cells for c in touched_cells(x, y, z))


def make_fill_tile_blob():
    """The canonical 0m/nodata fill tile: R=128 G=0 B=0 A=0 uniformly
    (elevation=0.0m per the Terrarium formula, alpha=0 marking it
    explicitly synthetic -- not real coverage), via this project's own
    encoding path (utils.get_rounded_elevation_data + the same RGBA/
    webp_encode construction utils.save_terrarium_tile() uses)."""
    data = np.zeros((512, 512), dtype=np.float64)
    valid_mask = np.zeros((512, 512), dtype=bool)
    rounded = utils.get_rounded_elevation_data(data, z=10)  # zoom is a no-op for a flat 0 tile
    d = rounded + 32768
    rgba = np.zeros((512, 512, 4), dtype=np.uint8)
    rgba[..., 0] = d // 256
    rgba[..., 1] = d % 256
    rgba[..., 2] = (d - np.floor(d)) * 256
    rgba[..., 3] = np.where(valid_mask, 255, 0).astype(np.uint8)
    return imagecodecs.webp_encode(rgba, lossless=True)


def load_fill_positions(archive_path):
    """Reads back every (z, x, y) actually present in a (small) fill
    archive already built by this script."""
    positions = set()
    with open(archive_path, 'rb') as f:
        header = deserialize_header(f.read(127))
        def get_bytes(offset, length):
            f.seek(offset)
            return f.read(length)
        def walk(dir_offset, dir_length):
            entries = deserialize_directory(get_bytes(dir_offset, dir_length))
            for e in entries:
                if e.run_length > 0:
                    for i in range(e.run_length):
                        positions.add(tileid_to_zxy(e.tile_id + i))
                else:
                    walk(header['leaf_directory_offset'] + e.offset, e.length)
        walk(header['root_offset'], header['root_length'])
    return positions


def write_fill_archive(fill_positions, fill_blob, out_path, attribution):
    """fill_positions: iterable of (z, x, y), any order -- sorted here into
    true ascending tile-id order (required for pmtiles.writer.Writer's own
    run-length coalescing to actually coalesce, not just avoid crashing).
    Atomic: writes to a same-directory tmp path, os.replace()s into place,
    matching utils.create_archive()'s own rationale (D37/D44/D171)."""
    entries = sorted(
        ((zxy_to_tileid(z, x, y), z, x, y) for z, x, y in fill_positions),
        key=lambda t: t[0],
    )
    if not entries:
        raise ValueError('No fill-eligible positions found -- nothing to write.')

    min_z, max_z = math.inf, 0
    min_lon, min_lat = math.inf, math.inf
    max_lon, max_lat = -math.inf, -math.inf

    tmp_out_path = f'{out_path}.tmp-{os.getpid()}'
    with open(tmp_out_path, 'wb') as out_f:
        writer = Writer(out_f)
        for tile_id, z, x, y in entries:
            writer.write_tile(tile_id, fill_blob)
            max_z, min_z = max(max_z, z), min(min_z, z)
            west, south, east, north = tile_bounds_deg(x, y, z)
            min_lon, min_lat = min(min_lon, west), min(min_lat, south)
            max_lon, max_lat = max(max_lon, east), max(max_lat, north)

        min_lon_e7, min_lat_e7 = int(min_lon * 1e7), int(min_lat * 1e7)
        max_lon_e7, max_lat_e7 = int(max_lon * 1e7), int(max_lat * 1e7)
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
            {'attribution': attribution},
        )
    os.replace(tmp_out_path, out_path)
    print(f'wrote {out_path}: {writer.addressed_tiles:_} addressed tiles, '
          f'{len(writer.tile_entries):_} directory entries, clustered={writer.clustered}')


def build_z0_7(overview_path, out_path):
    cells = load_glo30_cells()
    fill_blob = make_fill_tile_blob()
    fill_positions = []
    not_eligible = 0
    with open(overview_path, 'rb') as f:
        reader = Reader(MmapSource(f))
        for z in range(0, 8):
            n = 2 ** z
            for x in range(n):
                for y in range(n):
                    if reader.get(z, x, y):
                        continue
                    if fill_eligible(x, y, z, cells):
                        fill_positions.append((z, x, y))
                    else:
                        not_eligible += 1
    if not_eligible:
        # not expected (D174/D176 found 0 of these) -- surfacing loudly
        # rather than silently proceeding, since it would mean a genuine
        # z0-7 gap this script can't safely paper over
        print(f'WARNING: {not_eligible} z0-7 positions are absent but NOT '
              f'GLO-30-mask-eligible -- real gaps this fill does not cover.')
    write_fill_archive(
        fill_positions, fill_blob, out_path,
        'D174 synthetic fill (no real elevation data): 0m placeholder for '
        'z0-7 overview positions absent from the upstream Copernicus GLO-30 inventory.',
    )


def build_z8_z12(elevation_path, overview_path, z0_7_fill_path, out_path):
    cells = load_glo30_cells()
    fill_blob = make_fill_tile_blob()

    z0_7_fill = load_fill_positions(z0_7_fill_path)
    z7_fill_xy = {(x, y) for (z, x, y) in z0_7_fill if z == 7}

    def lon2x(lon, n):
        return int((lon + 180) / 360 * n)

    def lat2y(lat, n):
        lat_rad = math.radians(lat)
        return int((1 - math.asinh(math.tan(lat_rad)) / math.pi) / 2 * n)

    fill_positions = []
    fill_xy_by_zoom = {}
    with open(elevation_path, 'rb') as f_elev, open(overview_path, 'rb') as f_ov:
        r_elev = Reader(MmapSource(f_elev))
        r_ov = Reader(MmapSource(f_ov))
        for z in range(8, 13):
            n = 2 ** z
            x0, x1 = max(0, lon2x(BOX['west'], n)), min(n, lon2x(BOX['east'], n) + 1)
            y0, y1 = max(0, lat2y(BOX['north'], n)), min(n, lat2y(BOX['south'], n) + 1)
            this_zoom = set()
            for x in range(x0, x1):
                for y in range(y0, y1):
                    if r_elev.get(z, x, y):
                        continue
                    px, py = x // 2, y // 2
                    if z == 8:
                        # z8's parent lives in the z0-7 OVERVIEW archive, not
                        # the elevation archive -- resolvable if it's real
                        # there OR covered by the z0-7 fill (NOT "z0-7 fill
                        # alone is 100% complete" -- that was a real bug the
                        # first version of this promoted script reintroduced;
                        # the fill only covers what was PREVIOUSLY missing,
                        # the combination of real+fill is what's complete)
                        parent_ok = bool(r_ov.get(7, px, py)) or (px, py) in z7_fill_xy
                    else:
                        parent_ok = bool(r_elev.get(z - 1, px, py)) or (px, py) in fill_xy_by_zoom.get(z - 1, set())
                    if not parent_ok:
                        continue
                    if fill_eligible(x, y, z, cells):
                        this_zoom.add((x, y))
                        fill_positions.append((z, x, y))
            fill_xy_by_zoom[z] = this_zoom
            print(f'  z{z}: {len(this_zoom):_} fill-eligible-with-resolvable-parent')

    write_fill_archive(
        fill_positions, fill_blob, out_path,
        'D174 synthetic fill v2 (orphan-safe, no real elevation data): 0m placeholder '
        'for positions absent from both the real archive and the upstream Copernicus '
        'GLO-30 inventory, with a resolvable parent chain.',
    )


def build_z13_z16(elevation_path, out_path):
    """D182: fills every z13-z16 tile beneath a z12 'leaf' position (a real
    z12 tile with no real z13 child -- i.e. a position the pipeline never
    built past z12, either because it's a genuine GLO-30 inventory gap
    (D174's original mechanism) or because it's confirmed open ocean the
    pipeline simply had no reason to build finer than z12 for, D149's own
    "no value in 1m detail on open seafloor" design).

    Two independent, non-overlapping safety criteria decide eligibility --
    checked ONCE per z12 leaf, never per descendant:
      narrow: fill_eligible() against the GLOBAL GLO-30 inventory (D174's
              already-shipped, already-verified z8-z12 mask, unchanged).
      broad:  sea_fill_eligible() against jpnationalsea's OWN downloaded
              275-cell set -- NOT the global inventory, which also covers
              real foreign land (Korea/China/Russia/Taiwan) this project
              never downloaded and cannot vouch for. Only cells this
              project itself has verified real Copernicus bathymetry for
              are treated as confirmed ocean.

    Monotonic by construction (D182, verified): a child tile's footprint
    is a strict geographic subset of its parent's, so the set of 1-degree
    cells it touches is always a SUBSET of its parent's touched cells.
    Once a z12 leaf passes either criterion, every one of its 340 z13-z16
    descendants automatically passes the same criterion too -- no need to
    re-check, and no orphan risk, since every descendant of an eligible
    leaf is generated together as one complete subtree (never a partial
    one), the same "resolvable parent chain" invariant build_z8_z12's own
    top-down gate enforces, just satisfied by construction here instead of
    by an explicit per-position check."""
    glo30_cells = load_glo30_cells()
    jpnationalsea_cells = load_jpnationalsea_cells()
    fill_blob = make_fill_tile_blob()

    positions = enumerate_zoom_positions(elevation_path, [12, 13])
    z12, z13 = positions[12], positions[13]
    z13_parents = {(x // 2, y // 2) for (x, y) in z13}
    leaf_z12 = z12 - z13_parents
    print(f'z12 total: {len(z12):_}  with real z13 children: {len(z13_parents):_}  '
          f'leaf (fill candidates): {len(leaf_z12):_}')

    narrow_leaves, broad_leaves, ineligible = [], [], 0
    for (x, y) in leaf_z12:
        if fill_eligible(x, y, 12, glo30_cells):
            narrow_leaves.append((x, y))
        elif sea_fill_eligible(x, y, 12, jpnationalsea_cells):
            broad_leaves.append((x, y))
        else:
            ineligible += 1
    print(f'  narrow (GLO-30-absent, D174 criterion): {len(narrow_leaves):_}')
    print(f'  broad (jpnationalsea-confirmed ocean):   {len(broad_leaves):_}')
    print(f'  ineligible (neither -- left as genuine 204, real risk of '
          f'foreign land or unclassified): {ineligible:_}')

    fill_positions = []
    for (px, py) in narrow_leaves + broad_leaves:
        for z in range(13, 17):
            n_ancestors = z - 12
            for cx in range(px * (2 ** n_ancestors), (px + 1) * (2 ** n_ancestors)):
                for cy in range(py * (2 ** n_ancestors), (py + 1) * (2 ** n_ancestors)):
                    fill_positions.append((z, cx, cy))
    print(f'total z13-z16 fill positions: {len(fill_positions):_}')

    write_fill_archive(
        fill_positions, fill_blob, out_path,
        'D182 synthetic fill (no real elevation data): 0m placeholder for z13-z16 '
        'positions beneath a z12 leaf that is either absent from the upstream '
        'Copernicus GLO-30 global inventory (D174) or confirmed real ocean via '
        "this project's own jpnationalsea download (D182) -- never filled where "
        'neither is true, to avoid ever stamping over real (foreign) land.',
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='mode', required=True)

    p1 = sub.add_parser('z0-7', help="complete the z0-7 global overview's own gaps")
    p1.add_argument('--overview', required=True, help='path to the existing z0-7 global overview .pmtiles')
    p1.add_argument('--out', required=True)

    p2 = sub.add_parser('z8-z12', help='the staged fill for the elevation archive')
    p2.add_argument('--elevation', required=True, help='path to the real, current elevation .pmtiles')
    p2.add_argument('--overview', required=True, help="path to the SAME z0-7 global overview passed to this script's own z0-7 mode -- z8's own parent lives there, not in the elevation archive")
    p2.add_argument('--z0-7-fill', required=True, dest='z0_7_fill', help="output of this script's own z0-7 mode")
    p2.add_argument('--out', required=True)

    p3 = sub.add_parser('z13-z16', help='D182: fill beneath z12 leaves (GLO-30 gaps + confirmed ocean)')
    p3.add_argument('--elevation', required=True, help='path to the real, current elevation .pmtiles (must already include the z8-z12 fill spliced in)')
    p3.add_argument('--out', required=True)

    args = parser.parse_args()
    if args.mode == 'z0-7':
        build_z0_7(args.overview, args.out)
    elif args.mode == 'z8-z12':
        build_z8_z12(args.elevation, args.overview, args.z0_7_fill, args.out)
    else:
        build_z13_z16(args.elevation, args.out)


if __name__ == '__main__':
    main()
