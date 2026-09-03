"""Fetch tiles.mapterhorn.com's own global terrarium/webp tiles for z0..7
and write them into a standalone pmtiles archive, for later splicing (via
`pmtiles merge`) with mapterhorn-japan-bridge's own z8+ output.

Rationale (2026-08-30, Hidenori's design): mapterhorn-japan-bridge's own
z0-7 pyramid has real, structural gaps in deep-ocean areas far from any
Japan coastline (no source coverage at any zoom -- not a bug, genuinely
no data). Those gaps are only visually obvious at z0-7's zoomed-out
views (nobody navigates deep mid-Pacific ocean at z8+, so the identical
gap there is practically invisible). Rather than trying to patch our
own z0-7 pyramid, splice in Mapterhorn's own mature global product
wholesale for z0-7, and keep our own authoritative Japan data for z8+
unchanged. Since z0-7 and z8-16 share no tile IDs, `pmtiles merge`
(disjoint-archive merge) can combine them directly with no bespoke
merge logic needed.

encoding matches exactly (terrarium, webp, tileSize 512) per
tiles.mapterhorn.com's own tilejson -- no re-encoding needed, tile
bytes are used as-is.

Usage: uv run python3 build_global_overview.py [--maxzoom 7] [--out bundle-store/global-overview.pmtiles]
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

import argparse
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from pmtiles.tile import zxy_to_tileid
from pmtiles.writer import Writer

TILE_URL = 'https://tiles.mapterhorn.com/{z}/{x}/{y}.webp'


def fetch_tile(z, x, y, session, retries=3):
    url = TILE_URL.format(z=z, x=x, y=y)
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 200:
                return (z, x, y), r.content
            if r.status_code == 404:
                return (z, x, y), None
        except requests.RequestException:
            pass
        time.sleep(0.5 * (attempt + 1))
    return (z, x, y), None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--maxzoom', type=int, default=7)
    parser.add_argument('--out', default='bundle-store/global-overview.pmtiles')
    parser.add_argument('--workers', type=int, default=16)
    args = parser.parse_args()

    jobs = []
    for z in range(args.maxzoom + 1):
        n = 2 ** z
        for x in range(n):
            for y in range(n):
                jobs.append((z, x, y))

    print(f'fetching {len(jobs):_} tiles (z0-{args.maxzoom}) from tiles.mapterhorn.com '
          f'with {args.workers} workers...')

    tiles = {}
    missing = 0
    session = requests.Session()
    start = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(fetch_tile, z, x, y, session) for z, x, y in jobs]
        done = 0
        for future in as_completed(futures):
            (z, x, y), content = future.result()
            done += 1
            if content is not None:
                tile_id = zxy_to_tileid(z, x, y)
                tiles[tile_id] = content
            else:
                missing += 1
            if done % 2000 == 0:
                elapsed = time.time() - start
                print(f'{done:_} / {len(jobs):_} fetched ({elapsed:.0f}s elapsed, '
                      f'{missing} missing so far)...')

    print(f'fetch complete: {len(tiles):_} tiles, {missing} missing/404, '
          f'{time.time() - start:.0f}s total')

    min_lon, min_lat, max_lon, max_lat = 180.0, 90.0, -180.0, -90.0
    with open(args.out, 'wb') as f:
        writer = Writer(f)
        for tile_id in sorted(tiles.keys()):
            writer.write_tile(tile_id, tiles[tile_id])
        # bounds are the whole world by construction (tiles.mapterhorn.com's own tilejson)
        min_lon, min_lat, max_lon, max_lat = -180.0, -85.0511, 180.0, 85.0511
        from pmtiles.tile import TileType, Compression
        writer.finalize(
            {
                'tile_type': TileType.WEBP,
                'tile_compression': Compression.NONE,
                'min_zoom': 0,
                'max_zoom': args.maxzoom,
                'min_lon_e7': int(min_lon * 1e7),
                'min_lat_e7': int(min_lat * 1e7),
                'max_lon_e7': int(max_lon * 1e7),
                'max_lat_e7': int(max_lat * 1e7),
                'center_zoom': 2,
                'center_lon_e7': 0,
                'center_lat_e7': 0,
            },
            {
                'attribution': '© Mapterhorn (https://mapterhorn.com/attribution)',
            },
        )
    print(f'wrote {args.out}, {len(tiles):_} tiles')


if __name__ == '__main__':
    main()
