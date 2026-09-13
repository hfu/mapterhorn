from glob import glob
import os
import shutil

import mercantile
from ulid import ULID

import utils

REQUIRED_DATATYPES = utils.get_required_datatypes()

def get_mercator_resolutions(minzoom, maxzoom):
    resolutions = []
    for z in range(minzoom, maxzoom + 1):
        tile = mercantile.Tile(x=0, y=0, z=z)
        bounds = mercantile.xy_bounds(tile)
        resolutions.append((bounds.right - bounds.left) / 512)
    return resolutions

def bounds_intersect_no_anitmeridian_crossing(a, b):
    left_a, bottom_a, right_a, top_a = a
    left_b, bottom_b, right_b, top_b = b
    dont_intersect = False
    dont_intersect |= right_a <= left_b
    dont_intersect |= right_b <= left_a
    dont_intersect |= top_a <= bottom_b
    dont_intersect |= top_b <= bottom_a
    return not dont_intersect

def split_at_antimeridian(bbox):
    left, bottom, right, top = bbox
    if left < right:
        return [bbox]
    bbox_1 = (left, bottom, utils.X_MAX_3857, top)
    bbox_2 = (utils.X_MIN_3857, bottom, right, top)
    return [bbox_1, bbox_2]

def bounds_intersect(a, b):
    for aa in split_at_antimeridian(a):
        for bb in split_at_antimeridian(b):
            if bounds_intersect_no_anitmeridian_crossing(aa, bb):
                return True
    return False

def get_intersecting_tiles_dfs(bounds, tile, zoom):
    tile_bounds = mercantile.xy_bounds(tile)
    if not bounds_intersect(bounds, tile_bounds):
        return []
    if tile.z == zoom:
        return [tile]
    result = []
    for child in mercantile.children(tile, zoom=tile.z + 1):
        result += get_intersecting_tiles_dfs(bounds, child, zoom)
    return result

def get_macrotile_map():
    macrotile_map = {}
    filepaths = sorted(glob('source-store/*/bounds.csv'))
    mercator_resolutions = get_mercator_resolutions(0, 32)
    for filepath in filepaths:
        print(f'reading {filepath}...')
        source = filepath.split('/')[1]
        with open(filepath) as f:
            f.readline() # skip header
            line = f.readline().strip()
            while line != '':
                filename, left, bottom, right, top, width, height = line.split(',')
                width, height = [int(a) for a in [width, height]]
                left, bottom, right, top = [float(a) for a in [left, bottom, right, top]]

                multiplier = 2
                buffer = multiplier * utils.macrotile_buffer_3857
                buffered_bounds = (
                    left - buffer,
                    bottom - buffer,
                    right + buffer,
                    top + buffer
                )

                tiles = get_intersecting_tiles_dfs(buffered_bounds, mercantile.Tile(x=0, y=0, z=0), utils.macrotile_z)
                
                maxzoom = get_smallest_overzoom(left, bottom, right, top, width, height, mercator_resolutions)

                # Use at least a maxzoom of 12 (macrotile_z).
                # Note that glo30 does not everywhere give a maxzoom of 12. Examples:
                # S79 native maxzoom 9
                # N66 native maxzoom 10
                # N50 native maxzoom 11
                # N49 native maxzoom 12 (group has only ~30 percent of total macrotiles)
                # Use gdal warp with cubicspline when maxzoom is 12
                maxzoom = max(maxzoom, utils.macrotile_z)

                for tile in tiles:
                    if (tile.x, tile.y) not in macrotile_map:
                        macrotile_map[(tile.x, tile.y)] = {'sources': {}}
                    if source not in macrotile_map[(tile.x, tile.y)]['sources']:
                        macrotile_map[(tile.x, tile.y)]['sources'][source] = []
                    macrotile_map[(tile.x, tile.y)]['sources'][source].append({
                        'filename': filename,
                        'maxzoom': maxzoom,
                    })
                line = f.readline().strip()

    return macrotile_map

