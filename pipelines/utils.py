import subprocess
import gzip
from pathlib import Path
from glob import glob
import math
import os
import hashlib
import re

import numpy as np

from rasterio.warp import transform_bounds
import mercantile
import imagecodecs
from pmtiles.tile import zxy_to_tileid, tileid_to_zxy, TileType, Compression
from pmtiles.writer import Writer

# Upstream's original value is 12. 4bf6e535 raised it to 17 globally as a
# safety cap for a ~4cm/px (maxzoom~21) Freetown orthophoto source, whose
# gap from macrotile_z=12 would have made aggregation_reproject.py try to
# materialize a ~256GiB raster per macrotile. That's the right fix for a
# z21 source, but applied unconditionally it also forces every 1m
# elevation source (maxzoom 17) into macrotile_z==maxzoom -- i.e. one
# macrotile per single output tile, with no room for
# aggregation_covering.py to group a sensible contiguous area into one
# reprojection. Each macrotile is warped independently with only a small
# (150-unit) edge buffer, and cubicspline's resampling kernel needs
# neighboring pixels outside that buffer for a truly seamless result --
# plausible root cause of the seam/staircase artifact seen only below the
# native zoom. terrarium mode restores upstream's 12 (still >=
# num_overviews below our own maxzoom 17, so the pyramid range is
# unaffected); rgb/orthophoto mode keeps the 17 safety cap unchanged.
macrotile_z = 12 if os.environ.get('TILE_ENCODING', 'terrarium') == 'terrarium' else 17
macrotile_buffer_3857 = 150
num_overviews = 6

X_MIN_3857, _, X_MAX_3857, __ = transform_bounds('EPSG:4326', 'EPSG:3857', -180, 0, 180, 0)

def run_command(command, silent=True, env=None):
    if env is None:
        env = os.environ.copy()
    if not silent:
        print(command)
    p = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    stdout, stderr = p.communicate()
    err = stderr.decode()
    if err != '' and not silent:
        print(err)
    out = stdout.decode()
    if out != '' and not silent:
        print(out)
    return out, err

def create_folder(path):
    folder_path = Path(path)
    folder_path.mkdir(parents=True, exist_ok=True)

def get_aggregation_ids():
    '''
    returns aggregation ids ordered from oldest to newest
    '''
    return list(sorted([path.split('/')[-1] for path in glob('aggregation-store/*')]))

def get_vertical_rounding_multiplier(z):
    return int(2 ** ((10 - z) / 2) / (1 / 256))

def save_terrarium_tile(data, filepath, valid_mask=None):
    """`valid_mask` (bool array, same shape as `data`, True = real data) is
    encoded as an alpha channel so gaps (no source coverage at all -- not
    the same as a small internal hole already filled by priority-merge)
    survive as nodata through downsampling's tile pyramid, instead of
    silently becoming a fake elevation of 0m that then contaminates a
    weighted average with neighboring real data. `None` means "fully
    valid" (whole-tile alpha=255), for callers with no gap information."""
    filename = filepath.split('/')[-1]
    z = int(filename.split('-')[0])

    # full terrarium resolution of 1/256 at `full_resolution_zoom`
    # multiples of 2 of full terrarium resolution at lower zooms
    full_resolution_zoom = 19
    factor = 2 ** (full_resolution_zoom - z) / 256
    data = np.round(data / factor) * factor

    data += 32768
    rgba = np.zeros((512, 512, 4), dtype=np.uint8)
    np.seterr(all='raise')
    try:
        rgba[..., 0] = data // 256
        rgba[..., 1] = data % 256
        rgba[..., 2] = (data - np.floor(data)) * 256
    except FloatingPointError:
        print(f'FloatingPointError raised in {filepath}')
        raise FloatingPointError()
    rgba[..., 3] = 255 if valid_mask is None else np.where(valid_mask, 255, 0).astype(np.uint8)
    with open(filepath, 'wb') as f:
        f.write(imagecodecs.webp_encode(rgba, lossless=True))

def save_rgb_tile(rgb_data, filepath, mask_data=None):
    """Save orthophoto RGB tile as WebP with optional alpha channel from mask.
    Black pixels (0,0,0) are treated as transparent/nodata."""
    if rgb_data.ndim == 2:
        rgb_data = np.stack([rgb_data, rgb_data, rgb_data], axis=2)

    rgb_data = np.nan_to_num(rgb_data, nan=0.0)
    rgb_data = np.clip(rgb_data, 0, 255).astype(np.uint8)

    # Create alpha channel: nodata from mask + black pixel transparency
    alpha = np.ones((rgb_data.shape[0], rgb_data.shape[1]), dtype=np.uint8) * 255

    if mask_data is not None:
        mask_data = np.clip(mask_data, 0, 1) * 255
        alpha = mask_data.astype(np.uint8)

    # Make black pixels (0,0,0) transparent (nodata)
    black_pixels = (rgb_data[:,:,0] == 0) & (rgb_data[:,:,1] == 0) & (rgb_data[:,:,2] == 0)
    alpha[black_pixels] = 0

    rgba_data = np.dstack([rgb_data, alpha])
    rgba_data = np.ascontiguousarray(rgba_data)
    encoded = imagecodecs.webp_encode(rgba_data, lossless=False, level=80)

    with open(filepath, 'wb') as f:
        if encoded:
            f.write(encoded)

