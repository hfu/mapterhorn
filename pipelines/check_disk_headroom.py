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

Usage: python3 check_disk_headroom.py [--warn-gb 300] [--critical-gb 120]
"""
import argparse
import os
import shutil
from datetime import datetime
from pathlib import Path

# 1.5-go launch pre-flight (2026-09-04): extended to cover pmtiles-store
# as well as Migrate-2025-04 -- the 1.5-go national run writes the bulk of
# its output there (leaf + pyramid pmtiles), and the original single-volume
# version would have stayed silent through exactly the kind of headroom
# exhaustion this script exists to catch.
VOLUMES = ['/Volumes/Migrate-2025-04', '/Volumes/pmtiles-store']
LOG_PATH = Path(__file__).resolve().parent.parent / 'disk_headroom.log'

# Scratch/temp trees that hold reclaimable bytes. D157 (2026-09-10): a merge
# died on ENOSPC with two orphaned 310GB writer-scratch files from a crash a
# week earlier still sitting here -- 578GB of pure garbage that this script
# had no way to distinguish from real data, because it only ever reported a
# volume-level free number. Report their sizes explicitly so a human (or an
# agent) reading the log can see "free is low BUT scratch is huge" and know
# the fix is a cleanup, not a bigger disk.
# Note `tmp-store` is a symlink to `pmtiles-store/tmp-store` (same tree), so
# listing both would double-count. Name the two scratch subtrees individually
# instead -- that also makes the log say *which* one is holding the bytes,
# which is the actionable part.
SCRATCH_DIRS = [
    'pmtiles-store/tmp-store/writer-scratch',  # utils.create_archive(), Python pmtiles Writer
    'pmtiles-store/tmp-store/go-cli-scratch',  # ./pmtiles wrapper (cluster/merge/verify)
]


def dir_size_gb(path):
    """Sum of file sizes under path, in GB. Returns None if absent/unreadable.
    Deliberately not `du` -- avoids spawning a process every 15 minutes, and a
    partial walk is fine for an advisory number."""
    if not os.path.isdir(path):
        return None
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
    except OSError:
        return None
    return total / 1e9


def main():
    parser = argparse.ArgumentParser()
    # D157: the old defaults (200/80) were guesses and demonstrably too low --
    # the national elevation merge ran with 228GiB free, logged "ok", and then
    # died on ENOSPC. Re-derived from what the stages actually consume at
    # national scale, measured on the 1.5-go artifacts (2026-09-11):
    #   merge_japan_bundles.py : 237.4 GiB in -> 237.4 GiB out (z8plus)
    #   pmtiles merge (splice) : -> 240.4 GiB final archive
    # So ~240 GiB is the largest single-stage appetite. warn sits above it, so
    # the warning fires while there is still time to act rather than after a
    # stage has already picked a doomed path; critical marks "even the smaller
    # stages are now at risk". Revisit whenever the archive's size class
    # changes materially (2-go onward).
    parser.add_argument('--warn-gb', type=float, default=300)
    parser.add_argument('--critical-gb', type=float, default=120)
    args = parser.parse_args()

    now = datetime.now().isoformat(timespec='seconds')
    lines = []
    for volume in VOLUMES:
        usage = shutil.disk_usage(volume)
        free_gb = usage.free / 1e9
        total_gb = usage.total / 1e9

        level = 'ok'
        if free_gb < args.critical_gb:
            level = 'CRITICAL'
        elif free_gb < args.warn_gb:
            level = 'WARNING'

        lines.append(f'{now}  {volume}  free={free_gb:.1f}GB  total={total_gb:.1f}GB  {level}')

    # Only worth the walk when headroom is actually a question -- on a healthy
    # volume this would be pure noise appended every 15 minutes forever.
    any_pressure = any(line.rstrip().endswith(('WARNING', 'CRITICAL')) for line in lines)
    if any_pressure:
        for scratch in SCRATCH_DIRS:
            size_gb = dir_size_gb(scratch)
            if size_gb is None:
                continue
            lines.append(f'{now}  scratch  {scratch}  {size_gb:.1f}GB  (reclaimable if no job is running)')

    with open(LOG_PATH, 'a') as f:
        for line in lines:
            print(line)
            f.write(line + '\n')


if __name__ == '__main__':
    main()
