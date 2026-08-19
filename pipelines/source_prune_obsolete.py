"""Move local source-store files that are no longer in the current
file_list.csv -- typically because the underlying japan-geotiff-dem
manifest was refreshed and a mesh cell's file got superseded (a new
survey lands under a *new* filename, per that repo's own D9/D13; the
old filename simply drops out of `latest_file_list.csv.gz`).

Nothing else in this pipeline detects this on its own: source_download.py
is purely additive (it only ever adds files present in the current
manifest, never removes ones that fell out of it), and source_bounds.py
globs `source-store/{source}/*.tif` unconditionally, so an orphaned old
file gets included in bounds.csv -- and from there, polygonize and
aggregation -- exactly as if it were still current, silently using
stale survey data even though a newer file for the same cell exists.

Usage: python3 source_prune_obsolete.py <source> [--apply]

Without --apply, only reports what *would* move (safe default). With
--apply, moves orphaned files to source-store/{source}-stale/ -- not
deleted, reversible, matching this project's established "move aside,
don't destroy" convention (e.g. japan-geotiff-dem's own skip-published
zips). Run source_bounds.py again afterward for this source before the
next source_polygonize.py run, so bounds.csv reflects the pruned set.
"""
import csv
import sys
from pathlib import Path

import utils


def load_expected_filenames(source):
    csv_path = Path(f'../source-catalog/{source}/file_list.csv')
    with open(csv_path, newline='') as f:
        return set(row['url'].rsplit('/', 1)[-1] for row in csv.DictReader(f))


def prune_obsolete(source, apply=False):
    expected = load_expected_filenames(source)
    store_dir = Path(f'source-store/{source}')
    local_tifs = sorted(p.name for p in store_dir.glob('*.tif'))
    print(f'{len(local_tifs)} local .tif files, {len(expected)} expected '
          f'per current file_list.csv.')

    orphaned = [name for name in local_tifs if name not in expected]
    print(f'{len(orphaned)} orphaned (no longer in file_list.csv -- '
          f'superseded upstream, or dropped from scope).')

    if not orphaned:
        print('Nothing to prune.')
        return

    if not apply:
        print('Dry run -- pass --apply to actually move these aside. '
              f'Sample (up to 20 of {len(orphaned)}):')
        for name in orphaned[:20]:
            print(f'  {name}')
        return

    stale_dir = Path(f'source-store/{source}-stale')
    utils.create_folder(str(stale_dir))
    for name in orphaned:
        (store_dir / name).rename(stale_dir / name)
    print(f'Moved {len(orphaned)} orphaned files to {stale_dir}/ '
          f'(not deleted -- reversible).')
    print('Re-run source_bounds.py for this source before the next '
          'source_polygonize.py run, so bounds.csv drops these too.')


def main():
    if len(sys.argv) < 2:
        print('Usage: source_prune_obsolete.py <source> [--apply]')
        sys.exit(1)
    source = sys.argv[1]
    apply = '--apply' in sys.argv[2:]
    prune_obsolete(source, apply=apply)


if __name__ == '__main__':
    main()
