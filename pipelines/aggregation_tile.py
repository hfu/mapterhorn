from glob import glob
import math
import os
import json

import mercantile
import numpy as np
import rasterio

import utils

# 'terrarium' (default): elevation sources, lossless Terrarium RGB encoding
# (upstream's original behavior). 'rgb': orthophoto/imagery sources, lossy
# RGB WebP encoding (added in 5eaa737 for the Freetown orthophoto project --
# see FORK_NOTES.md section B). Elevation sources must not go through the
# 'rgb' path: it clips raw elevation floats into an 8-bit 0-255 range with no
# Terrarium encoding at all, destroying sub-meter precision and, combined
# with Terrarium's ~257x decode amplification per RGB unit, turning every
# whole-meter quantization step into a fake multi-hundred-meter cliff.
TILE_ENCODING = os.environ.get('TILE_ENCODING', 'terrarium')

def create_tiles(tmp_folder, aggregation_tile, tiff_filepath, buffer_pixels):
    base_x = aggregation_tile.x
    base_y = aggregation_tile.y
    base_z = aggregation_tile.z

    child_z = None
    with rasterio.open(tiff_filepath) as src:
        assert len(src.block_shapes) >= 1
        assert src.block_shapes[0] == (512, 512)
        horizontal_block_count = (src.width - 2 * buffer_pixels) / 512
        assert math.floor(horizontal_block_count) == horizontal_block_count
        child_z = base_z + int(math.log2(horizontal_block_count))
    z = child_z
    x_min = base_x * 2 ** (z - base_z)
    y_min = base_y * 2 ** (z - base_z)
    for i, x in enumerate(range(x_min, x_min + 2 ** (z - base_z))):
        for j, y in enumerate(range(y_min, y_min + 2 ** (z - base_z))):
            out_filepath = f'{tmp_folder}/{z}-{x}-{y}.webp'
            create_tile(i, j, tiff_filepath, out_filepath, buffer_pixels)

def create_tile(i, j, tiff_filepath, out_filepath, buffer_pixels):
    col_start = i * 512 + buffer_pixels
    col_end = (i + 1) * 512 + buffer_pixels
    row_start = j * 512 + buffer_pixels
    row_end = (j + 1) * 512 + buffer_pixels
    window = rasterio.windows.Window(
        col_off=col_start,
        row_off=row_start,
        width=col_end - col_start,
        height=row_end - row_start
    )
    subdata = None
    mask_data = None
    with rasterio.open(tiff_filepath) as src:
        if TILE_ENCODING == 'terrarium':
            subdata = src.read(1, window=window, out_shape=(512, 512))
        elif src.count >= 3:
            subdata = src.read([1, 2, 3], window=window, out_shape=(3, 512, 512))
            subdata = subdata.transpose((1, 2, 0))
        else:
            subdata = src.read(1, window=window, out_shape=(512, 512))
        if TILE_ENCODING != 'terrarium':
            # Read mask (nodata/alpha) for this window only - dataset_mask() always
            # returns an array, so read it windowed rather than probing full-res first.
            mask_data = src.dataset_mask(window=window, out_shape=(512, 512))
            mask_data = mask_data.astype(np.float32) / 255.0

    if TILE_ENCODING == 'terrarium':
        valid_mask = subdata != -9999
        subdata[subdata == -9999] = 0
        utils.save_terrarium_tile(subdata, out_filepath, valid_mask=valid_mask)
    else:
        utils.save_rgb_tile(subdata, out_filepath, mask_data=mask_data)

def main(filepath, tmp_folder):
    filename = filepath.split('/')[-1]

    z, x, y, child_z = [int(a) for a in filename.replace('-aggregation.csv', '').split('-')]


    pmtiles_done_filepath = f'{tmp_folder}/pmtiles-done'
    if os.path.isfile(pmtiles_done_filepath):
        print(f'tiling {filename} already done...')
        return

    merge_done = os.path.isfile(f'{tmp_folder}/merge-done')
    if not merge_done:
        print('merge not done yet...')
        return

    buffer_pixels = None
    with open(f'{tmp_folder}/reprojection.json') as f:
        metadata = json.load(f)
        buffer_pixels = metadata['buffer_pixels']

    tiff_filepath = f'{tmp_folder}/merged-3857.tiff'

    aggregation_tile = mercantile.Tile(x=x, y=y, z=z)
    out_folder = utils.get_pmtiles_folder(x, y, z)
    utils.create_folder(out_folder)
    out_filepath = f'{out_folder}/{z}-{x}-{y}-{child_z}.pmtiles'
    # Remove stale prior-generation output at this exact macrotile position (same
    # z-x-y, different child_z) so bundle.py's unconditional
    # glob('pmtiles-store/*.pmtiles' + '*/*.pmtiles') never mixes an old run's
    # now-superseded archive in alongside this run's current one -- pmtiles-store
    # is otherwise never cleaned between runs (DECISIONS.md D12's open question).
    # get_pmtiles_folder() buckets by z7 parent, so out_folder can hold many
    # unrelated macrotiles' files; the z-x-y-prefixed glob (not out_folder-wide)
    # keeps this scoped to just this position's own prior generations.
    for stale_filepath in glob(f'{out_folder}/{z}-{x}-{y}-*.pmtiles'):
        if stale_filepath != out_filepath:
            os.remove(stale_filepath)
    create_tiles(tmp_folder, aggregation_tile, tiff_filepath, buffer_pixels)
    utils.create_archive(tmp_folder, out_filepath)
    utils.run_command(f'touch {pmtiles_done_filepath}')
