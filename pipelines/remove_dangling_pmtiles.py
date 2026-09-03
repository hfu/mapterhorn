"""Remove pmtiles-store files no longer expected by their generation's
covering (rewritten 2026-09-04, mapterhorn-japan-bridge DECISIONS.md D120
Fable review item #5).

The previous version was structurally unsafe in exactly the way that
caused D74-D76's 3,344-file loss:
  * it baselined against ONLY the latest aggregation-store generation,
    then scanned a SHARED flat pmtiles-store -- so every other
    generation's files (older, still-published data included) looked
    "dangling" and were deleted;
  * it compared bare filenames, so a filename expected by generation A
    protected an unrelated generation B file (and vice versa);
  * it deleted immediately, with no dry run and no confirmation.

This version closes all three holes:
  * it operates on exactly ONE generation, named explicitly on the
    command line (never inferred from "latest"), and scans ONLY that
    generation's own `pmtiles-store/{layer}/{datatype}/{generation_id}/`
    subtrees -- other generations' files are structurally out of reach,
    it cannot even see them;
  * the legacy flat layout (1-go, pre-D107) is refused entirely: those
    files live outside any generation subtree, so no automated cleanup
    here can safely reason about them;
  * dry-run is the DEFAULT; deletion requires the explicit --delete flag.

Usage:
  uv run python3 remove_dangling_pmtiles.py <generation_id>            # report only
  uv run python3 remove_dangling_pmtiles.py <generation_id> --delete   # actually remove
"""
import argparse
import os
from glob import glob

import utils


def find_dangling(generation_id):
    agg_dir = f'aggregation-store/{generation_id}'
    if not os.path.isdir(agg_dir):
        raise SystemExit(f'no such generation: {agg_dir} does not exist')

    filepaths = glob(f'{agg_dir}/*-aggregation.csv')
    filepaths += glob(f'{agg_dir}/*-downsampling.csv')

    expected_pmtiles_filenames = set()
    for filepath in filepaths:
        filename = filepath.split('/')[-1]
        expected_pmtiles_filenames.add(
            filename.replace('-aggregation.csv', '.pmtiles')
                    .replace('-downsampling.csv', '.pmtiles'))

    dangling = []
    present = 0
    for layer in utils.LAYERS:
        for datatype in utils.DATATYPES:
            root = f'pmtiles-store/{layer}/{datatype}/{generation_id}'
            for pmtiles_filepath in sorted(
                    glob(f'{root}/*.pmtiles') + glob(f'{root}/*/*.pmtiles')):
                present += 1
                if pmtiles_filepath.split('/')[-1] not in expected_pmtiles_filenames:
                    dangling.append(pmtiles_filepath)

    print(f'generation: {generation_id}')
    print(f'num expected filenames (from covering CSVs): {len(expected_pmtiles_filenames)}')
    print(f'num present files (this generation\'s subtrees only): {present}')
    print(f'num dangling: {len(dangling)}')
    return dangling


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('generation_id',
                        help='the aggregation-store ULID whose subtree to clean '
                             '(explicit on purpose -- never inferred from "latest")')
    parser.add_argument('--delete', action='store_true',
                        help='actually delete; without this flag, report only (dry run)')
    args = parser.parse_args()

    if args.generation_id == utils.FLAT_LEGACY_GENERATION_ID:
        raise SystemExit(
            f'REFUSING: {args.generation_id} is 1-go, whose data lives in the '
            f'legacy flat pmtiles-store layout outside any generation subtree. '
            f'This tool cannot reason safely about that layout -- clean 1-go '
            f'only by hand, with a human-reviewed file list (see D74-D76).')

    dangling = find_dangling(args.generation_id)

    if not dangling:
        print('nothing dangling.')
        return

    for path in dangling:
        if args.delete:
            print(f'Removing {path}...')
            os.remove(path)
        else:
            print(f'would remove: {path}')

    if not args.delete:
        print('\n(dry run -- pass --delete to actually remove these files)')
    else:
        print('done')


if __name__ == '__main__':
    main()
