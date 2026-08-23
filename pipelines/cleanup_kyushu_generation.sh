#!/bin/bash
# Deletes confirmed-safe leftover storage from the superseded Kyushu-scope
# test generation (01M0FNHYXSAMNVTV430XD3XB5T) and older generations.
# Written 2026-08-24 per mapterhorn-japan-bridge DECISIONS.md D40 addendum.
#
# Methodology: cross-referenced every old generations own aggregation-store
# *-aggregation.csv position keys against the current national generation
# (01M0MWK852631SHCHPA66F21WQ)s own keys. Only positions absent from the
# current generation (permanent orphans -- will NEVER be revisited or
# overwritten by aggregation_tile.pys own in-place stale-file cleanup) are
# included. Does NOT touch any position still shared with the current
# generation -- those self-heal naturally as aggregation_run.py progresses.
set -euo pipefail
cd "$(dirname "$0")"

echo "=== Safety check: confirm no open file handles on target paths ==="
if lsof +D tmp-store/01M0FNHYXSAMNVTV430XD3XB5T 2>/dev/null | grep -q .; then
  echo "ABORT: open handle found on Kyushu tmp-store dir"; exit 1
fi
if lsof bundle-store/japan.pmtiles.bak-20260810 2>/dev/null | grep -q .; then
  echo "ABORT: open handle found on bak file"; exit 1
fi
while read -r f; do
  if [ -n "$f" ] && lsof "$f" 2>/dev/null | grep -q .; then
    echo "ABORT: open handle found on $f"; exit 1
  fi
done < kyushu_cleanup_manifest_pmtiles_orphans.txt
echo "OK: no open handles."

echo "=== Deleting tmp-store: superseded/setaside working directories ==="
rm -rf tmp-store/01M0FNHYXSAMNVTV430XD3XB5T
rm -rf tmp-store/old-trial-setaside
rm -rf tmp-store/sea-crop-v1-superseded
rm -rf tmp-store/sea-crop-v2-superseded
rm -rf tmp-store/01KZM87D6PEKWM2B2ZEDSNFSQW
rm -rf tmp-store/01KZVPVTAM9V0QP8SRR42XRYKW

echo "=== Deleting bundle-store: stale manual backup ==="
rm -f bundle-store/japan.pmtiles.bak-20260810

echo "=== Deleting pmtiles-store: confirmed permanent orphans (148 files, ~2.54GB) ==="
xargs rm -f < kyushu_cleanup_manifest_pmtiles_orphans.txt

echo "=== Done. Verify with: df -h /Volumes/Migrate-2025-04 ==="
