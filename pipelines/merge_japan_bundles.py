# Ad hoc merge script for the japan-bridge effort (see
# hfu/mapterhorn-japan-bridge's DECISIONS.md D7): generalizes this repo's
# own merge_bundles.py (hardcoded to Freetown's two specific files) to
# glob every bundle-store/*.pmtiles instead, so it keeps working as
# coverage grows past Hokkaido. Not checked in to this fork (see
# FORK_NOTES.md's split between generic fixes and Freetown-specific
# tooling) -- lives in mapterhorn-japan-bridge's own HANDOVER.md instead.
from glob import glob
import os

import mercantile
from pmtiles.reader import Reader, MmapSource, all_tiles
from pmtiles.tile import TileType, Compression, zxy_to_tileid
from pmtiles.writer import Writer

OUTPUT = 'bundle-store/japan.pmtiles'
INPUTS = sorted(p for p in glob('bundle-store/*.pmtiles') if os.path.abspath(p) != os.path.abspath(OUTPUT))

def main():
    print(f'merging {len(INPUTS)} file(s): {INPUTS}')
    min_lon, min_lat, max_lon, max_lat = 180.0, 90.0, -180.0, -90.0
    total = 0

    with open(OUTPUT, 'wb') as out_f:
        writer = Writer(out_f)
        for path in INPUTS:
            with open(path, 'r+b') as in_f:
                reader = Reader(MmapSource(in_f))
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
