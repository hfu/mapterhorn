"""Tile a global-tier-encoded lineage/provenance raster into PMTiles, the
categorical-data counterpart of aggregation_tile.py's elevation path
(mapterhorn-japan-bridge DECISIONS.md D93/D94/D96/D107).

Mirrors aggregation_tile.py's create_tiles()/create_tile() block-slicing
geometry exactly (same buffer_pixels convention, same child_z derivation)
so a lineage tile and its corresponding elevation tile at the same z-x-y
cover the identical ground footprint -- but slices an in-memory numpy array
(the provenance array lineage_provenance.compute_provenance()/
local_provenance_to_global() already produced) instead of reading windows
from a saved tiff, since there's no lineage-equivalent of merged-3857.tiff
on disk to read from (aggregation_merge.merge() never touches lineage data).
"""
from glob import glob
import math
import os

import mercantile
import numpy as np

import utils


def create_lineage_tiles(tmp_folder, aggregation_tile, category_data, buffer_pixels):
    base_x = aggregation_tile.x
    base_y = aggregation_tile.y
    base_z = aggregation_tile.z

    height, width = category_data.shape
    horizontal_block_count = (width - 2 * buffer_pixels) / 512
    assert math.floor(horizontal_block_count) == horizontal_block_count, (
        f'category_data width {width} (buffer {buffer_pixels}) is not an '
        f'exact multiple of 512 -- must match aggregation_tile.py\'s own '
        f'merged-3857.tiff geometry exactly'
    )
    child_z = base_z + int(math.log2(horizontal_block_count))
    z = child_z
    x_min = base_x * 2 ** (z - base_z)
    y_min = base_y * 2 ** (z - base_z)
    for i, x in enumerate(range(x_min, x_min + 2 ** (z - base_z))):
        for j, y in enumerate(range(y_min, y_min + 2 ** (z - base_z))):
            out_filepath = f'{tmp_folder}/{z}-{x}-{y}.webp'
            create_lineage_tile(i, j, category_data, out_filepath, buffer_pixels)


def create_lineage_tile(i, j, category_data, out_filepath, buffer_pixels):
    col_start = i * 512 + buffer_pixels
    col_end = (i + 1) * 512 + buffer_pixels
    row_start = j * 512 + buffer_pixels
    row_end = (j + 1) * 512 + buffer_pixels
    block = category_data[row_start:row_end, col_start:col_end]
    # lineage_provenance.local_provenance_to_global() already encodes nodata
    # as lineage_provenance.NODATA (255); the valid mask is simply "not that
    # sentinel" -- no separate alpha/mask array to carry through, unlike
    # aggregation_tile.py's Terrarium path (which gets its valid_mask from a
    # true/false nodata array computed alongside the elevation data itself).
    valid_mask = block != 255
    utils.save_lineage_tile(block, out_filepath, valid_mask=valid_mask)


def main(x, y, z, child_z, category_data, buffer_pixels, tmp_folder, aggregation_id):
    """Called from aggregation_run.py's run() when EMIT_LINEAGE is set,
    right after lineage_provenance.local_provenance_to_global() produces
    category_data. `aggregation_id` is the generation this item belongs
    to -- output goes into that generation's own pmtiles-store subtree.
    Writes its own block .webp files into a dedicated
    `{tmp_folder}/lineage-blocks/` subfolder, NOT tmp_folder itself --
    aggregation_tile.py's own create_tiles() writes elevation block files
    named identically ({z}-{x}-{y}.webp) into the same tmp_folder later in
    run(), and utils.create_archive() globs `*.webp` unconditionally, so
    sharing a directory would let one datatype's blocks silently clobber
    or bundle into the other's archive."""
    blocks_folder = f'{tmp_folder}/lineage-blocks'
    utils.create_folder(blocks_folder)

    aggregation_tile = mercantile.Tile(x=x, y=y, z=z)
    out_folder = utils.get_pmtiles_folder(x, y, z, layer='aggregation', datatype='lineage', generation_id=aggregation_id)
    utils.create_folder(out_folder)
    out_filepath = f'{out_folder}/{z}-{x}-{y}-{child_z}.pmtiles'
    # Same stale prior-run cleanup as aggregation_tile.py's own elevation
    # path, scoped to the lineage datatype's own generation-scoped
    # out_folder (see the audit comment there -- same reasoning).
    for stale_filepath in glob(f'{out_folder}/{z}-{x}-{y}-*.pmtiles'):
        if stale_filepath != out_filepath:
            os.remove(stale_filepath)

    create_lineage_tiles(blocks_folder, aggregation_tile, category_data, buffer_pixels)
    utils.create_archive(blocks_folder, out_filepath)
