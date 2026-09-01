#!/usr/bin/env python3
"""Audit downsampling `.done` markers against the pmtiles-store files
they claim to have produced (mapterhorn-japan-bridge DECISIONS.md
D53/D55): `aggregation_run.py`'s own in-place stale-file cleanup can
rename a `pmtiles-store` file (its filename encodes maxzoom, which
changes when a position's source composition changes) after a
downsampling item that referenced the old name has already been marked
`.done` -- this leaves a permanently stale marker. `downsampling_run.py`
`main()` only checks whether a `.done` file exists, never re-verifies
the archive it's supposed to guarantee, so a stale marker is never
retried on its own.

This is a repair tool, not something the normal publish cycle runs
automatically. Run `--fix` only once `aggregation_run.py` has fully
finished for this generation -- D53 found no stale markers newer than
that point, consistent with the race needing concurrent aggregation
reprocessing; running `--fix` while aggregation is still active would
race the exact same mechanism this tool cleans up, and could delete a
marker for a file that's correct and just hasn't been reprocessed yet.

Usage:
  uv run python3 check_downsampling_done_integrity.py <aggregation_id> [--fix]
"""
import argparse
import datetime
import os
from glob import glob

import utils


def audit(aggregation_id):
    agg_dir = f'aggregation-store/{aggregation_id}'
    done_files = glob(f'{agg_dir}/*-downsampling.done')
    print(f'total .done markers: {len(done_files):_}')

    stale = []
    healthy = 0
    for done_path in done_files:
        filename = os.path.basename(done_path)
        base = filename.replace('-downsampling.done', '')
        z, x, y, parent_zoom = [int(a) for a in base.split('-')]
        # z (the extent's own zoom), not parent_zoom -- matches
        # downsampling_run.py's own main() -> get_pmtiles_folder(extent_x,
        # extent_y, extent_z, layer='downsampling') call. This audits
        # downsampling_run.py's own output, always that layer (D95/D107).
        folder = utils.get_pmtiles_folder(x, y, z, layer='downsampling')
        filepath = f'{folder}/{base}.pmtiles'
        if os.path.isfile(filepath):
            healthy += 1
        else:
            stale.append((done_path, os.path.getmtime(done_path)))

    print(f'healthy (referenced file exists): {healthy:_}')
    print(f'stale (done, but referenced file missing): {len(stale):_}')
    if stale:
        stale.sort(key=lambda item: item[1])
        oldest_mtime = datetime.datetime.fromtimestamp(stale[0][1])
        newest_mtime = datetime.datetime.fromtimestamp(stale[-1][1])
        print(f'oldest stale marker mtime: {oldest_mtime}')
        print(f'newest stale marker mtime: {newest_mtime}')
    return [path for path, _ in stale]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('aggregation_id')
    parser.add_argument(
        '--fix', action='store_true',
        help='delete stale .done markers so downsampling_run.py retries them '
             '(only run once aggregation_run.py has fully finished -- see module docstring)')
    args = parser.parse_args()

    stale_paths = audit(args.aggregation_id)

    if args.fix:
        if not stale_paths:
            print('nothing to fix.')
            return
        for path in stale_paths:
            os.remove(path)
        print(f'deleted {len(stale_paths):_} stale .done markers -- '
              f're-run downsampling_run.py to rebuild them.')
    elif stale_paths:
        print('\n(dry run -- pass --fix to delete these markers)')


if __name__ == '__main__':
    main()
