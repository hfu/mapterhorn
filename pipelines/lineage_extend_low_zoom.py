"""One-off extension of the published lineage downsampling pyramid below its
normal z8 floor (Hidenori's 2026-09-06 request, mapterhorn-japan-bridge
DECISIONS.md D146): elevation's z8 floor exists specifically so Mapterhorn's
own global z0-7 overview can be spliced in wholesale (see
downsampling_covering.py's write_downsampling_items() comment) -- a rationale
that doesn't apply to lineage, which has no external global counterpart to
splice. This script continues lineage's OWN majority-vote pyramid further
levels down (z8->7->6->...) so nationwide provenance patterns become visible
at low zoom, without touching elevation's covering/run scripts or any
elevation data.

Deliberately standalone, not merged into downsampling_covering.py /
downsampling_run.py: those two files iterate top-down from the finest
aggregation leaf over the *whole* national pyramid via a datatype-agnostic
covering-CSV format shared by both datatypes. Naively lowering
min_output_zoom there would also pull elevation into building (unwanted,
wasteful, and never-published) z4-z7 overviews of its own -- exactly the
kind of shared-code hazard this project's own D74-D76/D95 already learned
from. This script only reads existing, already-published z8 lineage
pmtiles-store output and writes new low-zoom archives using the SAME
file-naming convention get_pmtiles_folder() already uses for z<7 (a flat
bucket, `{extent}-{output_zoom}.pmtiles`), so bundle.py's existing glob
(pmtiles-store/downsampling/{datatype}/{generation_id}/*.pmtiles) picks them
up with no code changes there either. All extents use a single z0/x0/y0
box throughout -- Japan's total tile count at z4-z7 is tiny, so the
multi-extent grouping machinery downsampling_covering.py needs for national
elevation scale buys nothing here.
"""
import glob
import io
import os
import shutil

import imagecodecs
import mercantile
import numpy as np
from PIL import Image

import lineage_downsample
import utils
import downsampling_run  # reuse get_tile_to_pmtiles_filename, get_cached_reader

GENERATION_ID = utils.get_aggregation_ids()[-1]
DATATYPE = 'lineage'
LAYER = 'downsampling'
TARGET_ZOOM = int(os.environ.get('LINEAGE_EXTEND_TARGET_ZOOM', 4))
SOURCE_ZOOM = int(os.environ.get('LINEAGE_EXTEND_SOURCE_ZOOM', 8))

FOLDER = utils.get_pmtiles_folder(0, 0, 0, layer=LAYER, datatype=DATATYPE, generation_id=GENERATION_ID)

# Regression guard, not a data filter: exists purely to catch the specific
# "whole globe" bug this script hit during 1.5-go development
# (get_tile_to_pmtiles_filename() re-deriving a self-produced archive's
# coverage from its own placeholder 0-0-0 extent tile, which
# mercantile.children() then expands to literally the entire planet at the
# next zoom down -- 16,384 tiles at z7 alone). A future generation (2号
# onward) hitting this same class of bug would otherwise silently bloat the
# archive with thousands of nodata tiles, exactly as happened here before it
# was caught by manual inspection.
#
# A geographic bounding-box version of this check was tried first and
# dropped: real published z8 lineage data legitimately includes tiles
# reaching ~49 deg N (e.g. Tile(x=216, y=88, z=8), a jpnationalsea/GLO-30
# coverage tile north of Hokkaido) -- a bbox loose enough to admit that
# without flagging it would no longer meaningfully distinguish real data
# from the bug. A pure tile-count threshold is more robust: the bug
# produces order-of-magnitude blowups (16,384 vs. this generation's real
# ~272 at z8), so a generous ceiling still catches it without needing to
# know 2号's exact real geographic coverage in advance.
SANE_TILE_COUNT_CEILING = 5000


def assert_tiles_sane(tiles, label):
    if len(tiles) > SANE_TILE_COUNT_CEILING:
        raise RuntimeError(
            f'{label}: {len(tiles)} tiles is implausibly many for Japan-only '
            f'lineage data at this zoom -- likely the whole-globe bug (see '
            f'this module\'s docstring / SANE_TILE_COUNT_CEILING comment) has '
            f'recurred.')


def read_child_rgba(reader, child):
    child_bytes = reader.get(child.z, child.x, child.y)
    if not child_bytes:
        return None
    img = np.array(Image.open(io.BytesIO(child_bytes)))
    if img.ndim == 2:
        img = np.stack([img, img, img, np.ones_like(img) * 255], axis=2)
    elif img.shape[2] == 3:
        alpha = np.ones((img.shape[0], img.shape[1], 1)) * 255
        img = np.dstack([img, alpha])
    return img