def get_smallest_overzoom(left, bottom, right, top, width, height, mercator_resolutions):
    horizontal_resolution = (right - left) / width if left < right else (left - right) / width
    vertical_resolution = (top - bottom) / height

    for z in range(len(mercator_resolutions)):
        if mercator_resolutions[z] < horizontal_resolution and mercator_resolutions[z] < vertical_resolution:
            return z
    raise ValueError(f'No overzoom found. (left, bottom, right, top, width, height) = {(left, bottom, right, top, width, height)}')

def add_group_ids(macrotile_map):
    for tile_tuple in macrotile_map:
        group_id_parts = set({})
        for source in macrotile_map[tile_tuple]['sources']:
            for source_item in macrotile_map[tile_tuple]['sources'][source]:
                group_id_parts.add((source, source_item['maxzoom']))
        group_id = tuple(sorted(list(group_id_parts)))
        macrotile_map[tile_tuple]['group_id'] = group_id

def get_aggregation_tiles_dfs(candidate, macrotile_map):
    if candidate.z == utils.macrotile_z:
        return [candidate]
    macrotiles = list(mercantile.children(candidate, zoom=utils.macrotile_z))
    group_ids = set({})
    for macrotile in macrotiles:
        tile_tuple = (macrotile.x, macrotile.y)
        if tile_tuple in macrotile_map:
            group_ids.add(macrotile_map[tile_tuple]['group_id'])
    if len(group_ids) == 0:
        return []
    if len(group_ids) == 1:
        group_id = list(group_ids)[0]
        maxzoom = 0
        for part in group_id:
            maxzoom = max(maxzoom, part[1])
        if candidate.z >= maxzoom - utils.num_overviews:
            return [candidate]
    result = []
    for child in mercantile.children(candidate, zoom=candidate.z + 1):
        result += get_aggregation_tiles_dfs(child, macrotile_map)
    return result

def get_aggregation_tiles(macrotile_map):
    candidates = set({})
    for tile_tuple in macrotile_map.keys():
        candidates.add(mercantile.parent(mercantile.Tile(x=tile_tuple[0], y=tile_tuple[1], z=utils.macrotile_z), zoom=utils.macrotile_z - utils.num_overviews))
    aggregation_tiles = []
    for candidate in candidates:
        aggregation_tiles += get_aggregation_tiles_dfs(candidate, macrotile_map)
    return aggregation_tiles

def write_aggregation_items(macrotile_map, aggregation_tiles, aggregation_id):
    folder = f'aggregation-store/{aggregation_id}'
    utils.create_folder(folder)
    for aggregation_tile in aggregation_tiles:
        macrotiles = list(mercantile.children(aggregation_tile, zoom=utils.macrotile_z))
        lines = ['source,filename,maxzoom\n']
        line_tuples = set({})
        child_z = 0
        for macrotile in macrotiles:
            tile_tuple = (macrotile.x, macrotile.y)
            if tile_tuple not in macrotile_map:
                continue
            for source in macrotile_map[tile_tuple]['sources']:
                for source_item in macrotile_map[tile_tuple]['sources'][source]:
                    line_tuples.add((
                        source, 
                        source_item['filename'], 
                        str(source_item['maxzoom']
                    )))
                    child_z = max(child_z, source_item['maxzoom'])
        if len(line_tuples) == 0:
            continue
        line_tuples = sorted(list(line_tuples))
        for line_tuple in line_tuples:
            lines.append(f'{",".join(line_tuple)}\n')
        with open(f'{folder}/{aggregation_tile.z}-{aggregation_tile.x}-{aggregation_tile.y}-{child_z}-aggregation.csv', 'w') as f:
            f.writelines(lines)