def create_archive(tmp_folder, out_filepath):
    with open(out_filepath, 'wb') as f1:
        writer = Writer(f1)
        min_z = math.inf
        max_z = 0
        min_lon = math.inf
        min_lat = math.inf
        max_lon = -math.inf
        max_lat = -math.inf

        tile_ids = []
        for filepath in glob(f'{tmp_folder}/*.webp'):
            filename = filepath.split('/')[-1]
            z, x, y = [int(a) for a in filename.replace('.webp', '').split('-')]
            tile_ids.append(zxy_to_tileid(z=z, x=x, y=y))
        tile_ids = sorted(tile_ids)

        if not tile_ids:
            raise ValueError(f'No tiles found in {tmp_folder}. Parent tile processing may have skipped all tiles.')

        for tile_id in tile_ids:
            z, x, y = tileid_to_zxy(tile_id)
            filepath = f'{tmp_folder}/{z}-{x}-{y}.webp'
            with open(filepath, 'rb') as f2:
                writer.write_tile(tile_id, f2.read())

            max_z = max(max_z, z)
            min_z = min(min_z, z)
            west, south, east, north = mercantile.bounds(x, y, z)
            min_lon = min(min_lon, west)
            min_lat = min(min_lat, south)
            max_lon = max(max_lon, east)
            max_lat = max(max_lat, north)

        min_lon_e7 = int(min_lon * 1e7)
        min_lat_e7 = int(min_lat * 1e7)
        max_lon_e7 = int(max_lon * 1e7)
        max_lat_e7 = int(max_lat * 1e7)

        writer.finalize(
            {
                'tile_type': TileType.WEBP,
                'tile_compression': Compression.NONE,
                'min_zoom': min_z,
                'max_zoom': max_z,
                'min_lon_e7': min_lon_e7,
                'min_lat_e7': min_lat_e7,
                'max_lon_e7': max_lon_e7,
                'max_lat_e7': max_lat_e7,
                'center_zoom': int(0.5 * (min_z + max_z)),
                'center_lon_e7': int(0.5 * (min_lon_e7 + max_lon_e7)),
                'center_lat_e7': int(0.5 * (min_lat_e7 + max_lat_e7)),
            },
            {
                'attribution': '<a href="https://mapterhorn.com/attribution">© Mapterhorn</a>'
            },
        )

def get_aggregation_item_string(aggregation_id, filename):
    result = ''
    filepath = f'aggregation-store/{aggregation_id}/{filename}'
    if not os.path.isfile(filepath):
        return None
    
    with open(filepath) as f:
        result = ''.join([l.strip() for l in f.readlines()])
    
    return result.strip()

def get_dirty_aggregation_filenames(current_aggregation_id, last_aggregation_id):
    filepaths = sorted(glob(f'aggregation-store/{current_aggregation_id}/*-aggregation.csv'))

    if last_aggregation_id is None:
        return [filepath.split('/')[-1] for filepath in filepaths]

    dirty_filenames = []
    for filepath in filepaths:
        filename = filepath.split('/')[-1]
        current = get_aggregation_item_string(current_aggregation_id, filename)
        last = get_aggregation_item_string(last_aggregation_id, filename)
        if current != last:
            dirty_filenames.append(filename)
    return dirty_filenames

def get_pmtiles_folder(x, y, z):
    if z < 7:
        return 'pmtiles-store'
    if z == 7:
        return f'pmtiles-store/{z}-{x}-{y}'
    else:
        parent = mercantile.parent(mercantile.Tile(x=x, y=y, z=z), zoom=7)
        return f'pmtiles-store/{parent.z}-{parent.x}-{parent.y}'

# GSI's own DEM naming embeds a product-type letter after the resolution
# digits (e.g. ...-DEM5A-, ...-DEM10B-): A = airborne laser (LiDAR,
# highest accuracy), B/C = photogrammetry-derived fallbacks used where no
# LiDAR survey exists yet (20cm/40cm GSD respectively for the 5m tier).
# Lower rank = higher accuracy = should be tried first. Sources with no
# such suffix (e.g. jpnationalsea's Copernicus GLO-30 files) get rank 0,
# since there is only ever one product type per cell for those -- rank
# is meaningless as a priority signal there, only used to keep the group
# key well-defined.
PRODUCT_TYPE_RANK = {'A': 0, 'B': 1, 'C': 2}
# Case-insensitive: most GSI filenames use uppercase (DEM5A/DEM10B), but
# some older (pre-~2018) vintages use lowercase (dem5b/dem10b) -- 6,676
# such files found across source-store this session. The pattern used to
# be uppercase-only, silently falling through to rank 0 (the same rank as
# tier-A/highest-accuracy) for every lowercase file -- misclassifying
# real tier-B/C data as if unranked. See mapterhorn-japan-bridge
# DECISIONS.md D28.
PRODUCT_TYPE_PATTERN = re.compile(r'-DEM\d+([A-C])-', re.IGNORECASE)

