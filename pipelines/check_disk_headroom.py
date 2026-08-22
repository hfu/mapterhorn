#!/usr/bin/env python3
"""Log free space on the pipeline's data volume over time and flag low
headroom (mapterhorn-japan-bridge DECISIONS.md D23 addendum: "a disk-space
check partway through the [unattended] window... wasn't a scheduled check
before this session surfaced the 139GB/1,431-directory accumulation by
accident").

Appends one line per invocation to disk_headroom.log (repo root, not
pipelines/, so it doesn't get swept up by tmp-store cleanup). Meant to be
run periodically (e.g. every 15min from a screen loop) during unattended
stretches -- this script only observes and logs; it never deletes or kills
anything.

Usage: python3 check_disk_headroom.py [--warn-gb 200] [--critical-gb 80]
"""
import argparse
import shutil
from datetime import datetime
from pathlib import Path

VOLUME = '/Volumes/Migrate-2025-04'
LOG_PATH = Path(__file__).resolve().parent.parent / 'disk_headroom.log'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--warn-gb', type=float, default=200)
    parser.add_argument('--critical-gb', type=float, default=80)
    args = parser.parse_args()

    usage = shutil.disk_usage(VOLUME)
    free_gb = usage.free / 1e9
    total_gb = usage.total / 1e9
    now = datetime.now().isoformat(timespec='seconds')

    level = 'ok'
    if free_gb < args.critical_gb:
        level = 'CRITICAL'
    elif free_gb < args.warn_gb:
        level = 'WARNING'

    line = f'{now}  free={free_gb:.1f}GB  total={total_gb:.1f}GB  {level}'
    print(line)
    with open(LOG_PATH, 'a') as f:
        f.write(line + '\n')


if __name__ == '__main__':
    main()
