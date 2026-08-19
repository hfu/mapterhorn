import sys
import os
from multiprocessing import Pool
import shutil

import utils

SILENT = False

# Max input files per `gdal vector concat` invocation. macOS ARG_MAX is
# 1MB; per-mesh gpkg paths run ~60-80 bytes, so 3000 keeps well clear of
# that limit (tested directly: 3000 files completed in ~92s with no
# "argument list too long" error, vs. a full 18k-file batch which did
# hit the limit).
BATCH_SIZE = 3000

def polygonize_tif(source, filename):
    utils.run_command(f'GDAL_CACHEMAX=1024 gdal_footprint source-store/{source}/{filename} polygon-store/{source}/{filename}.gpkg -overwrite', silent=SILENT)

def get_filenames(source):
    lines = None
    with open(f'source-store/{source}/bounds.csv') as f:
        lines = f.readlines()
    lines = [l.strip() for l in lines[1:]]
    filenames = [line.split(',')[0] for line in lines]
    return filenames

def polygonize_source(source, processes):
    filenames = get_filenames(source)
    utils.create_folder(f'polygon-store/{source}/')
    argument_tuples = []
    for filename in filenames:
        argument_tuples.append((source, filename))
    with Pool(processes) as pool:
        pool.starmap(polygonize_tif, argument_tuples, chunksize=1)

def chunk(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]

def concat_batch(args):
    source, batch_index, filepaths, batch_dir = args
    output_path = f'{batch_dir}/batch-{batch_index:05d}.gpkg'
    command = (
        'gdal vector concat ' + ' '.join(f'"{p}"' for p in filepaths)
        + f' "{output_path}" --mode single --output-layer out'
    )
    utils.run_command(command, silent=SILENT)
    return output_path

def merge_source(source, processes):
    """Rewritten 2026-08-19 (D14-adjacent, mapterhorn-japan-bridge repo)
    to replace the old N-subprocess (one `ogr2ogr -update -append` per
    mesh) loop, which cost ~90-430ms per file purely in subprocess/GDAL-
    driver-init overhead regardless of disk speed (see DECISIONS.md/
    HANDOVER.md investigation) -- at national scale (hundreds of
    thousands of files) that loop alone would run for many hours.
    Uses the new unified `gdal vector concat` CLI (GDAL 3.13+), which
    merges many inputs into one output within a single process. Still
    has to batch (macOS ARG_MAX is 1MB, a single 75k+/378k+-file
    argument list blows past it -- confirmed directly), so this runs in
    two levels: many parallel `gdal vector concat` calls over
    BATCH_SIZE-sized chunks, then one final `gdal vector concat` over
    the resulting (far fewer) batch outputs. Verified byte-for-byte
    equivalent feature count against the old method on a real 500-file
    sample (both produced 494 features) before rollout.
    """
    filenames = get_filenames(source)
    filepaths = [f'polygon-store/{source}/{filename}.gpkg' for filename in filenames]

    batch_dir = f'polygon-store/{source}-batches'
    utils.create_folder(f'{batch_dir}/')

    batches = list(chunk(filepaths, BATCH_SIZE))
    print(f'merging {len(filepaths):_} features in {len(batches):_} batches of up to {BATCH_SIZE:_}...')
    tasks = [(source, i, batch, batch_dir) for i, batch in enumerate(batches)]
    with Pool(processes) as pool:
        batch_outputs = pool.map(concat_batch, tasks)

    merged_filepath = f'polygon-store/{source}/merged.gpkg'
    if os.path.isfile(merged_filepath):
        os.remove(merged_filepath)
    command = (
        'gdal vector concat ' + ' '.join(f'"{p}"' for p in batch_outputs)
        + f' "{merged_filepath}" --mode single --output-layer out'
    )
    utils.run_command(command, silent=False)
    shutil.rmtree(batch_dir)

    union_filepath = f'polygon-store/{source}.gpkg'
    if os.path.isfile(union_filepath):
        os.remove(union_filepath)
    utils.run_command(f'ogr2ogr -f GPKG {union_filepath} {merged_filepath} -nln union -dialect sqlite -sql "SELECT ST_Union(ST_MakeValid(geom)) AS geom FROM out"', silent=False)

def main():
    source = None
    processes = None
    if len(sys.argv) == 3:
        source = sys.argv[1]
        processes = int(sys.argv[2])
        print(f'polygonizing {source} with {processes} processes...')
    else:
        print('Not enough arguments. Usage: source_polygonize.py {{source}} {{processes}}')
        exit()
    polygonize_source(source, processes)
    merge_source(source, processes)
    shutil.rmtree(f'polygon-store/{source}')

if __name__ == '__main__':
    main()