def try_reuse_from_previous_generation(filepath, filename, current_generation_id, last_generation_id):
    """DECISIONS1.md D163: the safe redesign of the dirty-tracking D57
    ripped out. Returns True (and, on success, materializes a real
    output file plus a real .done manifest inside the CURRENT
    generation's own folder) only when ALL of the following hold:

    1. An equivalent item exists in the previous generation, with a
       REAL (not legacy/empty) .done manifest that certifies every
       datatype this run needs.
    2. The previous generation's own pmtiles-store output file actually
       exists on disk, for every required datatype -- D57's own explicit
       "verify the referenced output actually exists" requirement, never
       just trust the marker. Checked before step 3 below since it's
       cheap (just os.path.isfile) and lets an item whose old output was
       since pruned/moved fail fast, before paying for a fingerprint.
    3. Today's fingerprint of this item's inputs -- the covering CSV's
       own content AND every referenced source file's own MD5 (D163;
       see utils.md5_input_entries_for_aggregation_csv's own docstring
       for why MD5, not just the CSV text, is required) -- exactly
       matches what that manifest recorded when it was built.

    Any single failure falls through to full reprocessing (write a
    .todo, the current safe default) -- never a bare skip. This is the
    load-bearing difference from the pre-D57 code: that version only
    checked step 3 (and an unreliable version of it, comparing against
    whatever the second-to-last generation happened to be, not
    verifying it was ever complete) and then SKIPPED the item entirely,
    leaving pmtiles-store's cross-generation flat namespace as the only
    thing standing between "unchanged" and "silently never built" --
    exactly what let 2,343 positions vanish. This version never skips:
    on a match, it COPIES the previous generation's own file and writes
    a brand-new manifest scoped to the CURRENT generation_id, so the
    current generation ends up with its own real artifact, exactly as
    if aggregation_run.py had built it fresh. Since D95/D124 made
    pmtiles-store generation_id-scoped, no other generation's future run
    can ever rename or delete this copy out from under it -- closing
    D69's stale-marker failure mode too, not just D57's undercount.

    D164: this function's own caller (write_aggregation_todos()) wraps
    every call in a try/except -- any exception here (a malformed
    manifest, a source-catalog manifest that vanished mid-run, a
    shutil.copy2 I/O error) falls through to writing a .todo for this
    one item rather than crashing the whole covering pass, so one bad
    item can never silently strand every item after it in the sorted
    glob without a .todo OR a .done.

    D165/D166 CORRECTION to a claim D164's own docstring used to make
    here: it used to say a wrong guessed output path under upsampling
    "simply fails the os.path.isfile() check below ... never a silent
    wrong reuse." That reasoning only covers the case where the
    PREVIOUS generation was upsampled and the current one is not. The
    actual 1.6-go direction is the opposite -- the CURRENT generation
    upsamples land items, the previous one (1.5-go) did not -- and in
    that direction the guessed path (parsed from filename, i.e. the
    PLANNED/native child_z, which is what 1.5-go's own real output
    actually used) resolves correctly, `os.path.isfile()` succeeds, and
    every other check below (content + MD5 fingerprint) still matches
    (upsampling changes NONE of the covering CSV's own fields). Without
    the explicit leaf_child_z comparison added below, this would
    silently copy 1.5-go's non-upsampled archive into 1.6-go under the
    same (wrong, non-upsampled) filename and mark it done -- a real,
    verified hazard (an Opus design review found ~93% of 1.6-go's own
    target items sit at the macrotile_z floor, where even D149's own
    granularity change can't make their filename differ from 1.5-go's),
    not a hypothetical one.
    """
    if os.environ.get('DISABLE_AGGREGATION_REUSE', '0') == '1':
        return False

    last_filepath = f'aggregation-store/{last_generation_id}/{filename}'
    last_done_path = f'{last_filepath}.done'
    if not os.path.isfile(last_filepath) or not os.path.isfile(last_done_path):
        return False

    # D164: done_covers() treats a legacy/empty manifest ({} -- pre-D119
    # touch files, or any unparseable JSON) as "covers elevation" without
    # ever comparing a fingerprint (see its own docstring: "Legacy empty
    # markers ... stay 'current' for elevation, deliberate: never churn
    # 1-go"). That bypass is exactly the D18/D35 gap this whole mechanism
    # exists to close -- reachable here if a future generation's
    # immediate predecessor ever has a legacy/corrupt manifest (not true
    # for 1.5-go, which this session backfilled with real fingerprints,
    # but nothing structurally prevents it for some future generation
    # pair). Require a real, fingerprint-bearing manifest before trusting
    # anything it says -- and keep the manifest itself, since D165/D166
    # needs to read its own recorded leaf_child_z below, not just check
    # truthiness.
    last_manifest = utils.read_done_manifest(last_done_path)
    if not last_manifest:
        return False

    z, x, y, _planned_child_z = [int(a) for a in filename.replace('-aggregation.csv', '').split('-')]

    # D165/D166: the actual fix this function's own docstring describes
    # -- reject reuse outright if the CURRENT generation's own target
    # child_z for this position (utils.leaf_child_z(), which is 16 for a
    # land item in a generation that upsamples, native otherwise) differs
    # from what the PREVIOUS generation's manifest recorded actually
    # producing. Cheap (two dict lookups once each generation's covering
    # has been scanned), so checked before done_covers()/the per-file MD5
    # fingerprint below. For every existing (non-upsampling) generation
    # pair this is always a match, since leaf_child_z() returns the
    # covering filename's own native value for both sides -- byte-
    # identical behavior to before this check existed.
    current_child_z = utils.leaf_child_z(current_generation_id, z, x, y)
    if last_manifest.get('leaf_child_z') != current_child_z:
        return False

    if not utils.done_covers(last_done_path, REQUIRED_DATATYPES):
        return False

    last_out_paths = {}
    for datatype in REQUIRED_DATATYPES:
        last_out_folder = utils.get_pmtiles_folder(x, y, z, layer='aggregation', datatype=datatype, generation_id=last_generation_id)
        last_out_path = f'{last_out_folder}/{z}-{x}-{y}-{current_child_z}.pmtiles'
        if not os.path.isfile(last_out_path):
            return False
        last_out_paths[datatype] = last_out_path

    # Only now (after every cheap check above has passed) pay for the
    # per-referenced-file MD5 fingerprint.
    current_entries = utils.aggregation_fingerprint_entries(filepath, filename)
    if not utils.done_is_current(last_done_path, REQUIRED_DATATYPES, current_entries):
        return False

    for datatype, last_out_path in last_out_paths.items():
        current_out_folder = utils.get_pmtiles_folder(x, y, z, layer='aggregation', datatype=datatype, generation_id=current_generation_id)
        utils.create_folder(current_out_folder)
        shutil.copy2(last_out_path, f'{current_out_folder}/{z}-{x}-{y}-{current_child_z}.pmtiles')

    utils.write_done_manifest(
        f'{filepath}.done',
        datatypes=REQUIRED_DATATYPES,
        generation_id=current_generation_id,
        entries=current_entries,
        extra={'reused_from_generation_id': last_generation_id, 'leaf_child_z': current_child_z},
    )
    return True

