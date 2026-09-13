"""DECISIONS1.md D163/D165/D166: one-off backfill so an EXISTING
generation's own .done manifests carry two things later generations'
cross-generation reuse (aggregation_covering.py's try_reuse_from_
previous_generation()) needs to compare against:

1. Per-source MD5 fingerprint entries (D163). Without this, a later
   generation can never safely reuse an earlier one's output -- not
   because anything is actually different, but because the earlier
   manifest was written before this fingerprint existed at all, so
   done_is_current() would always see a mismatch and correctly (but
   wastefully) fall back to full reprocessing for every single item.
2. The item's own effective leaf_child_z (D165/D166, added when 1.6-go's
   land-area upsampling design was corrected after an Opus review found
   the ORIGINAL design would have let reuse silently copy a non-
   upsampled generation's output into an upsampling one). Without this,
   the D165/D166 comparison in try_reuse_from_previous_generation()
   would see `last_manifest.get('leaf_child_z')` as None for every
   pre-existing generation and reject reuse for literally everything --
   safe, but it would defeat D163/D164's own reuse benefit entirely for
   any generation launched after this field was introduced, not just
   for the upsampled items 1.6-go actually needs to distinguish.

Safe to run at any time: this only rewrites small JSON .done manifests
(never touches pmtiles-store's binary output, never touches the
covering .csv files themselves), is idempotent (a manifest that already
has both fields is left untouched; a manifest with one but not the
other gets only the missing one added). The recomputed MD5s are read
from each source's CURRENT file_list.csv.gz/csv -- valid to backfill
onto an OLDER generation's manifest only when that source's manifest
has not been regenerated since that item was actually built (verified
once, by hand, before running this against 1.5-go: source-catalog/
jpnational{1,5,10,sea}'s own manifests all last changed 2026-08-19
through 2026-08-25, well before 1.5-go's own aggregation run in D132,
2026-09-04/05). This precondition is also checked per item at runtime
(a source manifest newer than the item's own `created_at` gets skipped
entirely -- neither field backfilled -- rather than silently backfilled
with an unverifiable MD5 fingerprint; leaf_child_z has no such staleness
risk on its own, since it only depends on the covering CSV's own
already-fixed content and the static LAND_UPSAMPLE_ZOOM_BY_GENERATION
table, but it's computed in the same pass for simplicity). leaf_child_z
is always correct to backfill for a generation NOT in that table
(returns the covering filename's own native value, which is exactly
what such a generation's real output always used, by definition of
predating the upsampling feature).

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

    backfilled_md5 = 0
    backfilled_leaf_child_z = 0
    already_complete = 0
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
            needs_md5 = not any('md5' in e for e in existing_entries)
            needs_leaf_child_z = 'leaf_child_z' not in manifest

            if not needs_md5 and not needs_leaf_child_z:
                already_complete += 1
                continue

            if needs_md5:
                # D164: refuse to backfill MD5 if any referenced source's
                # manifest has changed since this item was actually built
                # -- today's MD5 would then reflect a LATER state than
                # what this generation's own pmtiles output was really
                # built from, producing a fingerprint that could falsely
                # certify a since-changed item as reusable.
                created_at = datetime.fromisoformat(manifest['created_at'])
                sources_referenced = {source for source, _filename, _maxzoom in utils.read_aggregation_csv_rows(csv_path)}
                stale_sources = [s for s in sources_referenced if get_manifest_mtime(s) > created_at]
                if stale_sources:
                    print(f'SKIPPING {filename}: source manifest(s) {sorted(stale_sources)} changed after this item was built ({created_at.isoformat()}) -- refusing to backfill an unverifiable fingerprint')
                    skipped_manifest_changed_since += 1
                    continue
                new_entries = utils.aggregation_fingerprint_entries(csv_path, filename)
            else:
                new_entries = manifest['inputs']

            if needs_leaf_child_z:
                z, x, y, _planned_child_z = [int(a) for a in filename.replace('-aggregation.csv', '').split('-')]
                leaf_child_z_value = utils.leaf_child_z(generation_id, z, x, y)
                # D166 Opus code review finding #6: utils.leaf_child_z() is
                # a policy-table prediction, not a verified fact -- it has
                # no way to know whether THIS item was actually built
                # before or after its generation was added to LAND_
                # UPSAMPLE_ZOOM_BY_GENERATION (see aggregation_run.py's own
                # D166 fix for that exact ordering hazard). Require the
                # real pmtiles-store file at the predicted child_z to
                # actually exist before trusting it -- the same "verify
                # the referenced output actually exists" principle D57
                # established for cross-generation reuse, applied here to
                # a backfill that could otherwise stamp a manifest with a
                # value nothing on disk supports.
                real_out_folder = utils.get_pmtiles_folder(x, y, z, layer='aggregation', datatype='elevation', generation_id=generation_id)
                if not os.path.isfile(f'{real_out_folder}/{z}-{x}-{y}-{leaf_child_z_value}.pmtiles'):
                    print(f'SKIPPING {filename}: predicted leaf_child_z={leaf_child_z_value} has no matching real pmtiles-store output -- refusing to backfill an unverified value')
                    skipped_error += 1
                    continue
            else:
                leaf_child_z_value = manifest['leaf_child_z']

            if apply:
                utils.write_done_manifest(
                    done_path,
                    datatypes=manifest['datatypes'],
                    generation_id=manifest['generation_id'],
                    entries=new_entries,
                    extra={
                        **{k: v for k, v in manifest.items() if k not in (
                            'format', 'datatypes', 'generation_id', 'created_at',
                            'inputs', 'inputs_fingerprint')},
                        'created_at': manifest['created_at'],  # preserve original build time
                        'leaf_child_z': leaf_child_z_value,
                        'backfilled_at': datetime.now(timezone.utc).isoformat(),
                        'backfill_note': 'D163/D165/D166: backfilled MD5 fingerprint and/or leaf_child_z for cross-generation reuse; no rebuild happened',
                    },
                )
            # D166 Opus code review finding #7: count as backfilled only
            # after write_done_manifest() has actually succeeded (or, in
            # dry-run mode, only once every check above has passed and
            # nothing remains that could fail) -- the old code incremented
            # before the write, so an I/O error (ENOSPC -- a real incident
            # class here, D157) would land this same item in BOTH the
            # backfilled and error counts, overstating how many manifests
            # were actually repaired.
            if needs_md5:
                backfilled_md5 += 1
            if needs_leaf_child_z:
                backfilled_leaf_child_z += 1
        except Exception as e:
            # D164: one bad item (a missing source-catalog manifest, a
            # malformed covering CSV, a corrupt .done) must not abort the
            # whole batch partway through with no record of what was
            # already written vs. what was skipped.
            print(f'ERROR on {filename}: {e!r} -- skipping this item')
            skipped_error += 1

    print(f'backfilled MD5 fingerprint: {backfilled_md5}')
    print(f'backfilled leaf_child_z: {backfilled_leaf_child_z}')
    print(f'already complete (skipped): {already_complete}')
    print(f'legacy/empty manifests (skipped): {skipped_legacy_or_missing}')
    print(f'source manifest changed since build (skipped): {skipped_manifest_changed_since}')
    print(f'errors (skipped): {skipped_error}')
    if not apply:
        print('DRY RUN -- pass --apply to actually write manifests')

if __name__ == '__main__':
    main()
