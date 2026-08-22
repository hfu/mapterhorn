#!/usr/bin/env python3
"""One incremental japan.pmtiles publish cycle: readiness-gated
downsampling -> bundle -> merge -> rsync to `stars`.

Operating-model decision (mapterhorn-japan-bridge DECISIONS.md, this
session): `aggregation_run.py` runs continuously and is never paused for
publishing -- pausing it would stall real progress toward the national
build for no benefit, since a same-machine concurrent run was measured
to hold up under sustained CPU oversubscription (load ~11-14 on 10
cores, over an hour) without thrashing or I/O saturation. The publish
pipeline itself, in exchange, runs as a single non-overlapping instance
("thin, exactly one at a time") -- this script takes a flock() so a
cycle that runs long is skipped rather than doubled up, and does no
internal sleep/loop of its own: cadence is set by whatever schedules
this script (cron/launchd), not by this file. Starting cadence: once
per day (measured cycle cost today, at partial/small scale: downsampling
backlog + ~64min single-region bundle bottleneck + ~12min merge --
comfortable margin under a 24h budget; revisit toward twice-daily once
a real national-scale bundle run confirms it still fits).

Each stage is run for real, in the actual `pipelines/` working
directory -- this is NOT the isolated `pipelines-rehearsal/` symlink
setup used earlier this session to test bundle.py/merge_japan_bundles.py
against throwaway generations without touching production. Only run
this once the real national aggregation_covering.py generation exists
and you mean to actually publish.
"""
import fcntl
import os
import subprocess
import sys
from datetime import datetime

PIPELINES_DIR = os.path.dirname(os.path.abspath(__file__))
LOCK_PATH = '/tmp/japan_publish_cycle.lock'
STARS_TARGET = 'stars@stars.local:/home/stars/data/'
TMPDIR = '/Volumes/Migrate-2025-04/tmp'  # not the internal SSD -- see HANDOVER.md


def run(cmd, extra_env=None):
    print(f'[{datetime.now()}] $ {cmd}', flush=True)
    env = {**os.environ, 'TMPDIR': TMPDIR, **(extra_env or {})}
    result = subprocess.run(cmd, shell=True, cwd=PIPELINES_DIR, env=env)
    if result.returncode != 0:
        raise RuntimeError(f'command failed (exit {result.returncode}): {cmd}')


def main():
    lock_file = open(LOCK_PATH, 'w')
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f'[{datetime.now()}] another publish cycle is already running -- skipping this invocation')
        return 0

    try:
        print(f'[{datetime.now()}] publish cycle starting')

        run('uv run python3 downsampling_run.py',
            extra_env={'PRIORITY_MODE': 'quadrans', 'DOWNSAMPLING_STRICT': '1'})

        run('uv run python3 bundle.py 1', extra_env={'BUNDLE_WORKERS': '2'})

        run('uv run python3 merge_japan_bundles.py')

        run(f'rsync -av --progress bundle-store/japan.pmtiles {STARS_TARGET}')

        print(f'[{datetime.now()}] publish cycle finished')
        return 0
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


if __name__ == '__main__':
    sys.exit(main())