def write_aggregation_todos(aggregation_id=None):
    """D165 (Opus code review, 2026-09-13): `aggregation_id` defaults to
    None, re-deriving `aggregation_ids[-1]` (newest ULID on disk) for
    standalone/backward-compatible invocation -- but main() now passes
    its OWN resolved aggregation_id explicitly (see main()'s own
    AGGREGATION_ID override) rather than letting this function silently
    re-derive a possibly-different one. Before this, running
    `AGGREGATION_ID=<a non-latest generation>` to re-plan a specific
    generation would write fresh coverings into that generation's own
    folder via write_aggregation_items(), but every .todo/.done/reuse-
    copy from this function would land against whatever generation
    happened to be lexicographically newest instead -- a silent no-op
    for the generation actually being re-planned, and unrelated churn
    for the wrong one."""
    # DECISIONS.md D51/D57: this used to compare the current generation's
    # own aggregation.csv content against aggregation_ids[-2] (the old
    # Kyushu-scope test generation) via get_dirty_aggregation_filenames(),
    # and skip writing a .todo for any item judged "unchanged" -- on the
    # assumption that unchanged content means the position is already
    # correctly built. That assumption is false whenever the *older*
    # generation itself never finished building that position: pmtiles-
    # store is flat (not generation-scoped), so "unchanged since Kyushu"
    # silently inherited every one of Kyushu's own incomplete positions
    # forward, forever, into every later generation -- this is the same
    # dirty-filter-against-an-unrelated-baseline pattern D51 already
    # found and removed from downsampling_run.py, just with a more
    # consequential blast radius here: quantified on the real 1号
    # generation, 4,394 of 6,373 native aggregation items (69%) never
    # got a .todo at all, and 2,343 of those have zero pmtiles-store
    # output -- meaning aggregation_run_national's own "1,979/1,979 done"
    # (D48) was 100% of an undercounted denominator, not 100% of the
    # true national total.
    #
    # D163: dirty-tracking is back, redesigned to be safe --
    # try_reuse_from_previous_generation() above verifies actual output
    # existence and a real content+MD5 fingerprint match before ever
    # treating an item as already built; anything short of that gets a
    # .todo like before. aggregation_run.py's own run() still checks
    # `.done` before doing any real work, so a redundant .todo for an
    # already-`.done` item (reused or genuinely rebuilt) still costs
    # nothing beyond a fast no-op skip -- that idempotency guard is
    # unchanged.
    aggregation_ids = utils.get_aggregation_ids()
    if aggregation_id is None:
        aggregation_id = aggregation_ids[-1]
    # last_aggregation_id is always "whichever generation immediately
    # precedes aggregation_id in on-disk ULID order" -- correct whether
    # aggregation_id is the newest (the common case) or an explicit
    # override, as long as the override is itself a real, already-
    # existing generation directory (true for any re-plan of a
    # generation write_aggregation_items() just wrote coverings into).
    older_ids = [i for i in aggregation_ids if i < aggregation_id]
    last_aggregation_id = older_ids[-1] if older_ids else None

    filepaths = sorted(glob(f'aggregation-store/{aggregation_id}/*-aggregation.csv'))
    reused_count = 0
    for filepath in filepaths:
        filename = filepath.split('/')[-1]
        reused = False
        if last_aggregation_id:
            # D164: any exception inside the reuse attempt (a malformed
            # manifest, a source-catalog manifest that vanished mid-run,
            # a shutil.copy2 I/O error) falls through to a .todo for
            # just this one item instead of crashing write_aggregation_
            # todos() entirely -- without this, every item later in the
            # sorted glob than the one that raised would end up with
            # neither a .todo nor a .done, silently reproducing the
            # "item never gets processed" failure class D57 exists to
            # prevent, just via an unhandled exception instead of a bad
            # dirty-filter.
            try:
                reused = try_reuse_from_previous_generation(filepath, filename, aggregation_id, last_aggregation_id)
            except Exception as e:
                print(f'WARNING: reuse check for {filename} raised {e!r} -- queuing full reprocessing instead')
                reused = False
        if reused:
            reused_count += 1
        else:
            with open(f'{filepath}.todo', 'w') as f:
                f.write('')
    print(f'aggregation todos: {reused_count}/{len(filepaths)} reused from generation {last_aggregation_id}, {len(filepaths) - reused_count} queued for (re)processing')

def main():

    print('get_macrotile_map...')
    macrotile_map = get_macrotile_map()

    print('add group ids...')
    add_group_ids(macrotile_map)

    print('get aggregation tiles...')
    aggregation_tiles = get_aggregation_tiles(macrotile_map)

    # AGGREGATION_ID override (2026-09-04): lets a generation's ULID be
    # minted AHEAD of the covering run and recorded in PLAN.md section
    # 0's generation table first -- so the id in the table is guaranteed
    # to be the id the run actually uses (1.5号's id was pre-minted this
    # way). Without the override, behavior is unchanged: a fresh ULID.
    aggregation_id = os.environ.get('AGGREGATION_ID') or str(ULID())
    print(f'aggregation (generation) id: {aggregation_id}')
    utils.create_folder(f'aggregation-store/{aggregation_id}')

    print('write aggregation items...')
    write_aggregation_items(macrotile_map, aggregation_tiles, aggregation_id)

    print('write aggregation todos...')
    write_aggregation_todos(aggregation_id)


if __name__ == '__main__':
    main()
