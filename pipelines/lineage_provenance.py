"""Shared provenance-computation core for lineage tiles (mapterhorn-japan-
bridge DECISIONS.md D93/D94/D107). Extracted out of lineage_inspect.py (the
standalone diagnostic tool, still the only caller before D107) so
aggregation_run.py's production EMIT_LINEAGE path can reuse the exact same
logic instead of drifting from it -- D93 explicitly recommended sharing this
rather than reimplementing.

(source, product-type rank) -> GLOBAL_TIER and compute_provenance() are the
two pieces production actually needs. group_label()/PALETTE/provenance_to_rgb()
stay in lineage_inspect.py -- they're presentation-only, never needed outside
the standalone diagnostic.
"""
from glob import glob

import numpy as np
import rasterio

import utils

# (source, product-type rank) -> global tier index, matching this project's
# own seven-tier priority order (DECISIONS.md D20): 1, 5a, 5b, 5c, 10a, 10b,
# sea. jpnationalsea has no product-type letter, so its rank is always
# utils.PRODUCT_TYPE_RANK's fallback, 0.
GLOBAL_TIER = {
    ('jpnational1', 0): 0,
    ('jpnational5', 0): 1,
    ('jpnational5', 1): 2,
    ('jpnational5', 2): 3,
    ('jpnational10', 0): 4,
    ('jpnational10', 1): 5,
    ('jpnationalsea', 0): 6,
}

NUM_CATEGORIES = len(GLOBAL_TIER)  # 7, matches lineage_downsample.NUM_CATEGORIES
NODATA = 255  # single-byte category encoding (utils.save_lineage_tile); -1
              # (lineage_downsample.NODATA) is the in-memory sentinel instead


def group_product_type_rank(group):
    return utils.get_product_type_rank(group[0]['filename'])


def global_tier_of(group):
    source = group[0]['source']
    rank = group_product_type_rank(group)
    return GLOBAL_TIER.get((source, rank))


def compute_provenance(filepath, tmp_folder):
    """Re-derive which group filled each pixel, using the same nodata-fill
    walk aggregation_merge.merge() uses for the real elevation composite --
    just tracking group index instead of elevation value. Must run against
    tmp_folder's per-group `{i}-3857.tiff` reprojected files -- these are
    consumed and deleted by aggregation_merge.merge() itself, so this has to
    run *before* merge(), right after aggregation_reproject.reproject()."""
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


def local_provenance_to_global(provenance, groups):
    """Convert compute_provenance()'s LOCAL group-list index array (values
    0..len(groups)-1, -1=nodata) to GLOBAL_TIER indices (0..6, NODATA=255
    for storage) -- required before persisting: a tile's own `groups` list
    is a subsequence of the full seven tiers (most tiles don't have all
    seven present), so the LOCAL index's meaning is tile-specific and not
    comparable across tiles (lineage_inspect.py's own PALETTE comment
    documents this same trap for the visualization path). Returns a uint8
    array, ready for utils.save_lineage_tile()."""
    global_of_local = np.array(
        [global_tier_of(group) for group in groups], dtype=np.uint8
    ) if groups else np.zeros(0, dtype=np.uint8)
    out = np.full(provenance.shape, NODATA, dtype=np.uint8)
    valid = provenance != -1
    out[valid] = global_of_local[provenance[valid]]
    return out
