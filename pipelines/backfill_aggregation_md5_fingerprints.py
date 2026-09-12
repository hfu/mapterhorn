"""DECISIONS1.md D163: one-off backfill so an EXISTING generation's own
.done manifests carry the per-source MD5 fingerprint entries that
aggregation_covering.py's try_reuse_from_previous_generation() needs to
compare against. Without this, a later generation (2-go) can never
safely reuse an earlier one's (1.5-go's) output -- not because anything
is actually different, but because the earlier manifest was written
before this fingerprint existed at all, so done_is_current() would
always see a mismatch and correctly (but wastefully) fall back to full
reprocessing for every single item.

Safe to run at any time: this only rewrites small JSON .done manifests
(never touches pmtiles-store's binary output, never touches the
covering .csv files themselves), is idempotent (a manifest that already
has MD5 entries is left untouched), and the recomputed MD5s are read
from each source's CURRENT file_list.csv.gz/csv -- valid to backfill
onto an OLDER generation's manifest only because that source's manifest
has not been regenerated since (verified once, by hand, before running
this against 1.5-go: source-catalog/jpnational{1,5,10,sea}'s own
manifests all last changed 2026-08-19 through 2026-08-25, well before
1.5-go's own aggregation run in D132, 2026-09-04/05).

Usage:
    uv run python backfill_aggregation_md5_fingerprints.py <generation_id>          # dry run
    uv run python backfill_aggregation_md5_fingerprints.py <generation_id> --apply  # write
"""
from datetime import datetime, timezone
from glob import glob
import sys

import utils

def main():
    if len(sys.argv) < 2:
        print(f'usage: {sys.argv[0]} <generation_id> [--apply]')
        sys.exit(1)
    generation_id = sys.argv[1]
    apply = '--apply' in sys.argv[2:]

    done_paths = sorted(glob(f'aggregation-store/{generation_id}/*-aggregation.csv.done'))
    print(f'found {len(done_paths)} .done manifests under aggregation-store/{generation_id}/')

    backfilled = 0
    already_had_md5 = 0
    skipped_legacy_or_missing = 0

    for done_path in done_paths:
        csv_path = done_path[:-len('.done')]
        filename = csv_path.split('/')[-1]

        manifest = utils.read_done_manifest(done_path)
        if not manifest:
            # None (no file -- can't happen, we just globbed it) or {}
            # (legacy empty touch-file marker, pre-D119). Nothing to
            # backfill onto a manifest with no structure to extend.
            skipped_legacy_or_missing += 1
            continue

        existing_entries = manifest.get('inputs', [])
        if any('md5' in e for e in existing_entries):
            already_had_md5 += 1
            continue

        new_entries = [utils.content_input_entry(csv_path, canonical_path=filename)] + utils.md5_input_entries_for_aggregation_csv(csv_path)

        if apply:
            utils.write_done_manifest(
                done_path,
                datatypes=manifest['datatypes'],
                generation_id=manifest['generation_id'],
                entries=new_entries,
                extra={
                    'created_at': manifest['created_at'],  # preserve original build time
                    'backfilled_at': datetime.now(timezone.utc).isoformat(),
                    'backfill_note': 'D163: added per-source MD5 fingerprint entries for cross-generation reuse; no rebuild happened',
                },
            )
        backfilled += 1

    print(f'backfilled: {backfilled}')
    print(f'already had MD5 entries (skipped): {already_had_md5}')
    print(f'legacy/empty manifests (skipped): {skipped_legacy_or_missing}')
    if not apply:
        print('DRY RUN -- pass --apply to actually write manifests')

if __name__ == '__main__':
    main()
