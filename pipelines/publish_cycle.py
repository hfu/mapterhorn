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
    # D115 SAFETY GUARD (2026-09-03): this script is stale and dangerous
    # against the current pmtiles-store layout. bundle.py was refactored
    # (D95/D107) to read only pmtiles-store/{aggregation,downsampling}/
    # {datatype}/... (the new 1.5-go layered tree), which is currently
    # EMPTY -- 1-go production data (14,590 files, ~579GB as of tonight)
    # is still entirely in the old flat pmtiles-store/{z7bucket}/ layout.
    # Traced the actual consequence end to end (mapterhorn-japan-bridge
    # DECISIONS.md D115): this script would (1) rm -f the local final
    # archive unconditionally, (2) run bundle.py against the empty
    # layered tree -- which does NOT crash, it just produces 0 files and
    # exits 0, so nothing downstream catches it, (3) merge_japan_bundles.py
    # then runs on empty input, (4) ssh stars rm -f the LIVE PUBLISHED
    # archive, then (5) rsync a file that was deleted in step 1 and never
    # recreated (this script never even runs the z0-7 overview splice --
    # it rsyncs a plain 'mapterhorn-japan-bridge.pmtiles' that nothing
    # here produces; the D109 rename made the real intermediate name
    # '.z8plus.pmtiles'). Net effect: all three copies (local final,
    # local z8plus intermediate, live published) gone, nothing republished.
    # Refuse to run until this is properly repaired (D115 plan step 1.2:
    # reconcile flat-vs-layered source, fix the D109 naming drift, add the
    # missing z0-7 splice step) and this guard is removed by that fix.
    import sys as _sys
    print('REFUSING TO RUN: publish_cycle.py is stale against the current '
          'pmtiles-store layout and would destroy the local and published '
          'archives without replacing them. See mapterhorn-japan-bridge '
          'DECISIONS.md D115 for the full trace and the repair plan '
          '(step 1.2) before removing this guard.', file=_sys.stderr)
    _sys.exit(1)

    lock_file = open(LOCK_PATH, 'w')
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f'[{datetime.now()}] another publish cycle is already running -- skipping this invocation')
        return 0

    try:
        print(f'[{datetime.now()}] publish cycle starting')

        # DECISIONS.md D37/D55: downsampling_run.py can only ever process
        # what downsampling_covering.py has already enumerated as
        # `*-downsampling.csv` candidates -- that step was never wired
        # into this script, so it had to be run by hand once per
        # generation (D37 first found this the hard way: a generation
        # with zero candidate files silently downsamples nothing, no
        # matter how much aggregation finishes). write_downsampling_items()
        # starts with `rm aggregation-store/{id}/*-downsampling.csv` and
        # fully regenerates the set, so it's safe and idempotent to run
        # every cycle (measured ~13-20s at this generation's current
        # scale -- negligible next to the other stages).
        run('uv run python3 downsampling_covering.py')

        run('uv run python3 downsampling_run.py',
            extra_env={'PRIORITY_MODE': 'quadrans', 'DOWNSAMPLING_STRICT': '1', 'DOWNSAMPLING_WORKERS': '3'})

        # DECISIONS.md D53/D54: bundle.py never reads or writes bundle-
        # store's own merged OUTPUT file (it only reads pmtiles-store and
        # writes fresh per-region bundles) -- but the *old* merged output
        # (up to ~290GB) sat there completely unused throughout bundle.py's
        # own multi-hour run one night, pushing free space down to ~107Gi
        # before merge_japan_bundles.py's own open(OUTPUT, 'wb') truncated
        # it moments later anyway. Deleting it here, before bundle.py even
        # starts, reclaims that headroom for the entire bundle+merge
        # window instead of only from merge onward.
        run('rm -f bundle-store/mapterhorn-japan-bridge.pmtiles')

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

        run(f'rsync -av --partial --progress bundle-store/mapterhorn-japan-bridge.pmtiles {STARS_TARGET}')

        print(f'[{datetime.now()}] publish cycle finished')
        return 0
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


if __name__ == '__main__':
    sys.exit(main())