def get_product_type_rank(filename):
    m = PRODUCT_TYPE_PATTERN.search(filename)
    if m:
        return PRODUCT_TYPE_RANK[m.group(1).upper()]
    return 0

# Group source items by (maxzoom, source, product-type rank), in that
# priority order -- e.g. for a tile covered by jpnational1/jpnational5
# (a mix of DEM5A/5B/5C)/jpnational10 (DEM10A/10B)/jpnationalsea, this
# produces up to seven groups in priority order: 1, 5a, 5b, 5c, 10a,
# 10b, sea (DECISIONS.md D20). Each group is now guaranteed a single
# product type, so aggregation_reproject.py's per-group gdalbuildvrt
# call never has to arbitrate between different-accuracy files anymore
# -- that arbitration now happens via aggregation_merge.py's existing
# per-pixel nodata-fill + Gaussian-blurred-seam compositing across
# groups (the same mechanism already used for 1m vs 5m vs 10m vs sea),
# reused as-is since it already handles an arbitrary number of groups.
def get_grouped_source_items(filepath):
    lines = []
    with open(filepath) as f:
        lines = f.readlines()
    lines = lines[1:] # skip header
    line_tuples = []
    for line in lines:
        source, filename, maxzoom = line.strip().split(',')
        maxzoom = int(maxzoom)
        line_tuples.append((
            -maxzoom,
            source,
            get_product_type_rank(filename),
            filename
        ))
    line_tuples = sorted(line_tuples)
    grouped_source_items = []

    first_line_tuple = line_tuples[0]
    last_group_signature = (first_line_tuple[0], first_line_tuple[1], first_line_tuple[2])
    current_group = [{
        'maxzoom': -first_line_tuple[0],
        'source': first_line_tuple[1],
        'filename': first_line_tuple[3],
    }]
    for line_tuple in line_tuples[1:]:
        current_group_signature = (line_tuple[0], line_tuple[1], line_tuple[2])
        if current_group_signature != last_group_signature:
            grouped_source_items.append(current_group)
            current_group = []
            last_group_signature = current_group_signature
        current_group.append({
            'maxzoom': -line_tuple[0],
            'source': line_tuple[1],
            'filename': line_tuple[3],
        })
    grouped_source_items.append(current_group)
    return grouped_source_items

class HashWriter:
    def __init__(self, f):
        self.f = f
        self.md5 = hashlib.md5()
    def write(self, data):
        self.md5.update(data)
        return self.f.write(data)
    def tell(self):
        return self.f.tell()
    def flush(self):
        return self.f.flush()
    def close(self):
        return self.f.close()


# Japan-specific quadrant classifier for downsampling priority ordering
# (mapterhorn-japan-bridge DECISIONS.md D25). Translates
# japan-geotiff-dem/scripts/quadrans_script.rb's mesh-code-based
# North/East/South/West split into direct lon/lat thresholds, using the
# JIS 1st-order mesh grid's own defining formula (mesh code y -> latitude
# = y * 2/3 deg; mesh code x -> longitude = x + 100 deg), since aggregation
# items here are indexed by mercator tile z/x/y, not GSI mesh codes, and
# have no mesh code of their own to classify directly.
JAPAN_QUADRANS_PRIORITY = {'north': 0, 'south': 1, 'east': 2, 'west': 3}

def japan_quadrans_of(lon, lat):
    if lat >= 62 * (2.0 / 3.0):  # ~41.333 deg N (quadrans_script.rb: y >= 62)
        return 'north'
    if lon >= 38 + 100:  # 138 deg E (quadrans_script.rb: x >= 38)
        return 'east'
    if lon <= 32 + 100:  # 132 deg E (quadrans_script.rb: x <= 32)
        return 'south'
    return 'west'


# Manifest source-catalog file_list.csv[.gz] opener (mapterhorn-japan-bridge
# DECISIONS.md D26). Prefers the gzip-compressed form -- large national-scope
# manifests (jpnational1/5, hundreds of thousands of rows) exceed GitHub's
# 50MB recommended file-size threshold as plain CSV; gzip shrinks this
# highly-repetitive URL-list text dramatically. Falls back to a plain .csv
# for any source not yet converted, so this is backward compatible.
def open_manifest(source):
    gz_path = Path(f'../source-catalog/{source}/file_list.csv.gz')
    csv_path = Path(f'../source-catalog/{source}/file_list.csv')
    if gz_path.is_file():
        return gzip.open(gz_path, 'rt', newline='')
    return open(csv_path, newline='')

def manifest_path_glob():
    """All source-catalog dirs with either manifest form, source name only."""
    names = set()
    for p in Path('../source-catalog').glob('*/file_list.csv.gz'):
        names.add(p.parent.name)
    for p in Path('../source-catalog').glob('*/file_list.csv'):
        names.add(p.parent.name)
    return sorted(names)

def manifest_exists(source):
    return (Path(f'../source-catalog/{source}/file_list.csv.gz').is_file()
            or Path(f'../source-catalog/{source}/file_list.csv').is_file())
