# Ad hoc merge script for the japan-bridge effort (see
# hfu/mapterhorn-japan-bridge's DECISIONS.md D7): generalizes this repo's
# own merge_bundles.py (hardcoded to Freetown's two specific files) to
# glob every bundle-store/*.pmtiles instead, so it keeps working as
# coverage grows past Hokkaido. Committed to this fork (2026-08-09,
# `5609479` on the mapterhorn-japan-bridge side) -- an earlier version of
# this comment said otherwise; that was true only before that commit.
import json
from glob import glob
import os

import mercantile
from pmtiles.reader import Reader, all_tiles
from pmtiles.tile import TileType, Compression, zxy_to_tileid
from pmtiles.writer import Writer

from utils import run_command

# D104/D105 (mapterhorn-japan-bridge DECISIONS.md): the pmtiles library's
# Writer buffers tile bytes via tempfile.TemporaryFile() with no path
# argument, so it lands on tempfile.gettempdir() -- which honors TMPDIR,
# but macOS login/SSH sessions already export TMPDIR (pointing at the
# per-user /var/folders/.../T/ directory on the small boot volume) before
# this script ever runs, so os.environ.setdefault('TMPDIR', ...) (D104's
# first attempt) was always a no-op: the key is never actually absent.
# Force-override unconditionally instead.
os.environ['TMPDIR'] = os.path.abspath('pmtiles-store/tmp-store/writer-scratch/')
os.makedirs(os.environ['TMPDIR'], exist_ok=True)
import tempfile
tempfile.tempdir = None  # drop any cached resolution from before this line ran

# D93/D96/D107/D109: 'elevation' (default, 1号's only mode) or 'lineage'.
# japan.pmtiles before mapterhorn-japan-bridge DECISIONS.md D46.
#
# Naming (D109 refactor -- resolves the D103 ENOSPC incident's root cause,
# an ambiguous pair of files where the with-overview/without-overview
# distinction lived only in which one someone remembered to delete):
#   elevation: this step's own output is an INTERMEDIATE, never published
#   directly -- pmtiles_merge.py still needs to splice in Mapterhorn's
#   global z0-7 overview before it's publishable. ".z8plus" makes that
#   explicit (echoes the pre-D46 "japan-z8plus.pmtiles" name). Only the
#   final, overview-spliced archive is ever named plain
#   "mapterhorn-japan-bridge.pmtiles" -- that name now refers to exactly
#   one thing, never two candidates someone has to pick between.
#   lineage: no global-overview splice applies (Mapterhorn's own global
#   product has no provenance/lineage data to splice in -- lineage is
#   Japan-only end to end), so this step's own output IS the final,
#   publishable lineage archive already.
MERGE_DATATYPE = os.environ.get('MERGE_DATATYPE', 'elevation')
if MERGE_DATATYPE == 'lineage':
    OUTPUT = 'bundle-store/mapterhorn-japan-bridge-lineage.pmtiles'
else:
    OUTPUT = 'bundle-store/mapterhorn-japan-bridge.z8plus.pmtiles'
# Datatype-scoped: bundle-store holds both datatypes' regional archives
# side by side (distinguished by BUNDLE_DATATYPE's own "-lineage" filename
# suffix, D107) -- a naive glob would merge them together into one corrupt
# archive, so include only this datatype's own files.
_is_lineage_file = lambda p: p.endswith('-lineage.pmtiles')
INPUTS = sorted(
    p for p in glob('bundle-store/*.pmtiles')
    if os.path.abspath(p) != os.path.abspath(OUTPUT)
    and _is_lineage_file(p) == (MERGE_DATATYPE == 'lineage')
)


def assert_inputs_complete():
    """D117/D119: this script deletes each INPUTS file as it consumes it
    (see the os.remove(path) comment below), so a run interrupted partway
    through (e.g. the 2026-09-02 ENOSPC crash) leaves a PARTIAL bundle-store
    on disk, and a naive re-run's glob silently treats that partial set as
    complete -- exactly what happened on 9/2, publishing 14 of 23 regional
    bundles with all of western Japan's z13+ missing, undetected until
    D117's investigation. bundle*.py's own meta-store/bundle/*.json (one
    file per region, written by that script only after the region's build
    succeeds) is the authoritative "what should be here" list -- use it as
    a completeness gate before merging anything.
    """
    manifest_dir = 'meta-store/bundle'
    expected = {}
    for meta_path in sorted(glob(f'{manifest_dir}/*.json')):
        name = os.path.basename(meta_path)[:-len('.json')]
        is_lineage_manifest = name.endswith('-lineage')
        if is_lineage_manifest != (MERGE_DATATYPE == 'lineage'):
            continue
        with open(meta_path) as mf:
            expected[f'bundle-store/{name}.pmtiles'] = json.load(mf)

    missing = sorted(p for p in expected if not os.path.isfile(p))
    wrong_size = sorted(
        (p, expected[p]['size'], os.path.getsize(p)) for p in expected
        if p not in missing and os.path.getsize(p) != expected[p]['size']
    )
    extra = sorted(set(INPUTS) - set(expected))

    if missing or wrong_size or extra:
        raise SystemExit(
            'REFUSING TO MERGE -- bundle-store is not the complete, current '
            'set of regional bundles (see mapterhorn-japan-bridge '
            'DECISIONS.md D117/D119).\n'
            f'  missing ({len(missing)}): {missing}\n'
            f'  size mismatch ({len(wrong_size)}): {wrong_size}\n'
            f'  present but not in {manifest_dir}/ ({len(extra)}): {extra}'
        )
    total_gib = sum(v['size'] for v in expected.values()) / 2**30
    print(f'completeness check OK: {len(expected)} bundle(s), {total_gib:.1f} GiB')


