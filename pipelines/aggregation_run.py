from glob import glob
import json
import random
import shutil
import os
from multiprocessing import Pool

import aggregation_reproject
import aggregation_merge
import aggregation_tile
import lineage_provenance
import lineage_tile
import utils

# D93/D94/D96/D107: opt-in production lineage tile emission, off by default
# (D93's own recommendation -- 1号 never used this; 1.5号 is the first
# generation to). When set, run() computes a lineage/provenance raster
# alongside the elevation composite and writes it as its own PMTiles
# archive (datatype='lineage', separate from the elevation datatype).
EMIT_LINEAGE = os.environ.get('EMIT_LINEAGE', '0') == '1'

def get_worker_count():
    """Get worker count with graceful defaults (mirrors downsampling_run.py)"""
    if 'AGGREGATION_WORKERS' in os.environ:
        try:
            value = int(os.environ['AGGREGATION_WORKERS'])
            if value >= 1:
                return value
        except ValueError:
            pass
    # Default: 5 workers. Raised from 4 (mapterhorn-japan-bridge DECISIONS.md
    # D84, 2026-09-01) -- the original 4 assumed aggregation_run.py sharing
    # the machine with a concurrent downsampling_run.py pass and a single
    # pmtiles-store disk; neither holds today (pmtiles-store is now split
    # across two disks since D58/D61, and aggregation_repair_3344 runs
    # alone). CPU measured ~46-47% idle at 4 workers (10 logical cores,
    # 4P+6E), so raising this by one worker at a time and measuring the
    # actual items/15min pace, rather than jumping straight to a number
    # that assumes full utilization is safe.
    return 5

def emit_lineage(filepath, tmp_folder):
    """D93/D94/D96/D107: compute and tile the provenance raster. Must run
    between reproject() and merge() -- merge() consumes and deletes the
    per-group `{i}-3857.tiff` files compute_provenance() needs (or, in the
    single-source case, renames the only one away entirely), so there is
    no reprojected data left to read from once merge() has returned. This
    is exactly why lineage_inspect.py's own standalone diagnostic never
    calls merge() at all -- reused here, not rediscovered."""
    filename = filepath.split('/')[-1]
    z, x, y, child_z = [int(a) for a in filename.replace('-aggregation.csv', '').split('-')]

    groups = utils.get_grouped_source_items(filepath)
    provenance, _num_groups_used = lineage_provenance.compute_provenance(filepath, tmp_folder)
    category_data = lineage_provenance.local_provenance_to_global(provenance, groups)

    with open(f'{tmp_folder}/reprojection.json') as f:
        buffer_pixels = json.load(f)['buffer_pixels']

    lineage_tile.main(x, y, z, child_z, category_data, buffer_pixels, tmp_folder)

def run(filepath):
    filename = filepath.split('/')[-1]
    item = filename.replace('-aggregation.csv', '')
    # aggregation_id scopes tmp_folder so a different run can never resume
    # a stale folder left by an earlier run at the same z-x-y-maxzoom
    # coordinates with different source composition (see DECISIONS.md D12's
    # update: this was upstream's own pre-Manager/Worker behavior, dropped by
    # 6cdf66b's global `tmp-store/{item}` layout).
    aggregation_id = filepath.split('/')[-2]
    if os.path.isfile(f'{filepath}.done'):
        print(f'Aggregation item {item} already done. Skipping...')
        return
    print(f'{item} start')
    tmp_folder = f'tmp-store/{aggregation_id}/{item}'
    os.makedirs(tmp_folder, exist_ok=True)
    aggregation_reproject.reproject(filepath, tmp_folder)
    if EMIT_LINEAGE:
        emit_lineage(filepath, tmp_folder)
    aggregation_merge.merge(filepath, tmp_folder)
    aggregation_tile.main(filepath, tmp_folder)
    shutil.rmtree(tmp_folder)
    os.rename(f'{filepath}.todo', f'{filepath}.done')
    print(f'{item} end')

def main():
    
    aggregation_ids = utils.get_aggregation_ids()
    aggregation_id = aggregation_ids[-1]

    dirty_filepaths = [filepath.replace('.todo', '') for filepath in glob(f'aggregation-store/{aggregation_id}/*-aggregation.csv.todo')]

    # .todo files were created in geographically-sorted order (write_aggregation_todos()
    # iterates a sorted glob), and directory listing order on APFS tends to preserve
    # that -- so without shuffling, nearby tiles (often similarly source-dense/cheap
    # or source-sparse/expensive, since terrain/coverage complexity is spatially
    # correlated) land on the same handful of workers back to back. Observed in
    # practice: all 4 AGGREGATION_WORKERS getting stuck on the same expensive
    # geographic cluster (multiple adjacent z10 tiles each needing 1000+ source
    # files merged) while plenty of cheap tiles elsewhere sat untouched, leaving
    # CPU mostly idle despite "4 workers" running. Shuffling interleaves expensive
    # and cheap items across the pool so a few hard tiles can't stall the whole run.
    random.shuffle(dirty_filepaths)

    if len(dirty_filepaths) == 0:
        print('nothing to do.')
    else:
        print(f'start aggregating {len(dirty_filepaths)} items...')

    argument_tuples = [(dirty_filepath,) for dirty_filepath in dirty_filepaths]
    worker_count = get_worker_count()
    print(f'using {worker_count} workers (set AGGREGATION_WORKERS to override)')
    with Pool(processes=worker_count) as pool:
        pool.starmap(run, argument_tuples, chunksize=1)

if __name__ == '__main__':
    main()
