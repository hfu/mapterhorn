#!/usr/bin/env python3
"""Report readiness of the active generation's downsampling items without
running anything (mapterhorn-japan-bridge DECISIONS.md D25 point 4's
"readiness filter", read-only report half).

A downsampling item (one `*-downsampling.csv` file) is "ready" when every
PMTiles file it references already exists on disk -- the same check
`downsampling_run.py`'s `main()` performs inline when `DOWNSAMPLING_STRICT`
is set, extracted here as a standalone, no-side-effects report so readiness
can be inspected before committing to a real (STRICT) run. This script does
not touch `aggregation-store`/`pmtiles-store` at all.

Usage: python3 check_downsampling_readiness.py [--list-not-ready N]
"""
import argparse
from glob import glob

import utils


def candidate_filepaths(aggregation_id):
    # DECISIONS.md D51/D56: this used to re-derive downsampling_run.py's
    # own __main__ dirty-filter (is_parent_of_dirty_aggregation_tile /
    # not_in_previous_aggregation, comparing against aggregation_ids[-2]).
    # That filter compared against an unrelated old test generation and
    # silently excluded 68-78% of still-incomplete coarse-zoom items --
    # D51 removed it from downsampling_run.py itself; removed here too so
    # this report stays consistent with what a real run actually
    # considers, rather than reproducing the same undercount.
    return list(glob(f'aggregation-store/{aggregation_id}/*-downsampling.csv'))


def check_readiness(filepath):
    """Returns (is_ready, is_already_done, referenced_count, missing_count)."""
    if utils.os.path.isfile(filepath.replace('-downsampling.csv', '-downsampling.done')):
        return True, True, None, 0

    with open(filepath) as f:
        pmtiles_filenames = [a.strip() for a in f.readlines()[1:]]

    missing = 0
    for pmtiles_filename in pmtiles_filenames:
        file_z, file_x, file_y, _ = [int(a) for a in
                                      pmtiles_filename.replace('.pmtiles', '').split('-')]
        pmtiles_folder = utils.get_pmtiles_folder(file_x, file_y, file_z)
        if not utils.os.path.isfile(f'{pmtiles_folder}/{pmtiles_filename}'):
            missing += 1

    return missing == 0, False, len(pmtiles_filenames), missing


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--list-not-ready', type=int, default=10,
                         help='how many not-ready items to list (default 10)')
    args = parser.parse_args()

    aggregation_ids = utils.get_aggregation_ids()
    aggregation_id = aggregation_ids[-1]
    filepaths = candidate_filepaths(aggregation_id)

    already_done = ready_not_done = not_ready = 0
    not_ready_examples = []
    for filepath in sorted(filepaths):
        is_ready, is_done, referenced, missing = check_readiness(filepath)
        if is_done:
            already_done += 1
        elif is_ready:
            ready_not_done += 1
        else:
            not_ready += 1
            if len(not_ready_examples) < args.list_not_ready:
                not_ready_examples.append(
                    (filepath.split('/')[-1], missing, referenced))

    print(f'Active generation: {aggregation_id}')
    print(f'Candidate downsampling items: {len(filepaths)}')
    print(f'  already .done:        {already_done}')
    print(f'  ready, not yet run:   {ready_not_done}')
    print(f'  not ready (children missing): {not_ready}')
    if not_ready_examples:
        print(f'\nFirst {len(not_ready_examples)} not-ready items (missing/referenced children):')
        for name, missing, referenced in not_ready_examples:
            print(f'  {name}: {missing}/{referenced} children missing')


if __name__ == '__main__':
    main()