def FileSource(f):
    """Seek+read in place of pmtiles.reader.MmapSource. MmapSource maps the
    whole file and never calls madvise() to release pages it's already
    scanned past, so a single straight-through pass over one large archive
    (e.g. bundle-store's own consolidated regional files, tens of GB each)
    lets that file's resident pages accumulate toward the file's full size
    -- measured directly on slate (16GB RAM, see DECISIONS.md D24):
    merge_japan_bundles.py's RSS hit ~9GB / 56% of physical memory reading
    a single 42.9GB regional bundle, with the machine down to ~450MB free
    and starting to swap, while every other job on the box (jpnational1's
    long-running download included) was still competing for that same
    memory. Only this fork's own ad hoc script needed changing -- the
    upstream pmtiles library itself is untouched. Not thread-safe (shared
    file position), fine here since every INPUTS file is read start-to-end
    by a single sequential loop, never concurrently."""
    def get_bytes(offset, length):
        f.seek(offset)
        return f.read(length)
    return get_bytes


def main():
    assert_inputs_complete()
    print(f'merging {len(INPUTS)} file(s): {INPUTS}')
    min_lon, min_lat, max_lon, max_lat = 180.0, 90.0, -180.0, -90.0
    total = 0

    with open(OUTPUT, 'wb') as out_f:
        writer = Writer(out_f)
        for path in INPUTS:
            # Larger-than-default buffer: seek+read trades mmap's page-fault
            # driven readahead for explicit syscalls, so a bigger buffer
            # cuts syscall count back down on the mostly-forward-moving
            # (directory hop, then near-sequential tile-data) access
            # pattern all_tiles() produces.
            with open(path, 'rb', buffering=8 * 1024 * 1024) as in_f:
                reader = Reader(FileSource(in_f))
                for tile_tuple, tile_bytes in all_tiles(reader.get_bytes):
                    z, x, y = tile_tuple
                    tile_id = zxy_to_tileid(z, x, y)
                    writer.write_tile(tile_id, tile_bytes)
                    west, south, east, north = mercantile.bounds(x, y, z)
                    min_lon = min(min_lon, west)
                    min_lat = min(min_lat, south)
                    max_lon = max(max_lon, east)
                    max_lat = max(max_lat, north)
                    total += 1
                    if total % 100000 == 0:
                        print(f'{total:_} tiles written...')
            print(f'done with {path}, total so far: {total:_}')
            # DECISIONS.md D49/D53: without this, every already-consumed
            # INPUTS file stays on disk for the rest of the run, coexisting
            # with the pmtiles Writer's own scratch temp file (which itself
            # needs ~1x the final archive's tile-data size until
            # finalize()'s copy completes) -- together needing roughly 2x
            # the archive's own size in headroom at peak, which drove
            # `slate` to 13Gi free mid-run on 2026-08-28. Each `path` here
            # has already been fully read (the `with` block above is
            # closed), and `bundle.py` always rebuilds bundle-store fully
            # (dirty_only=False, D44) every cycle regardless, so deleting
            # it now costs nothing beyond a cheap regenerate if this script
            # crashes later -- same trade this project already accepted.
            os.remove(path)

        min_lon_e7 = int(min_lon * 1e7)
        min_lat_e7 = int(min_lat * 1e7)
        max_lon_e7 = int(max_lon * 1e7)
        max_lat_e7 = int(max_lat * 1e7)

        writer.finalize(
            {
                'tile_type': TileType.WEBP,
                'tile_compression': Compression.NONE,
                'min_lon_e7': min_lon_e7,
                'min_lat_e7': min_lat_e7,
                'max_lon_e7': max_lon_e7,
                'max_lat_e7': max_lat_e7,
                'center_zoom': 12,
                'center_lon_e7': int(140.9 * 1e7),
                'center_lat_e7': int(41.85 * 1e7),
            },
            {
                'attribution': '国土地理院 (GSI Japan). Processed with Mapterhorn (japan-bridge, interim).',
            },
        )
    print(f'wrote {OUTPUT}, {total:_} tiles total')

    # D140/D143: cluster every datatype's output here, unconditionally, so
    # it's a property of this script rather than a runbook step someone
    # has to remember per datatype. Originally only elevation's z8plus was
    # clustered by hand (D127) on the assumption lineage's small size made
    # it not worth the trouble -- measuring it instead (D143) found
    # lineage dedupes far MORE than elevation (~83.5% duplicate tile
    # content vs ~19%, since its categorical single-band values produce
    # huge byte-identical regions like contiguous sea), at a ~3.4s cost.
    # No datatype has a reason to skip this. For elevation this clusters
    # the .z8plus intermediate before pmtiles_merge.py's global-overview
    # splice (the splice output already inherits clustered ordering, per
    # D141's observation); for lineage, whose output here is already the
    # final publishable file (see the OUTPUT naming comment above), this
    # is the last step before publish.
    print(f'clustering {OUTPUT}...')
    run_command(f'./pmtiles cluster {OUTPUT}', silent=False)

if __name__ == '__main__':
    main()
