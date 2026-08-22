#!/usr/bin/env python3
"""Full-corpus decode-validity screening for a jpnational* source: opens
every GeoTIFF and forces a full pixel-data read (any decode failure, e.g.
the ZSTD corruption found in jpnational1 -- see mapterhorn-japan-bridge
DECISIONS.md D35 / japan-geotiff-dem DECISIONS.md D18 -- raises here) and
computes each file's valid-data percentage as a secondary signal for the
*silent* (non-crashing) corruption class from the same investigation.

This is a *screening* pass only -- it has no access to the raw GML
source (ground truth), so a low/zero valid-percent is a CANDIDATE for
silent corruption, not confirmed proof by itself (some tiles are
legitimately all-sea/all-nodata). Flags every 0%-valid file for
follow-up ground-truth checking against the actual GML source.

Usage: python3 screen_source.py <source> [--workers N] [--out results.csv]
Example: python3 screen_source.py jpnational5 --workers 8
"""
import argparse
import csv
import os
from glob import glob
from multiprocessing import Pool

import numpy as np
import rasterio


def check_one(path):
    name = os.path.basename(path)
    try:
        with rasterio.Env(GDAL_CACHEMAX=32):
            with rasterio.open(path) as src:
                data = src.read(1)
                nodata = src.nodata if src.nodata is not None else -9999.0
                valid = np.count_nonzero(data != nodata)
                total = data.size
                pct = 100.0 * valid / total if total else 0.0
                return (name, round(pct, 4), None)
    except Exception as e:
        return (name, None, f'{type(e).__name__}: {str(e)[:120]}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', help='source-catalog/source-store name, e.g. jpnational5')
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--out', default=None)
    args = parser.parse_args()

    source_dir = f'source-store/{args.source}'
    out_path = args.out or f'screen_results_{args.source}.csv'

    paths = sorted(glob(f'{source_dir}/*.tif'))
    print(f'{args.source}: {len(paths):_} files to screen, {args.workers} workers')

    done = 0
    zero_pct = []
    errors = []
    with open(out_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['name', 'valid_pct', 'error'])
        with Pool(processes=args.workers) as pool:
            for name, pct, err in pool.imap_unordered(check_one, paths, chunksize=50):
                writer.writerow([name, pct, err])
                done += 1
                if err:
                    errors.append(name)
                elif pct == 0.0:
                    zero_pct.append(name)
                if done % 5000 == 0:
                    f.flush()
                    print(f'{done:_} / {len(paths):_} done. '
                          f'0%% candidates so far: {len(zero_pct)}. errors: {len(errors)}.')

    print(f'FINISHED {args.source}. total={done:_} zero_pct_candidates={len(zero_pct)} errors={len(errors)}')


if __name__ == '__main__':
    main()
