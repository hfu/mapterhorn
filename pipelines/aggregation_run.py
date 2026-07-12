from glob import glob
import shutil
import os
from multiprocessing import Pool

import aggregation_reproject
import aggregation_merge
import aggregation_tile
import utils

def get_worker_count():
    """Get worker count with graceful defaults (mirrors downsampling_run.py)"""
    if 'AGGREGATION_WORKERS' in os.environ:
        try:
            return int(os.environ['AGGREGATION_WORKERS'])
        except ValueError:
            pass
    # Default: 4 workers (half of typical 8-core hardware, avoids saturating CPU/disk)
    return 4

def run(filepath):
    filename = filepath.split('/')[-1]
    item = filename.replace('-aggregation.csv', '')
    print(f'{item} start')
    aggregation_reproject.reproject(filepath)
    aggregation_merge.merge(filepath)
    aggregation_tile.main(filepath)
    tmp_folder = filepath.replace('-aggregation.csv', '-tmp')
    shutil.rmtree(tmp_folder)
    utils.run_command(f'touch {filepath.replace("-aggregation.csv", "-aggregation.done")}')
    print(f'{item} end')

def main():
    aggregation_ids = utils.get_aggregation_ids()
    aggregation_id = aggregation_ids[-1]

    dirty_filepaths = None
    if len(aggregation_ids) < 2:
        dirty_filepaths = sorted(glob(f'aggregation-store/{aggregation_id}/*-aggregation.csv'))
    else:
        last_aggregation_id = aggregation_ids[-2]
        dirty_filepaths = [f'aggregation-store/{aggregation_id}/{filename}' for filename in utils.get_dirty_aggregation_filenames(aggregation_id, last_aggregation_id)]
    
    dirty_filepaths = [filepath for filepath in dirty_filepaths if not os.path.isfile(filepath.replace('-aggregation.csv', '-aggregation.done'))]
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
