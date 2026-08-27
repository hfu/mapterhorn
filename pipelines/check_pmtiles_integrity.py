"""Comprehensive integrity check for a single merged .pmtiles archive:
verifies every tile has a parent at the next-coarser zoom (no orphaned
children -- would indicate a hole in the downsampling pyramid), reports
tile counts per zoom, and basic bounding-box sanity.

Walks the PMTiles directory tree directly (root -> leaf directories)
without ever fetching actual tile image bytes -- only the tiny
directory entries -- so this stays cheap even against a 200+GB archive.
Uses seek+read (not mmap) for the same reason merge_japan_bundles.py
does (D24): a single straight-through pass over one huge file would
otherwise accumulate resident pages toward the whole file's size.

Usage: uv run python3 check_pmtiles_integrity.py <path-to.pmtiles>
"""
import sys
import time
from collections import defaultdict

from pmtiles.reader import deserialize_header, deserialize_directory, tileid_to_zxy


def FileSource(f):
    def get_bytes(offset, length):
        f.seek(offset)
        return f.read(length)
    return get_bytes


def enumerate_tile_ids(get_bytes, header, dir_offset, dir_length):
    """Like pmtiles.reader.traverse(), but yields only tile_ids -- never
    calls get_bytes() for the actual tile data offset/length, only for
    the (tiny) directory entries themselves."""
    entries = deserialize_directory(get_bytes(dir_offset, dir_length))
    for entry in entries:
        if entry.run_length > 0:
            for i in range(entry.run_length):
                yield entry.tile_id + i
        else:
            yield from enumerate_tile_ids(
                get_bytes, header,
                header['leaf_directory_offset'] + entry.offset,
                entry.length,
            )


def check(path):
    start = time.time()
    with open(path, 'rb', buffering=8 * 1024 * 1024) as f:
        get_bytes = FileSource(f)
        header = deserialize_header(get_bytes(0, 127))
        print(f'header: min_zoom={header["min_zoom"]} max_zoom={header["max_zoom"]} '
              f'tile_count={header["addressed_tiles_count"]:_} '
              f'bounds=({header["min_lon_e7"]/1e7:.4f},{header["min_lat_e7"]/1e7:.4f})-'
              f'({header["max_lon_e7"]/1e7:.4f},{header["max_lat_e7"]/1e7:.4f})')

        by_zoom = defaultdict(set)  # zoom -> set of (x, y)
        total = 0
        for tile_id in enumerate_tile_ids(get_bytes, header, header['root_offset'], header['root_length']):
            z, x, y = tileid_to_zxy(tile_id)
            by_zoom[z].add((x, y))
            total += 1
            if total % 1_000_000 == 0:
                print(f'  enumerated {total:_} tile ids so far ({time.time()-start:.0f}s)...')

    print(f'enumerated {total:_} tile ids total in {time.time()-start:.0f}s')
    print('tiles per zoom:')
    for z in sorted(by_zoom.keys()):
        print(f'  z{z}: {len(by_zoom[z]):_}')

    max_z = max(by_zoom.keys())
    min_z = min(by_zoom.keys())
    orphans_by_zoom = {}
    total_orphans = 0
    for z in range(max_z, min_z, -1):
        parent_set = by_zoom.get(z - 1, set())
        orphans = [(x, y) for (x, y) in by_zoom[z] if (x // 2, y // 2) not in parent_set]
        if orphans:
            orphans_by_zoom[z] = orphans
            total_orphans += len(orphans)

    print(f'\n=== orphan check (child tile with no parent at z-1) ===')
    if total_orphans == 0:
        print('CLEAN: every tile at every zoom > min_zoom has a parent one zoom coarser.')
    else:
        print(f'FOUND {total_orphans:_} orphaned tiles across {len(orphans_by_zoom)} zoom level(s):')
        for z, orphans in sorted(orphans_by_zoom.items()):
            print(f'  z{z}: {len(orphans):_} orphans, e.g. {orphans[:5]}')

    print(f'\ntotal check time: {time.time()-start:.0f}s')
    return total_orphans


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print('Usage: check_pmtiles_integrity.py <path-to.pmtiles>')
        sys.exit(1)
    orphan_count = check(sys.argv[1])
    sys.exit(1 if orphan_count > 0 else 0)
