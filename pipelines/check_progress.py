#!/usr/bin/env python3
"""Check aggregation and downsampling progress"""
import subprocess
from glob import glob
from pathlib import Path

import utils

def get_current_aggregation_id():
    aggregation_ids = utils.get_aggregation_ids()
    return aggregation_ids[-1] if aggregation_ids else None

def check_aggregation_status(agg_id):
    result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
    gdal_count = sum(1 for line in result.stdout.split('\n') if 'gdal_translate' in line and 'grep' not in line)
    total = len(glob(f'aggregation-store/{agg_id}/*-aggregation.csv'))
    done = len(glob(f'aggregation-store/{agg_id}/*-aggregation.done'))
    return gdal_count, done, total

def check_downsampling_status():
    log_file = Path('downsampling.log')
    if not log_file.exists():
        return None, None

    with open(log_file) as f:
        for line in reversed(f.readlines()):
            if 'downsampling' in line and '/' in line:
                import re
                m = re.search(r'(\d+) / (\d+)', line)
                if m:
                    return int(m.group(1)), int(m.group(2))
    return None, None

def main():
    print("=" * 60)
    print("PIPELINE STATUS CHECK")
    print("=" * 60)
    print()

    agg_id = get_current_aggregation_id()
    if agg_id is None:
        print("No aggregation_id found (aggregation_covering.py not run yet).")
        return

    print(f"Current aggregation_id: {agg_id}")
    agg_procs, agg_done, agg_total = check_aggregation_status(agg_id)
    print(f"Aggregation:")
    print(f"  Active gdal_translate processes: {agg_procs}")
    if agg_total:
        pct = (agg_done / agg_total) * 100
        print(f"  Items completed: {agg_done}/{agg_total} ({pct:.1f}%)")
    else:
        print(f"  Items completed: {agg_done}/0 (no *-aggregation.csv found)")
    print()

    current, total = check_downsampling_status()
    if current is not None:
        pct = (current / total) * 100
        print(f"Downsampling:")
        print(f"  Progress: {current}/{total} ({pct:.1f}%)")
    else:
        print(f"Downsampling: Not yet started")

    print()
    print("Expected sequence:")
    print(f"  1. aggregation completes ({agg_total}/{agg_total})")
    print("  2. downsampling_covering.py + downsampling_run.py")
    print("  3. bundle.py")

if __name__ == '__main__':
    main()
