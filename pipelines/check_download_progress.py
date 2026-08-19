#!/usr/bin/env python3
"""Report download progress (file count only) for every source-catalog
entry that has a file_list.csv, by comparing source-store/{source}'s
local .tif count against the manifest's row count.

Usage: python3 check_download_progress.py [source ...]
  No args: checks every source-catalog/*/file_list.csv entry.
  With args: checks only the named sources.
"""
import csv
import sys
from pathlib import Path


def count_manifest_rows(source):
    csv_path = Path(f'../source-catalog/{source}/file_list.csv')
    if not csv_path.exists():
        return None
    with open(csv_path, newline='') as f:
        return sum(1 for _ in csv.DictReader(f))


def count_local_tifs(source):
    store_dir = Path(f'source-store/{source}')
    if not store_dir.exists():
        return 0
    return sum(1 for _ in store_dir.glob('*.tif'))


def main():
    if len(sys.argv) > 1:
        sources = sys.argv[1:]
    else:
        sources = sorted(
            p.parent.name for p in Path('../source-catalog').glob('*/file_list.csv')
        )

    if not sources:
        print('No sources with a file_list.csv found.')
        return

    name_width = max(len(s) for s in sources)
    for source in sources:
        total = count_manifest_rows(source)
        if total is None:
            print(f'{source:<{name_width}}  no file_list.csv')
            continue
        local = count_local_tifs(source)
        pct = (local / total * 100) if total else 0
        print(f'{source:<{name_width}}  {local:>7,} / {total:>7,}  ({pct:5.1f}%)')


if __name__ == '__main__':
    main()
