import mercantile
from pmtiles.reader import Reader, MmapSource, all_tiles
from pmtiles.tile import TileType, Compression
from pmtiles.writer import Writer

INPUTS = ['bundle-store/planet.pmtiles', 'bundle-store/6-29-30.pmtiles']
OUTPUT = 'bundle-store/freetown-mapterhorn.pmtiles'

def main():
    min_lon, min_lat, max_lon, max_lat = 180.0, 90.0, -180.0, -90.0
    total = 0

    with open(OUTPUT, 'wb') as out_f:
        writer = Writer(out_f)
        for path in INPUTS:
            with open(path, 'r+b') as in_f:
                reader = Reader(MmapSource(in_f))
                for tile_tuple, tile_bytes in all_tiles(reader.get_bytes):
                    z, x, y = tile_tuple
                    from pmtiles.tile import zxy_to_tileid
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
                'center_zoom': 17,
                'center_lon_e7': int(-13.2989501 * 1e7),
                'center_lat_e7': int(8.4995373 * 1e7),
            },
            {
                # Source imagery: "Freetown Urban with Sensitive Areas Blurred" by Ivan
                # Gayton / DroneTM / HOTOSM, via OpenAerialMap (CC-BY 4.0 - OAM's standard
                # license; this record doesn't set its own license field, so the platform
                # default applies). Processed with Mapterhorn - the generic
                # mapterhorn.com/attribution link is for the upstream project's own
                # multi-source catalog, not this derived single-source archive, so it does
                # not by itself satisfy CC-BY's attribution requirement for the source data.
                'attribution': (
                    'Imagery: Ivan Gayton / DroneTM / HOTOSM, via '
                    '<a href="https://map.openaerialmap.org">OpenAerialMap</a> (CC-BY 4.0). '
                    'Processed with <a href="https://mapterhorn.com">Mapterhorn</a>.'
                ),
            },
        )
    print(f'wrote {OUTPUT}, {total:_} tiles total')

if __name__ == '__main__':
    main()
