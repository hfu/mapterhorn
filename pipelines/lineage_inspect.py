"""On-demand QA tool: visualize which source/product-type group actually
supplied each pixel of one aggregation tile's merged output.

Deliberately NOT wired into the production pipeline (aggregation_run.py
never imports this) -- it's a standalone diagnostic, run by hand against
one aggregation item at a time, using its own isolated tmp directory so
it can never collide with a real run's tmp-store/{aggregation_id}/{item}
state. This mirrors hfu/fusi's own pipelines/lineage.py, which was also
a standalone single-tile generator distinct from that project's
always-on `--emit-lineage` production flag -- this tool intentionally
stops at the lighter-weight half of that pattern (see DECISIONS.md D20):
no companion "-lineage" PMTiles archive, no per-run I/O cost added to
real production, no changes to aggregation_reproject.py/
aggregation_merge.py/aggregation_tile.py/aggregation_run.py at all.
Reuses aggregation_reproject.reproject() as-is (don't reimplement
warping) and re-derives the provenance mask with the same nodata-fill
loop aggregation_merge.merge() already uses for the real elevation
composite, just tracking *which group* filled each pixel instead of
*what value*.

Usage:
  python3 lineage_inspect.py <aggregation-csv> [--out lineage.png]

<aggregation-csv> is one of the existing per-tile files under
aggregation-store/{aggregation_id}/{z}-{x}-{y}-{child_z}-aggregation.csv
(same file aggregation_run.py itself consumes).
"""
import argparse
import shutil
import tempfile
from glob import glob

import numpy as np
import rasterio
from PIL import Image

import aggregation_reproject
import utils

# Fixed palette: index 0..6 for the seven priority tiers (1, 5a, 5b, 5c,
# 10a, 10b, sea), -1 for nodata (no source covered this pixel at all).
# Distinct hues per resolution family (blue=1m, green=5m shades,
# orange=10m shades, grey=sea) so a glance shows both "which family"
# and "which product type within it" won.
#
# Keyed by GLOBAL tier, not by a group's position in one tile's own
# get_grouped_source_items() list -- most tiles don't have all seven
# tiers present (e.g. no 5C data for that cell), so the list is a
# subsequence of the full seven, not always starting at tier 0. Coloring
# by list position instead of global tier silently relabels every group
# after the first gap (e.g. a tile missing 5B/5C/10A would have its real
# `sea` group land on list index 2 and get painted 5B's green) -- found
# by inspecting real D20 sample output, where a jpnational1/jpnationalsea
# tile's ocean half rendered in 10a's orange. Fixed by mapping every
# group through GLOBAL_TIER before indexing PALETTE.
PALETTE = {
    -1: (255, 255, 255),  # nodata -> white
    0: (30, 60, 200),     # 1m (DEM1A) -> blue
    1: (0, 130, 0),       # 5a (DEM5A) -> dark green
    2: (110, 200, 90),    # 5b (DEM5B) -> medium green
    3: (190, 235, 170),   # 5c (DEM5C) -> pale green
    4: (200, 120, 0),     # 10a (DEM10A) -> dark orange
    5: (240, 190, 120),   # 10b (DEM10B) -> light orange
    6: (150, 150, 150),   # sea (GLO-30) -> grey
}

# (source, product-type rank) -> global tier index, matching this
# project's own seven-tier priority order (DECISIONS.md D20): 1, 5a, 5b,
# 5c, 10a, 10b, sea. jpnationalsea has no product-type letter, so its
# rank is always utils.PRODUCT_TYPE_RANK's fallback, 0.
GLOBAL_TIER = {
    ('jpnational1', 0): 0,
    ('jpnational5', 0): 1,
    ('jpnational5', 1): 2,
    ('jpnational5', 2): 3,
    ('jpnational10', 0): 4,
    ('jpnational10', 1): 5,
    ('jpnationalsea', 0): 6,
}


def group_product_type_rank(group):
    return utils.get_product_type_rank(group[0]['filename'])


def global_tier_of(group):
    source = group[0]['source']
    rank = group_product_type_rank(group)
    return GLOBAL_TIER.get((source, rank))


def group_label(group):
    """Human-readable tier label for a get_grouped_source_items() group,
    e.g. 'jpnational5/B'."""
    source = group[0]['source']
    m = utils.PRODUCT_TYPE_PATTERN.search(group[0]['filename'])
    return f'{source}/{m.group(1)}' if m else source


def compute_provenance(filepath, tmp_folder):
    """Re-derive which group filled each pixel, using the same nodata-fill
    walk aggregation_merge.merge() uses for the real elevation composite --
    just tracking group index instead of elevation value."""
    num_tiff_files = len(glob(f'{tmp_folder}/*-3857.tiff'))
    if num_tiff_files == 0:
        raise ValueError(f'no reprojected tiffs found for {filepath}')

    tiff_filepaths = [f'{tmp_folder}/{i}-3857.tiff' for i in range(num_tiff_files)]

    with rasterio.open(tiff_filepaths[0]) as src:
        merged = np.nan_to_num(src.read(1), nan=-9999)
        provenance = np.where(merged != -9999, 0, -1).astype('int16')

    for i, tiff_filepath in enumerate(tiff_filepaths[1:], start=1):
        with rasterio.open(tiff_filepath) as src:
            current = np.nan_to_num(src.read(1), nan=-9999)
        fill_mask = (provenance == -1) & (current != -9999)
        provenance[fill_mask] = i
        if -1 not in provenance:
            break

    return provenance, num_tiff_files


def provenance_to_rgb(provenance, groups):
    """`provenance` holds each pixel's LOCAL group-list index (or -1);
    map through global_tier_of() before looking up PALETTE so a tile
    missing some tiers doesn't shift every later group onto the wrong
    color (see PALETTE's own comment)."""
    h, w = provenance.shape
    out = np.zeros((h, w, 3), dtype=np.uint8)
    for val in np.unique(provenance):
        if val == -1:
            color = PALETTE[-1]
        else:
            tier = global_tier_of(groups[val])
            color = PALETTE.get(tier, (255, 0, 255))  # magenta = unrecognized (source, rank)
        out[provenance == val] = color
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('aggregation_csv')
    parser.add_argument('--out', default='lineage.png')
    args = parser.parse_args()

    filepath = args.aggregation_csv
    groups = utils.get_grouped_source_items(filepath)
    print(f'{len(groups)} priority group(s) for this tile:')
    for i, group in enumerate(groups):
        print(f'  {i}: {group_label(group)} ({len(group)} file(s)) [global tier {global_tier_of(group)}]')

    tmp_folder = tempfile.mkdtemp(prefix='lineage-inspect-')
    try:
        aggregation_reproject.reproject(filepath, tmp_folder)
        provenance, num_groups_used = compute_provenance(filepath, tmp_folder)
    finally:
        shutil.rmtree(tmp_folder, ignore_errors=True)

    print(f'{num_groups_used} group(s) actually warped for this tile '
          f'(loop may stop early once one group has no nodata).')
    unique, counts = np.unique(provenance, return_counts=True)
    total = provenance.size
    for val, count in sorted(zip(unique, counts)):
        label = 'nodata' if val == -1 else group_label(groups[val])
        print(f'  {label}: {count} px ({100*count/total:.1f}%)')

    rgb = provenance_to_rgb(provenance, groups)
    Image.fromarray(rgb).save(args.out)
    print(f'Wrote {args.out}')


if __name__ == '__main__':
    main()
