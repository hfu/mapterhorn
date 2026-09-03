from glob import glob
import math
import time
import sys
import json
import os
from multiprocessing import Pool

import mercantile
from pmtiles.tile import zxy_to_tileid, TileType, Compression
from pmtiles.reader import Reader, MmapSource, all_tiles
from pmtiles.writer import Writer

# D104/D105 (mapterhorn-japan-bridge DECISIONS.md): the pmtiles library's
# Writer buffers tile bytes via tempfile.TemporaryFile() with no path
# argument, so it lands on tempfile.gettempdir() -- which honors TMPDIR,
# but macOS login/SSH sessions already export TMPDIR (pointing at the
# per-user /var/folders/.../T/ directory on the small boot volume) before
# this script ever runs, so os.environ.setdefault('TMPDIR', ...) (D104's
# first attempt) was always a no-op: the key is never actually absent.
# Force-override unconditionally instead.
os.environ['TMPDIR'] = os.path.abspath('pmtiles-store/tmp-store/writer-scratch/')
os.makedirs(os.environ['TMPDIR'], exist_ok=True)
import tempfile
tempfile.tempdir = None  # drop any cached resolution from before this line ran

import utils

# D93/D96/D107: 'elevation' (default, 1号's only mode) or 'lineage' -- which
# pmtiles-store datatype tree to bundle. A single bundle.py invocation
# handles exactly one; building both means running this script twice
# (mirrors aggregation_run.py's EMIT_LINEAGE / downsampling_run.py's
# DOWNSAMPLING_DATATYPE, same env-var convention).
BUNDLE_DATATYPE = os.environ.get('BUNDLE_DATATYPE', 'elevation')

def get_parent_to_filepaths(only_dirty, num_aggregations, datatype='elevation', generation_id=None):
    # D95/D107 (+ generation_id, 2026-09-04): pmtiles-store is split by
    # layer (aggregation/downsampling), datatype (elevation/lineage), and
    # generation_id -- bundle.py's job is to combine BOTH layers of one
    # datatype OF ONE GENERATION into the final archive, so glob each
    # layer's generation subtree separately rather than assuming a single
    # shared tree the way the pre-D107 flat pmtiles-store/ did. Globbing
    # without the generation level would silently mix two generations'
    # archives the moment a second layered generation exists (the exact
    # D74-D76-class hazard the generation_id level closes).
    if not generation_id:
        raise ValueError('get_parent_to_filepaths() requires generation_id')
    filepaths = sorted(
        glob(f'pmtiles-store/aggregation/{datatype}/{generation_id}/*.pmtiles') +
        glob(f'pmtiles-store/aggregation/{datatype}/{generation_id}/*/*.pmtiles') +
        glob(f'pmtiles-store/downsampling/{datatype}/{generation_id}/*.pmtiles') +
        glob(f'pmtiles-store/downsampling/{datatype}/{generation_id}/*/*.pmtiles')
    )

    parent_to_filepath = {}
    dirty_parents = get_dirty_parents(num_aggregations)

    for filepath in filepaths:
        filename = filepath.split('/')[-1]
        z, x, y, child_z = [int(a) for a in filename.replace('.pmtiles', '').split('-')]

        parent = None
        # child_z here is the zoom level of the tiles inside this specific pmtiles-store
        # archive, not utils.macrotile_z (the aggregation covering grid zoom) - the two are
        # unrelated despite historically sharing the value 12. Aggregation output archives
        # always have child_z == the source's forced maxzoom (>= utils.macrotile_z, so never
        # <= 12 once macrotile_z is raised above 12, as this project's is). But downsampling
        # generates archives at every zoom down to 1, so low-zoom overview archives DO take
        # this branch and land in the single global planet.pmtiles bucket - correct behavior,
        # not dead code.
        if child_z <= 12:
            parent = mercantile.Tile(x=0, y=0, z=0)
        else:
            assert z >= 6
            if z == 6:
                parent = mercantile.Tile(x=x, y=y, z=z)
            else:
                parent = mercantile.parent(mercantile.Tile(x=x, y=y, z=z), zoom=6)

        if only_dirty and parent not in dirty_parents:
            continue

        if parent not in parent_to_filepath:
            parent_to_filepath[parent] = []

        parent_to_filepath[parent].append(filepath)

    return parent_to_filepath