def build_level(source_output_zoom, source_tiles=None):
    """source_tiles: the exact set of mercantile.Tile positions (at zoom=
    source_output_zoom) that genuinely hold data, if already known from the
    previous iteration. None means "discover from real upstream archives"
    (only valid for the first call, reading the already-published z8
    layer's own multi-extent archives).

    Passing the real tile set explicitly (rather than re-deriving it via
    get_tile_to_pmtiles_filename() on this script's own single-extent
    output file) matters: that function infers an archive's coverage from
    mercantile.children() of the filename's *own* extent tile, assuming
    the archive is dense over that whole descendant set. The upstream z8
    archives satisfy that (each really does cover its whole z5/z6 extent
    box). This script's own output, written under one fixed 0-0-0 extent
    tile purely as a filename-convention placeholder, does NOT -- it holds
    only ~68 real Japan tiles, not the ~16384 tiles z0/0/0 actually spans
    at z7. Re-deriving from the placeholder filename silently manufactured
    thousands of bogus whole-globe nodata tiles on the first version of
    this script (caught by inspecting the z6 output's own tile count)."""
    target_zoom = source_output_zoom - 1
    print(f'building z{target_zoom} from z{source_output_zoom}...')

    if source_tiles is None:
        pattern = f'{FOLDER}/*-{source_output_zoom}.pmtiles'
        filenames = sorted(os.path.basename(f) for f in glob.glob(pattern))
        if not filenames:
            raise RuntimeError(f'no input files matched {pattern}')
        print(f'  {len(filenames)} source archive(s): {filenames[:5]}{"..." if len(filenames) > 5 else ""}')
        tile_to_filename = downsampling_run.get_tile_to_pmtiles_filename(filenames)
        source_tiles = [t for t in tile_to_filename if t.z == source_output_zoom]
        assert_tiles_sane(source_tiles, f'source tiles at z{source_output_zoom}')
    else:
        # This script's own prior-level output: one archive, named for the
        # placeholder 0-0-0 extent, actually holding exactly source_tiles.
        filename = f'0-0-0-{source_output_zoom}.pmtiles'
        tile_to_filename = {t: filename for t in source_tiles}
        print(f'  1 source archive (self-produced): {filename}')

    parents = sorted(set(mercantile.parent(t, zoom=target_zoom) for t in source_tiles))
    assert_tiles_sane(parents, f'parent tiles at z{target_zoom}')
    print(f'  {len(source_tiles)} source tiles -> {len(parents)} parent tile(s) at z{target_zoom}')

    tmp_folder = f'tmp-store/lineage-extend-{target_zoom}'
    utils.create_folder(tmp_folder)

    for parent in parents:
        full_values = np.full((1024, 1024), lineage_downsample.NODATA, dtype=np.int64)
        full_alpha = np.zeros((1024, 1024), dtype=np.float32)
        for row_offset in range(2):
            for col_offset in range(2):
                child = mercantile.Tile(x=2 * parent.x + col_offset, y=2 * parent.y + row_offset, z=parent.z + 1)
                if child not in tile_to_filename:
                    continue
                filepath = f'{FOLDER}/{tile_to_filename[child]}'
                reader = downsampling_run.get_cached_reader(filepath)
                child_rgba = read_child_rgba(reader, child)
                if child_rgba is None:
                    continue
                row_start, row_end = 512 * row_offset, 512 * (row_offset + 1)
                col_start, col_end = 512 * col_offset, 512 * (col_offset + 1)
                full_values[row_start:row_end, col_start:col_end] = child_rgba[..., 0].astype(np.int64)
                full_alpha[row_start:row_end, col_start:col_end] = child_rgba[..., 3].astype(np.float32)

        parent_values, parent_alpha = lineage_downsample.majority_vote_downsample(full_values, full_alpha)
        parent_category = np.where(
            parent_values == lineage_downsample.NODATA, 255, parent_values
        ).astype(np.uint8)
        parent_rgba = np.zeros((512, 512, 4), dtype=np.uint8)
        parent_rgba[..., 0] = parent_category
        parent_rgba[..., 3] = parent_alpha
        parent_bytes = imagecodecs.webp_encode(parent_rgba, lossless=True)
        with open(f'{tmp_folder}/{parent.z}-{parent.x}-{parent.y}.webp', 'wb') as f:
            f.write(parent_bytes)

    out_filepath = f'{FOLDER}/0-0-0-{target_zoom}.pmtiles'
    utils.create_archive(tmp_folder, out_filepath)
    shutil.rmtree(tmp_folder)
    print(f'  wrote {out_filepath}')
    return target_zoom, parents


if __name__ == '__main__':
    print(f'generation_id={GENERATION_ID}, folder={FOLDER}')
    current_zoom = SOURCE_ZOOM
    current_tiles = None
    while current_zoom > TARGET_ZOOM:
        current_zoom, current_tiles = build_level(current_zoom, current_tiles)
    print('done.')
