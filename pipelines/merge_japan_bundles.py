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

# D104 (mapterhorn-japan-bridge DECISIONS.md): the pmtiles library's Writer
# buffers tile bytes via tempfile.TemporaryFile() with no path argument, so
# it silently lands on the OS default temp dir (TMPDIR, usually the boot
# volume) instead of the output volume -- on a machine where the real data
# lives on a much larger external volume than the boot disk, concurrent
# workers can exhaust the boot disk's small headroom long before the
# output volume fills up, surfacing as a confusing ENOSPC unrelated to the
# volume actually holding the data. setdefault() so an explicit TMPDIR
# (e.g. set by the caller for a different layout) still wins.
os.environ.setdefault('TMPDIR', os.path.abspath('pmtiles-store/tmp-store/writer-scratch/'))
os.makedirs(os.environ['TMPDIR'], exist_ok=True)

OUTPUT = 'bundle-store/mapterhorn-japan-bridge.pmtiles'  # japan.pmtiles before mapterhorn-japan-bridge DECISIONS.md D46
INPUTS = sorted(p for p in glob('bundle-store/*.pmtiles') if os.path.abspath(p) != os.path.abspath(OUTPUT))


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
