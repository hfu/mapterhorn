"""Extract all z>=8 tiles from mapterhorn-japan-bridge's own merged output
into a standalone archive, so it can be `pmtiles merge`d with a separately
built z0-7 global overview (build_global_overview.py). Mirrors
merge_japan_bundles.py's own single-large-file read pattern (D24: seek+read
FileSource instead of MmapSource, so RSS doesn't accumulate toward the
source file's full size over one straight-through pass).

`pmtiles extract --minzoom=8` was tried first but refuses ("source archive
must be clustered for extracts") since merge_japan_bundles.py's own output
isn't byte-clustered (it concatenates bundle files in filename order, not
globally tile_id order). Reading via pmtiles.reader.all_tiles() and
rewriting through our own Writer sidesteps this -- the PMTiles directory
structure is tile_id-sorted by construction regardless of physical byte
layout, so this produces a correctly-ordered output without needing to
cluster the multi-hundred-GB source first.

Usage: uv run python3 extract_z8plus.py <input.pmtiles> <output.pmtiles>
"""
import os

# D104/D105/D120 Fable review item #2: pmtiles.writer.Writer buffers via
# tempfile.TemporaryFile(), which lands on the small boot volume unless
# TMPDIR is force-overridden before tempfile resolves it (setdefault is a
# no-op -- macOS sessions always have TMPDIR set already). Same block as
# bundle.py/merge_japan_bundles.py/aggregation_run.py.
os.environ['TMPDIR'] = os.path.abspath('pmtiles-store/tmp-store/writer-scratch/')
os.makedirs(os.environ['TMPDIR'], exist_ok=True)
import tempfile
tempfile.tempdir = None  # drop any cached resolution from before this line ran

import sys

import mercantile
from pmtiles.reader import Reader, all_tiles
from pmtiles.tile import TileType, Compression, zxy_to_tileid
from pmtiles.writer import Writer


def FileSource(f):
    def get_bytes(offset, length):
        f.seek(offset)
        return f.read(length)
    return get_bytes


def main():
    input_path, output_path = sys.argv[1], sys.argv[2]
    min_lon, min_lat, max_lon, max_lat = 180.0, 90.0, -180.0, -90.0
    total = 0
    max_z = 0

    with open(output_path, 'wb') as out_f:
        writer = Writer(out_f)
        with open(input_path, 'rb', buffering=8 * 1024 * 1024) as in_f:
            reader = Reader(FileSource(in_f))
            for tile_tuple, tile_bytes in all_tiles(reader.get_bytes):
                z, x, y = tile_tuple
                if z < 8:
                    continue
                tile_id = zxy_to_tileid(z, x, y)
                writer.write_tile(tile_id, tile_bytes)
                west, south, east, north = mercantile.bounds(x, y, z)
                min_lon = min(min_lon, west)
                min_lat = min(min_lat, south)
                max_lon = max(max_lon, east)
                max_lat = max(max_lat, north)
                max_z = max(max_z, z)
                total += 1
                if total % 200000 == 0:
                    print(f'{total:_} tiles written...')

        writer.finalize(
            {
                'tile_type': TileType.WEBP,
                'tile_compression': Compression.NONE,
                'min_zoom': 8,
                'max_zoom': max_z,
                'min_lon_e7': int(min_lon * 1e7),
                'min_lat_e7': int(min_lat * 1e7),
                'max_lon_e7': int(max_lon * 1e7),
                'max_lat_e7': int(max_lat * 1e7),
                'center_zoom': 12,
                'center_lon_e7': int(140.9 * 1e7),
                'center_lat_e7': int(41.85 * 1e7),
            },
            {
                'attribution': '国土地理院 (GSI Japan). Processed with Mapterhorn (japan-bridge, interim).',
            },
        )
    print(f'wrote {output_path}, {total:_} tiles total (z8-{max_z})')


if __name__ == '__main__':
    main()
