# jphokkaidodem1

**Status: prototype / bridge entry, not upstream.** This is a regional
subset of Japan's 1m DEM (Hokkaido only), fed directly from
[`smartmaps/japan-geotiff-dem`](https://source.coop/smartmaps/japan-geotiff-dem)
on Source Cooperative -- not from raw GSI files. It exists to produce
terrain tiles for Hokkaido's newly-refreshed 1m coverage without
waiting for `jpdem1a` (the existing full-Japan 1m entry, produced via
[`hfu/fusi`](https://github.com/hfu/fusi)) to be regenerated upstream.
Once Mapterhorn's own pipeline picks up the refreshed national 1m data
through the normal `jpdem1a` path, this entry should be retired.

- `file_list.txt` — deduplicated (newest survey date per mesh
  coordinate, as of this entry's creation) list of public HTTPS URLs
  under `smartmaps/japan-geotiff-dem`'s `1/` prefix, restricted to
  Hokkaido mesh codes. Generated from `japan-geotiff-dem`'s local
  `dst/1/` output — see that repo's `HANDOVER.md` for how far through
  Hokkaido's 46 region-packs processing had gotten when this was built.
- Unlike `jpdem1a`, this entry uses `source_download.py` +
  `file_list.txt` directly (standard Mapterhorn source pipeline) rather
  than going through `fusi`.

**Known caveat, not yet resolved**: deduplication was only done within
the files this entry's own `file_list.txt` was built from. It's
possible the *original* full-Japan 2026-05-28 upload published an
older-vintage file at the same mesh coordinate for a mesh that has
since been updated -- if so, that stale file is still live on the
bucket under a different (older-dated) filename, and Mapterhorn's
same-maxzoom tie-break (earlier lexicographic name wins) would
currently favor the *older* date string over the newer one. Worth a
dedicated remote-side check before a real production run, not just a
local one.
