from glob import glob

import mercantile

import utils

def get_extents_from_coverings(aggregation_id, zoom):
    extents = []
    filepaths = glob(f'aggregation-store/{aggregation_id}/*-*-*-{zoom}-*.csv')
    for filepath in filepaths:
        filename = filepath.split('/')[-1]
        parts = filename.replace('.csv', '').split('-')
        extent_z, extent_x, extent_y = [int(a) for a in parts[:3]]
        extents.append(mercantile.Tile(x=extent_x, y=extent_y, z=extent_z))
    return extents

def get_tile_to_extent_map(extents, zoom):
    tile_to_extent_map = {}
    for extent in extents:
        for child in mercantile.children(extent, zoom=zoom):
            tile_to_extent_map[child] = extent
    return tile_to_extent_map

def get_simplified_extents(extents, zoom):
    simplified_extents_unlimited = list(mercantile.simplify(extents))
    simplified_extents = []
    for unlimited in simplified_extents_unlimited:
        if unlimited.z == zoom:
            simplified_extents.append(mercantile.parent(unlimited, zoom=zoom - 1))
        elif unlimited.z >= zoom - utils.num_overviews:
            simplified_extents.append(unlimited)
        else:
            simplified_extents += list(mercantile.children(unlimited, zoom=zoom - utils.num_overviews))
    return simplified_extents

def tiles_intersect(a, b):
    if a == b:
        return True
    if a.z < b.z and mercantile.parent(b, zoom=a.z) == a:
        return True
    if b.z < a.z and mercantile.parent(a, zoom=b.z) == b:
        return True
    return False

def is_parent_of_dirty_aggregation_tile(tile, dirty_aggregation_tiles):
    for dirty_aggregation_tile in dirty_aggregation_tiles:
        if tiles_intersect(dirty_aggregation_tile, tile):
            return True
    return False

def not_in_previous_aggregation(filename, aggregation_ids):
    return len(glob(f'aggregation-store/{aggregation_ids[-2]}/{filename}')) == 0

def write_downlsampling_todos():
    print('writing downsampling todos...')
    aggregation_ids = utils.get_aggregation_ids()
    aggregation_id = aggregation_ids[-1]

    dirty_aggregation_tiles = []
    if len(aggregation_ids) >= 2:
        dirty_aggregation_filenames = utils.get_dirty_aggregation_filenames(aggregation_id, aggregation_ids[-2])
        for filename in dirty_aggregation_filenames:
            z, x, y, _ = [int(a) for a in filename.replace('-aggregation.csv', '').split('-')]
            dirty_aggregation_tiles.append(mercantile.Tile(x=x, y=y, z=z))

    for filepath in sorted(glob(f'aggregation-store/{aggregation_id}/*-downsampling.csv')):
        filename = filepath.split('/')[-1]
        z, x, y, _ = [int(a) for a in filename.replace('-downsampling.csv', '').split('-')]

        if len(aggregation_ids) < 2 or is_parent_of_dirty_aggregation_tile(mercantile.Tile(x=x, y=y, z=z), dirty_aggregation_tiles) or not_in_previous_aggregation(filename, aggregation_ids):
            with open(f'{filepath}.todo', 'w') as f:
                f.write('')
    
def write_downsampling_items():
    aggregation_ids = utils.get_aggregation_ids()
    aggregation_id = aggregation_ids[-1]

    command = f'rm aggregation-store/{aggregation_id}/*-downsampling.csv'
    utils.run_command(command)

    # min_output_zoom=8 (2026-08-30, Hidenori's design): our own aggregation
    # coverage has real, structural no-data gaps in deep-ocean areas far from
    # any Japan coastline -- harmless at z8+ (nobody navigates deep ocean at
    # that zoom) but visually obvious at the zoomed-out z0-7 views. Rather
    # than patch our own z0-7 pyramid, splice in tiles.mapterhorn.com's own
    # mature global product for z0-7 wholesale (via pmtiles merge, disjoint
    # archives since z7's max tile_id < z8's min tile_id) and stop our own
    # downsampling at z8. child_zoom=9 is the last iteration that still
    # produces a real output (parent_zoom = child_zoom - 1 = 8).
    min_output_zoom = 8
    for child_zoom in reversed(range(min_output_zoom + 1, 32)):
        print(f'\nchild_zoom={child_zoom}')
        print('get extents...')
        extents = get_extents_from_coverings(aggregation_id, child_zoom)

        if len(extents) == 0:
            continue

        print('get tile to extent map...')
        tile_to_extent_map = get_tile_to_extent_map(extents, child_zoom)

        print('get simplified extents...')
        simplified_extents = get_simplified_extents(extents, child_zoom)

        print('iterate over simplified extents...')
        for j, simplified_extent in enumerate(simplified_extents):
            if j % 100 == 0:
                print(f'{j} / {len(simplified_extents)}')
            involved_extents = set({})
            children = list(mercantile.children(simplified_extent, zoom=child_zoom))
            for child in children:
                if child in tile_to_extent_map:
                    involved_extents.add(tile_to_extent_map[child])
            lines = ['filename\n']
            for involved_extent in involved_extents:
                lines.append(f'{involved_extent.z}-{involved_extent.x}-{involved_extent.y}-{child_zoom}.pmtiles\n')
            
            out_filepath = f'aggregation-store/{aggregation_id}/{simplified_extent.z}-{simplified_extent.x}-{simplified_extent.y}-{child_zoom - 1}-downsampling.csv'
            with open(out_filepath, 'w') as f:
                f.writelines(lines)

if __name__ == '__main__':
    write_downsampling_items()
    # write_downlsampling_todos() deliberately not called: its own
    # `.todo` output (mapterhorn-japan-bridge DECISIONS.md D55) is never
    # read anywhere in this codebase (confirmed by grepping every .py
    # file for `.todo` -- only aggregation_run.py's own, unrelated
    # `*-aggregation.csv.todo` mechanism is real). Now that this script
    # runs every publish cycle (D55), skip the wasted I/O. The function
    # itself is left defined, not deleted, in case a future generation
    # (号2) ever wires a real consumer to it.
