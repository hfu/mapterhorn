#!/usr/bin/env python3
"""One incremental mapterhorn-japan-bridge.pmtiles publish cycle:
readiness-gated downsampling -> bundle -> merge -> rsync to `stars`.
(Named japan.pmtiles before mapterhorn-japan-bridge DECISIONS.md D46.)

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
            extra_env={'PRIORITY_MODE': 'quadrans', 'DOWNSAMPLING_STRICT': '1', 'DOWNSAMPLING_WORKERS': '3'})

        run('uv run python3 bundle.py 1', extra_env={'BUNDLE_WORKERS': '2'})

        run('uv run python3 merge_japan_bundles.py')

        # DECISIONS.md D50/D51: rsync's own delta-transfer algorithm keeps
        # an open fd on the existing destination file as its basis for the
        # whole transfer, so the old (still-being-served) archive and the
        # growing new one coexist on `stars` for the entire duration --
        # roughly 2x the archive's own size in headroom, which bit us live
        # this session (ENOSPC ~20GB short of completing). Deleting the old
        # file first removes the basis file entirely, so the transfer only
        # ever needs 1x headroom. Trades a few hours of public-URL downtime
        # (depot.optgeo.org / stars.optgeo.org 404 until the new file lands)
        # for not needing permanent 2x disk headroom on an archive that
        # only grows generation over generation (D40/D41).
        run('ssh stars@stars.local rm -f /home/stars/data/mapterhorn-japan-bridge.pmtiles')

        run(f'rsync -av --progress bundle-store/mapterhorn-japan-bridge.pmtiles {STARS_TARGET}')

        print(f'[{datetime.now()}] publish cycle finished')
        return 0
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


if __name__ == '__main__':
    sys.exit(main())
