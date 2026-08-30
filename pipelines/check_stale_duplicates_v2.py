"""
D74's original stale-duplicate audit merged aggregation-layer and
downsampling-layer (z,x,y)->child_z into ONE dict, which is exactly the
cross-namespace collision D76 root-caused. This is a corrected, read-only
re-scan that keeps the two layers in separate namespaces and additionally
allows downsampling positions to have MULTIPLE legitimate child_z values
(D75's finding), to determine how many pmtiles-store files are *actually*
unexplained by either layer's own CSV inventory.

Usage: run from hfu-mapterhorn/pipelines/ on slate.
  python3 check_stale_duplicates_v2.py [--aggregation-id ID]
Dry-run only. Prints counts, does not delete anything.
"""
import sys
from glob import glob
from collections import defaultdict

import utils


def load_aggregation_correct(aggregation_id):
    correct = {}
    for filepath in glob(f'aggregation-store/{aggregation_id}/*-aggregation.csv'):
        filename = filepath.split('/')[-1]
        z, x, y, child_z = [int(a) for a in filename.replace('-aggregation.csv', '').split('-')]
        correct[(z, x, y)] = child_z
    return correct


def load_downsampling_correct(aggregation_id):
    correct = defaultdict(set)
    for filepath in glob(f'aggregation-store/{aggregation_id}/*-downsampling.csv'):
        filename = filepath.split('/')[-1]
        z, x, y, child_z = [int(a) for a in filename.replace('-downsampling.csv', '').split('-')]
        correct[(z, x, y)].add(child_z)
    return correct


def main():
    aggregation_ids = utils.get_aggregation_ids()
    aggregation_id = aggregation_ids[-1]
    if '--aggregation-id' in sys.argv:
        aggregation_id = sys.argv[sys.argv.index('--aggregation-id') + 1]
    print(f'using aggregation_id={aggregation_id}')

    agg_correct = load_aggregation_correct(aggregation_id)
    ds_correct = load_downsampling_correct(aggregation_id)
    print(f'aggregation positions: {len(agg_correct)}')
    print(f'downsampling positions: {len(ds_correct)}')

    all_files = glob('pmtiles-store/*.pmtiles') + glob('pmtiles-store/*/*.pmtiles')
    print(f'total pmtiles-store files: {len(all_files)}')

    by_position = defaultdict(list)
    for filepath in all_files:
        filename = filepath.split('/')[-1]
        try:
            z, x, y, child_z = [int(a) for a in filename.replace('.pmtiles', '').split('-')]
        except ValueError:
            print(f'SKIP (unparseable): {filepath}')
            continue
        by_position[(z, x, y)].append((child_z, filepath))

    print(f'distinct z-x-y positions: {len(by_position)}')

    orphan_count = 0
    orphan_size = 0
    true_stale_count = 0
    true_stale_size = 0
    multi_but_all_legit = 0

    import os

    for pos, entries in by_position.items():
        z, x, y = pos
        agg_valid = agg_correct.get(pos)
        ds_valid = ds_correct.get(pos, set())

        legit_child_zs = set()
        if agg_valid is not None:
            legit_child_zs.add(agg_valid)
        legit_child_zs |= ds_valid

        if len(entries) > 1 and len(legit_child_zs) >= len(entries):
            multi_but_all_legit += 1

        for child_z, filepath in entries:
            size = os.path.getsize(filepath)
            if child_z not in legit_child_zs:
                if len(legit_child_zs) == 0:
                    orphan_count += 1
                    orphan_size += size
                else:
                    true_stale_count += 1
                    true_stale_size += size

    print()
    print('--- corrected classification (per-layer, allowing multi-child_z downsampling) ---')
    print(f'positions with >1 file, all explained by legit child_z values (aggregation OR downsampling): {multi_but_all_legit}')
    print(f'orphan files (no CSV at all references this position, in either layer): {orphan_count}, {orphan_size / 1e9:.2f} GB')
    print(f'TRUE stale files (position has a legit child_z, but this file is a different, unreferenced child_z): {true_stale_count}, {true_stale_size / 1e9:.2f} GB')


if __name__ == '__main__':
    main()
