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

    expected_pmtiles_filenames = set()

    # D165/D166 (1.6-go land-area upsampling): an aggregation LEAF's real
    # output filename uses its EFFECTIVE child_z (utils.leaf_child_z()),
    # not the covering CSV filename's own native/planned one -- those
    # differ for a land item in a generation that upsamples. The original
    # naive `filename.replace('-aggregation.csv', '.pmtiles')` kept the
    # stale native value, which would have classified every upsampled
    # leaf (elevation AND lineage) as "not expected" -- i.e. dangling --
    # and --delete would have removed the entire feature this generation
    # exists to add. Verified this matters: leaf_child_z() differs from
    # the naive substitution for exactly the land items a generation in
    # LAND_UPSAMPLE_ZOOM_BY_GENERATION upsamples, and is identical to it
    # for every other generation (leaf_child_z() returns the covering
    # filename's own native value there), so this is a pure correctness
    # fix with no behavior change for any existing generation.
    for (leaf_z, leaf_x, leaf_y), effective_child_z in utils.get_leaf_child_z_map(generation_id).items():
        expected_pmtiles_filenames.add(f'{leaf_z}-{leaf_x}-{leaf_y}-{effective_child_z}.pmtiles')

    # *-downsampling.csv filenames are entirely under downsampling_
    # covering.py's own control within this same generation, self-
    # consistent with their own real output by construction -- unaffected
    # by upsampling, naive substitution remains correct.
    for filepath in glob(f'{agg_dir}/*-downsampling.csv'):
        filename = filepath.split('/')[-1]
        expected_pmtiles_filenames.add(filename.replace('-downsampling.csv', '.pmtiles'))

    dangling = []
    present = 0
    for layer in utils.LAYERS:
        for datatype in utils.DATATYPES:
            root = f'pmtiles-store/{layer}/{datatype}/{generation_id}'
            for pmtiles_filepath in sorted(
                    glob(f'{root}/*.pmtiles') + glob(f'{root}/*/*.pmtiles')):
                present += 1
                filename = pmtiles_filepath.split('/')[-1]
                if filename in expected_pmtiles_filenames:
                    continue
                # D165/D166 (Opus design review finding #7): lineage_
                # extend_low_zoom.py (D146) deliberately writes its own
                # standalone 0-0-0-{4..7}.pmtiles nationwide-overview
                # pyramid with NO covering CSV at all -- that script's
                # whole point is extending lineage's pyramid below
                # min_output_zoom=8 without touching downsampling_
                # covering.py/downsampling_run.py. Neither of the two
                # loops above can ever discover these (there is no
                # covering to derive them from, by design), so without
                # this explicit exception every run of this tool would
                # flag the entire feature as dangling and --delete would
                # remove it permanently. Narrow and principled: matches
                # lineage_extend_low_zoom.py's own fixed LAYER/DATATYPE/
                # position/zoom-range constants exactly, so it can never
                # accidentally spare a genuinely-dangling file elsewhere.
                if (layer == 'downsampling' and datatype == 'lineage'
                        and filename.startswith('0-0-0-')
                        and int(filename.replace('0-0-0-', '').replace('.pmtiles', '')) < 8):
                    continue
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
