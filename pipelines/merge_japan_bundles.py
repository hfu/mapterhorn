# Ad hoc merge script for the japan-bridge effort (see
# hfu/mapterhorn-japan-bridge's DECISIONS.md D7): generalizes this repo's
# own merge_bundles.py (hardcoded to Freetown's two specific files) to
# glob every bundle-store/*.pmtiles instead, so it keeps working as
# coverage grows past Hokkaido. Committed to this fork (2026-08-09,
# `5609479` on the mapterhorn-japan-bridge side) -- an earlier version of
# this comment said otherwise; that was true only before that commit.
from glob import glob
import os

import mercantile
from pmtiles.reader import Reader, all_tiles
from pmtiles.tile import TileType, Compression, zxy_to_tileid
from pmtiles.writer import Writer

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

if __name__ == '__main__':
    main()
