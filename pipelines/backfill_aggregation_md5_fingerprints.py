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
has MD5 entries is left untouched). The recomputed MD5s are read from
each source's CURRENT file_list.csv.gz/csv -- valid to backfill onto an
OLDER generation's manifest only when that source's manifest has not
been regenerated since that item was actually built (verified once, by
hand, before running this against 1.5-go: source-catalog/jpnational
{1,5,10,sea}'s own manifests all last changed 2026-08-19 through
2026-08-25, well before 1.5-go's own aggregation run in D132,
2026-09-04/05). D164: this precondition is now also checked per item at
runtime (a source manifest newer than the item's own `created_at` gets
skipped, not silently backfilled with an unverifiable fingerprint) --
the by-hand check above is what makes 1.5-go's own backfill safe, this
runtime check is what keeps a future, less-careful invocation from
quietly producing a wrong one.

Usage:
    uv run python backfill_aggregation_md5_fingerprints.py <generation_id>          # dry run
    uv run python backfill_aggregation_md5_fingerprints.py <generation_id> --apply  # write
"""
from datetime import datetime, timezone
from glob import glob
import os
import sys

import utils

_MANIFEST_MTIME_CACHE = {}

def get_manifest_mtime(source):
    """mtime of source-catalog/{source}/file_list.csv[.gz], memoized --
    mirrors utils.open_manifest()'s own gz-preferred-else-plain path
    resolution, but this script only needs the file's own mtime, not its
    content, so it doesn't go through utils.get_source_md5_map()'s
    (already memoized, but content-loading) path for this."""
    if source in _MANIFEST_MTIME_CACHE:
        return _MANIFEST_MTIME_CACHE[source]
    gz_path = f'../source-catalog/{source}/file_list.csv.gz'
    plain_path = f'../source-catalog/{source}/file_list.csv'
    path = gz_path if os.path.isfile(gz_path) else plain_path
    mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    _MANIFEST_MTIME_CACHE[source] = mtime
    return mtime

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
    skipped_manifest_changed_since = 0
    skipped_error = 0

    for done_path in done_paths:
        csv_path = done_path[:-len('.done')]
        filename = csv_path.split('/')[-1]

        try:
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

            # D164: refuse to backfill if any referenced source's
            # manifest has changed since this item was actually built --
            # today's MD5 would then reflect a LATER state than what this
            # generation's own pmtiles output was really built from,
            # producing a fingerprint that could falsely certify a since-
            # changed item as reusable.
            created_at = datetime.fromisoformat(manifest['created_at'])
            sources_referenced = {source for source, _filename, _maxzoom in utils.read_aggregation_csv_rows(csv_path)}
            stale_sources = [s for s in sources_referenced if get_manifest_mtime(s) > created_at]
            if stale_sources:
                print(f'SKIPPING {filename}: source manifest(s) {sorted(stale_sources)} changed after this item was built ({created_at.isoformat()}) -- refusing to backfill an unverifiable fingerprint')
                skipped_manifest_changed_since += 1
                continue

            new_entries = utils.aggregation_fingerprint_entries(csv_path, filename)

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
        except Exception as e:
            # D164: one bad item (a missing source-catalog manifest, a
            # malformed covering CSV, a corrupt .done) must not abort the
            # whole batch partway through with no record of what was
            # already written vs. what was skipped.
            print(f'ERROR on {filename}: {e!r} -- skipping this item')
            skipped_error += 1

    print(f'backfilled: {backfilled}')
    print(f'already had MD5 entries (skipped): {already_had_md5}')
    print(f'legacy/empty manifests (skipped): {skipped_legacy_or_missing}')
    print(f'source manifest changed since build (skipped): {skipped_manifest_changed_since}')
    print(f'errors (skipped): {skipped_error}')
    if not apply:
        print('DRY RUN -- pass --apply to actually write manifests')

if __name__ == '__main__':
    main()
