import os

# D116-launch pre-flight (2026-09-04): this script runs for many hours
# across thousands of items, each ending in utils.create_archive() ->
# pmtiles.writer.Writer, which buffers via tempfile.TemporaryFile() with
# no path argument -- lands on tempfile.gettempdir() unless TMPDIR is
# force-set first (D104/D105's finding; previously fixed only in
# bundle_1go_rebuild.py/merge_japan_bundles.py, not here). Each item's
# own scratch is small, but thousands of iterations over many hours raise
# the odds of an interrupted run leaving an orphaned scratch file on the
# small boot volume -- exactly the incident class that corrupted a 310GB
# archive earlier tonight. Set before any other import, same pattern as
# the other two scripts.
os.environ['TMPDIR'] = os.path.abspath('tmp-store/writer-scratch/')
os.makedirs(os.environ['TMPDIR'], exist_ok=True)
import tempfile
tempfile.tempdir = None

from glob import glob
import json
import random
import shutil
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
    # Default: 3 workers (mapterhorn-japan-bridge DECISIONS.md D129/D130/D131,
    # 2026-09-05). D84's 5-worker default (2026-09-01) was tuned for CPU idle
    # time only and didn't account for per-worker memory spikes on dense
    # source tiles -- 5 concurrent workers on this machine's 16GB RAM ran for
    # ~12.5h before exhausting the memory compressor's segment limit and
    # triggering a kernel panic (D129). Reducing to 3 costs ~20-23% throughput
    # (measured: 4.03->3.10 items/min) but has run crash-free for 12.5h+ since
    # (as of D130's analysis) with no memory-pressure incidents. Hidenori's
    # decision (D131): fix 3 workers for both the remainder of 1.5-go and for
    # 2-go -- accept the throughput cost in exchange for eliminating the
    # crash risk, rather than soak-testing 4 workers as D130 had left open.
    return 3

def emit_lineage(filepath, tmp_folder):
    """D93/D94/D96/D107: compute and tile the provenance raster. Must run
    between reproject() and merge() -- merge() consumes and deletes the
    per-group `{i}-3857.tiff` files compute_provenance() needs (or, in the
    single-source case, renames the only one away entirely), so there is
    no reprojected data left to read from once merge() has returned. This
    is exactly why lineage_inspect.py's own standalone diagnostic never
    calls merge() at all -- reused here, not rediscovered."""
    filename = filepath.split('/')[-1]
    aggregation_id = filepath.split('/')[-2]
    z, x, y, child_z = [int(a) for a in filename.replace('-aggregation.csv', '').split('-')]

    groups = utils.get_grouped_source_items(filepath)
    provenance, _num_groups_used = lineage_provenance.compute_provenance(filepath, tmp_folder)
    category_data = lineage_provenance.local_provenance_to_global(provenance, groups)

    with open(f'{tmp_folder}/reprojection.json') as f:
        buffer_pixels = json.load(f)['buffer_pixels']

    lineage_tile.main(x, y, z, child_z, category_data, buffer_pixels, tmp_folder, aggregation_id)

def run(filepath):
    filename = filepath.split('/')[-1]
    item = filename.replace('-aggregation.csv', '')
    # aggregation_id scopes tmp_folder so a different run can never resume
    # a stale folder left by an earlier run at the same z-x-y-maxzoom
    # coordinates with different source composition (see DECISIONS.md D12's
    # update: this was upstream's own pre-Manager/Worker behavior, dropped by
    # 6cdf66b's global `tmp-store/{item}` layout).
    aggregation_id = filepath.split('/')[-2]
    # D119 P2.B/D120 Fable #6: the .done marker is now a manifest that
    # records WHICH datatypes it certifies. An elevation-only .done (all
    # of 1-go's markers, and any pre-manifest legacy touch file) no
    # longer silently satisfies an EMIT_LINEAGE run -- that was the hard
    # blocker that would have made a lineage pass over an already-
    # aggregated generation a national-scale no-op.
    required_datatypes = ['elevation', 'lineage'] if EMIT_LINEAGE else ['elevation']
    done_path = f'{filepath}.done'
    if utils.done_covers(done_path, required_datatypes):
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
    # Fingerprint the covering CSV by content (not mtime -- coverings are
    # rewritten byte-identical between runs): the same signal
    # get_dirty_aggregation_filenames() already treats as this item's
    # identity. Written atomically; the old .todo -> .done rename is
    # replaced by manifest-write + best-effort .todo removal (a missing
    # .todo is no longer an error -- D110's rehearsal tripped over that).
    utils.write_done_manifest(
        done_path,
        datatypes=required_datatypes,
        generation_id=aggregation_id,
        entries=[utils.content_input_entry(filepath)],
    )
    try:
        os.remove(f'{filepath}.todo')
    except FileNotFoundError:
        pass
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