def get_dirty_parents(num_aggregations):
    dirty_parents = set([mercantile.Tile(x=0, y=0, z=0)])

    aggregation_ids = utils.get_aggregation_ids()
    assert len(aggregation_ids) >= num_aggregations

    for offset in range(num_aggregations):
        current_aggregation_id = aggregation_ids[-1 - offset]
        last_aggregation_id = None if len(aggregation_ids) == 1 else aggregation_ids[-2 - offset]
        aggregation_filenames = utils.get_dirty_aggregation_filenames(current_aggregation_id, last_aggregation_id)

        for filename in aggregation_filenames:
            z, x, y, child_z = [int(a) for a in filename.replace('-aggregation.csv', '').split('-')]
            if child_z >= 13:
                dirty_parents.add(mercantile.parent(mercantile.Tile(x=x, y=y, z=z), zoom=6))

    return list(dirty_parents)

def read_full_archive(filepath):
    tile_id_to_bytes = {}
    with open(filepath , 'r+b') as f2:
        reader = Reader(MmapSource(f2))
        for tile_tuple, tile_bytes in all_tiles(reader.get_bytes):
            tile_id = zxy_to_tileid(*tile_tuple)
            tile_id_to_bytes[tile_id] = tile_bytes
    return tile_id_to_bytes

def create_archive(filepaths, name):
    utils.create_folder('bundle-store')
    out_filepath = f'bundle-store/{name}.pmtiles'
    checksum = None
    min_z = math.inf
    max_z = 0
    min_lon = math.inf
    min_lat = math.inf
    max_lon = -math.inf
    max_lat = -math.inf
    with open(out_filepath, 'wb') as f1:
        hash_writer = utils.HashWriter(f1)
        writer = Writer(hash_writer)

        tile_ids_and_filepaths = []

        j = 0
        for filepath in filepaths:
            filename = filepath.split('/')[-1]
            z, x, y, child_z = [int(a) for a in filename.replace('.pmtiles', '').split('-')]
            parent = mercantile.Tile(x=x, y=y, z=z)
            tiles = []
            if z == child_z:
                tiles.append(parent)
            else:
                tiles += mercantile.children(parent, zoom=child_z)
            for tile in tiles:
                tile_id = zxy_to_tileid(tile.z, tile.x, tile.y)
                tile_ids_and_filepaths.append((tile_id, filepath))

            max_z = max(max_z, child_z)
            min_z = min(min_z, child_z)
            west, south, east, north = mercantile.bounds(x, y, z)
            min_lon = min(min_lon, west)
            min_lat = min(min_lat, south)
            max_lon = max(max_lon, east)
            max_lat = max(max_lat, north)
            j += 1
            if j % 1000 == 0:
                print(f'prepared {j:_} / {len(filepaths):_} filepaths...')

        tile_ids_and_filepaths = sorted(tile_ids_and_filepaths)

        last_filepath = None
        tile_id_to_bytes = None

        j = 0
        start = time.time()
        for tile_id, filepath in tile_ids_and_filepaths:
            if filepath != last_filepath:
                last_filepath = filepath
                # filepath was glob'd well before this point (get_parent_to_filepaths
                # runs once, up front, but a single create_archive() call can take
                # well over an hour for a large parent) -- aggregation_run.py running
                # concurrently (D32's operating model) may have since reprocessed this
                # exact position and replaced the file under a new name (its filename
                # encodes maxzoom, which can change between reprocessings). A same-path
                # overwrite would survive; a rename cannot (mapterhorn-japan-bridge
                # DECISIONS.md D37, first observed there, still unfixed until now).
                # Swapping in the new file isn't safe here -- its content is decomposed
                # at a different zoom than the tile_ids already computed for the stale
                # filename in the prep loop above, so its tile IDs wouldn't match.
                # Skipping is: this parent's bundle is very slightly incomplete for
                # this one cycle, but bundle.py always does a full fresh pass
                # (dirty_only=False), so the next publish_cycle re-globs and includes
                # it correctly -- same self-healing shape as DOWNSAMPLING_STRICT's own
                # skip-and-retry-next-time pattern elsewhere in this pipeline.
                try:
                    tile_id_to_bytes = read_full_archive(filepath)
                except FileNotFoundError:
                    print(f'WARNING: {filepath} no longer exists -- likely overwritten '
                          f'by a concurrent aggregation_run.py reprocess mid-bundle. '
                          f'Skipping its tiles this cycle; will be picked up correctly '
                          f'on the next publish_cycle run.', flush=True)
                    tile_id_to_bytes = {}
            if tile_id in tile_id_to_bytes:
                writer.write_tile(tile_id, tile_id_to_bytes[tile_id])

            j += 1
            if j % 10_000 == 0:
                tic = time.time()
                time_so_far = tic - start
                expected_duration = time_so_far * len(tile_ids_and_filepaths) / j
                finishes_in = expected_duration - time_so_far
                print(f'Processed {j:_} / {len(tile_ids_and_filepaths):_} tiles in {int(time_so_far / 60)} min {int(time_so_far) % 60} s. Finishes in {int(finishes_in / 3600)} h {int(finishes_in / 60) % 60} min...')

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
                'attribution': '<a href="https://mapterhorn.com/attribution">© Mapterhorn</a>',
            },
        )
        checksum = hash_writer.md5.hexdigest()

    utils.create_folder('meta-store/bundle')
    filesize = os.path.getsize(out_filepath)
    with open(f'meta-store/bundle/{name}.json', 'w') as f:
        json.dump({
            'size': filesize,
            'md5sum': checksum,
            'min_lon': min_lon,
            'min_lat': min_lat,
            'max_lon': max_lon,
            'max_lat': max_lat,
            'min_zoom': min_z,
            'max_zoom': max_z,
        }, f, indent=2)

