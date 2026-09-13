from glob import glob
import os

import mercantile

import utils

def get_extents_from_coverings(aggregation_id, zoom):
    """Everything that "exists" at exactly `zoom`, from two sources:

    1. *-downsampling.csv coverings from a DEEPER pass of this same
       write_downsampling_items() run (the recursive part of the
       pyramid: this script's own output, written earlier in the same
       call, one zoom shallower each time). These filenames are
       entirely self-consistent by construction -- unaffected by
       upsampling -- so matching them by filename is still correct.
    2. Native aggregation LEAVES whose real, EFFECTIVE child_z (D165/
       D166's utils.leaf_child_z(), NOT the covering CSV filename's own
       native/planned child_z) equals `zoom`.

    D165/D166 (2026-09-13): the original single glob here
    (`*-*-*-{zoom}-*.csv`, matching both file kinds by filename alone)
    is exactly why 1.6-go's upsampled z14-z16 leaves were silently
    invisible to the whole downsampling pyramid -- their covering CSV
    stays named with the native (pre-upsample) child_z forever, by
    design (D163/D164's dirty-tracking and cross-generation reuse both
    need that filename to keep meaning "this recipe", not "this real
    output zoom"). An earlier draft of this fix ADDED a real-file scan
    alongside the untouched old glob rather than replacing the leaf
    half of it -- an Opus design review (D166) caught that this would
    have double-counted every upsampled leaf (once at its real zoom via
    the new scan, once at its stale native zoom via the old glob),
    producing a *-downsampling.csv referencing a *.pmtiles filename
    that will never exist. Splitting into these two explicit sources,
    with the leaf half computed from leaf_child_z() rather than a
    filename match, is what avoids that: each leaf contributes to
    exactly one zoom, its real one."""
    extents = []

    for filepath in glob(f'aggregation-store/{aggregation_id}/*-*-*-{zoom}-downsampling.csv'):
        filename = filepath.split('/')[-1]
        parts = filename.replace('-downsampling.csv', '').split('-')
        extent_z, extent_x, extent_y, _extent_child_z = [int(a) for a in parts]
        extents.append(mercantile.Tile(x=extent_x, y=extent_y, z=extent_z))

    for (leaf_z, leaf_x, leaf_y), effective_child_z in utils.get_leaf_child_z_map(aggregation_id).items():
        if effective_child_z == zoom:
            extents.append(mercantile.Tile(x=leaf_x, y=leaf_y, z=leaf_z))

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

    # Plain glob expansion, not `rm` via shell: on a fresh generation the
    # pattern matches nothing, which would make bare `rm` exit nonzero --
    # harmless before, but run_command() now raises on failure (D120
    # Fable #3), so do the idempotent-delete in-process instead.
    for stale_csv in glob(f'aggregation-store/{aggregation_id}/*-downsampling.csv'):
        os.remove(stale_csv)

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