def get_name_from_parent(parent):
    name = None
    if parent == mercantile.Tile(x=0, y=0, z=0):
        name = 'planet'
    else:
        name = f'{parent.z}-{parent.x}-{parent.y}'
    # D107: keep the two datatypes' bundle-store output filenames distinct
    # so a lineage run's files never collide with (or get globbed alongside)
    # an elevation run's -- same directory, different names.
    if BUNDLE_DATATYPE == 'lineage':
        name = f'{name}-lineage'
    return name

def get_worker_count():
    """Get worker count with graceful defaults (same convention as
    AGGREGATION_WORKERS/DOWNSAMPLING_WORKERS)."""
    if 'BUNDLE_WORKERS' in os.environ:
        try:
            return int(os.environ['BUNDLE_WORKERS'])
        except ValueError:
            pass
    # Default: 4 workers (half of typical 8-core hardware, avoids saturating CPU/disk)
    return 4

def bundle_one(args):
    parent, filepaths = args
    name = get_name_from_parent(parent)
    print(name)
    create_archive(filepaths, name)
    return name

def main():
    num_aggregations = None
    if len(sys.argv) == 2:
        num_aggregations = int(sys.argv[1])
        print(f'bundling the last {num_aggregations} aggregation(s)...')
    else:
        print('Not enough arguments. Usage: bundle.py {{num_aggregations}}')
        exit()

    dirty_only = False  # Bundling all files (not just dirty) to include new downsampling tiles
    # Which generation's pmtiles-store subtree to bundle: default is the
    # active (latest) aggregation-store generation, overridable with
    # BUNDLE_GENERATION for deliberate work on an older one. Printed
    # loudly so a wrong-generation bundle is visible in the first log
    # line rather than discovered in the merged output.
    generation_id = os.environ.get('BUNDLE_GENERATION') or utils.get_aggregation_ids()[-1]
    print(f'datatype: {BUNDLE_DATATYPE} (set BUNDLE_DATATYPE to override)')
    print(f'generation: {generation_id} (set BUNDLE_GENERATION to override; default = latest aggregation-store id)')
    parent_to_filepaths = get_parent_to_filepaths(dirty_only, num_aggregations, datatype=BUNDLE_DATATYPE, generation_id=generation_id)

    # bundle_one() has no internal parallelism -- one region is one atomic,
    # single-threaded task (create_archive()'s writer needs tile-id-sorted
    # order, so the write side can't fan out across workers). With few
    # regions and a handful of workers, Pool.map's default chunksize groups
    # tasks in glob/dict-insertion order, not by size -- observed directly
    # on slate (mapterhorn-japan-bridge DECISIONS.md, bundle capacity
    # measurement): a 23-region run left one worker idle after finishing
    # several small regions while the other worker was still 64 minutes
    # into the one region holding 430,856 tiles, because that region
    # wasn't the first task handed out. Largest-first with chunksize=1
    # is classic longest-processing-time-first scheduling -- near-optimal
    # for minimizing makespan across identical workers, and cheap here
    # since len(filepaths) is already known before any work starts (a
    # source-file count, not the true tile count, but a reasonable proxy
    # without re-reading every archive just to sort them).
    parent_items = sorted(
        parent_to_filepaths.items(), key=lambda item: len(item[1]), reverse=True)

    # Each parent writes to its own bundle-store/{name}.pmtiles + meta-store/bundle/{name}.json --
    # fully independent, no shared state, so safe to fan out across processes (unlike the
    # sequential loop this replaces, which left slate's other 9 cores idle during a real run).
    worker_count = get_worker_count()
    print(f'using {worker_count} workers (set BUNDLE_WORKERS to override)')
    with Pool(processes=worker_count) as pool:
        pool.map(bundle_one, parent_items, chunksize=1)

    print(f'The following {len(parent_to_filepaths.keys())} file(s) were created:')
    for parent in parent_to_filepaths.keys():
        print(f'{get_name_from_parent(parent)}.pmtiles')

if __name__ == '__main__':
    main()
