"""Majority-vote downsampling for categorical (lineage/provenance) rasters.

Prep work for Generation 2 (mapterhorn-japan-bridge DECISIONS.md D93/D94) --
NOT wired into any production pipeline yet. downsampling_run.py's own
create_tile() builds a coarser-zoom parent tile by alpha-weighted-averaging
each 2x2 block of child pixels (see its own long comment on why: it matches
upstream mapterhorn/mapterhorn's real-elevation-then-re-encode approach).
That's correct for a continuous quantity like elevation, where the average
of four numbers is a meaningful number. It's meaningless for a categorical
label (e.g. "which source tier filled this pixel" -- lineage_inspect.py's
own GLOBAL_TIER 0..6): averaging tier indices 0 and 6 does not mean "tier 3
filled this pixel," it means nothing. The correct downsampling operation for
a categorical raster is the per-cell mode (majority vote) of its four
children, not their mean.

Design notes:
  - Tie-breaking: with 4 children and >2 candidate categories, an exact tie
    (e.g. 2-2) is possible. Ties are broken toward the *lower* category
    index. This is deliberate, not arbitrary: lineage_inspect.py's
    GLOBAL_TIER already orders categories from highest-priority source
    (0 = 1m DEM1A) to lowest (6 = sea/GLO-30) -- exactly the same priority
    order the original per-pixel merge (aggregation_merge.py) itself uses
    to decide which source wins a contested pixel. Breaking ties toward
    the lower index means "on a tie, prefer whichever source aggregation_
    merge.py itself would have preferred" -- consistent with the rest of
    the pipeline's own priority semantics, not a new, unrelated rule.
  - Implementation: one-hot vote counting (a count array per category,
    summed over the 2x2 block) plus np.argmax, rather than scipy.stats.mode
    -- avoids a new dependency, avoids scipy.stats.mode's own version-
    dependent keepdims/tie-breaking behavior changes, and is fully
    vectorized (no per-pixel Python loop) at the cost of one pass per
    category (cheap: num_categories is 8, not a large number).
  - Nodata handling: alpha=0 children are excluded from the vote. A parent
    cell with zero valid children is nodata; with 1-3 valid children, the
    vote is taken over just the valid ones (no artificial down-weighting
    for having fewer votes -- same spirit as downsampling_run.py's own
    weight_sum-based nodata handling for elevation).

Usage (once wired into a real pipeline): call majority_vote_downsample()
where downsampling_run.py's create_tile() currently does its alpha-weighted
average, behind a data-type switch (e.g. an EMIT_LINEAGE-style flag per
D93), on the lineage raster's own 1024x1024 category+alpha block instead of
the elevation RGBA block.
"""
import numpy as np

# 0..6 = GLOBAL_TIER from lineage_inspect.py (0 = highest priority source,
# 6 = lowest / sea fallback); NODATA is its own sentinel, never a vote
# candidate.
NODATA = -1
NUM_CATEGORIES = 7


def majority_vote_downsample(values, alpha, num_categories=NUM_CATEGORIES):
    """Downsample one 1024x1024 categorical block to 512x512 by per-cell
    majority vote over each 2x2 child block.

    Args:
      values: (1024, 1024) int array, category index (0..num_categories-1)
        per pixel. Value at nodata pixels is irrelevant (alpha gates it).
      alpha: (1024, 1024) array, >0 where the pixel is valid data, 0 where
        it's nodata (same convention downsampling_run.py's create_tile()
        already uses for the elevation path).
      num_categories: number of distinct category values in `values`.

    Returns:
      (parent_values, parent_alpha): both (512, 512). parent_values is
      NODATA where no child in that 2x2 block was valid.
    """
    if values.shape != (1024, 1024) or alpha.shape != (1024, 1024):
        raise ValueError(f'expected (1024, 1024) inputs, got values={values.shape} alpha={alpha.shape}')

    v_blocks = values.reshape(512, 2, 512, 2)
    a_blocks = alpha.reshape(512, 2, 512, 2)
    valid = a_blocks > 0

    # counts[..., cat] = how many of the (up to 4) valid children in this
    # cell's 2x2 block voted for `cat`. One pass per category rather than
    # scipy.stats.mode -- see module docstring.
    counts = np.zeros((512, 512, num_categories), dtype=np.uint8)
    for cat in range(num_categories):
        counts[..., cat] = ((v_blocks == cat) & valid).sum(axis=(1, 3))

    any_valid = valid.any(axis=(1, 3))
    # argmax over the category axis returns the FIRST (lowest-index) max on
    # a tie -- this is what makes the tie-break-toward-higher-priority
    # behavior work without any extra code; it falls out of argmax's own
    # documented tie-breaking rule plus GLOBAL_TIER's existing ordering.
    winning_category = np.argmax(counts, axis=-1)

    parent_values = np.where(any_valid, winning_category, NODATA).astype(np.int8)
    parent_alpha = np.where(any_valid, 255, 0).astype(np.uint8)
    return parent_values, parent_alpha


def _self_test():
    """Synthetic correctness check -- not a pytest suite (this repo has
    none), just a runnable proof the core logic is right before anyone
    wires it into a real pipeline. Run directly: `python3 lineage_downsample.py`."""
    values = np.full((1024, 1024), NODATA, dtype=np.int8)
    alpha = np.zeros((1024, 1024), dtype=np.uint8)

    # Cell (0,0)'s 2x2 block: three children vote category 2, one votes
    # category 5 -> category 2 should win outright (no tie).
    values[0, 0] = 2
    values[0, 1] = 2
    values[1, 0] = 2
    values[1, 1] = 5
    alpha[0:2, 0:2] = 255

    # Cell (0,1)'s 2x2 block (columns 2:4): a clean 2-2 tie between
    # category 4 and category 1 -> lower index (1) must win.
    values[0, 2] = 4
    values[0, 3] = 1
    values[1, 2] = 4
    values[1, 3] = 1
    alpha[0:2, 2:4] = 255

    # Cell (0,2)'s 2x2 block (columns 4:6): only 1 of 4 children is valid
    # (the rest are nodata) -> that lone valid vote must still win, not be
    # discarded for lack of quorum.
    values[0, 4] = 3
    alpha[0, 4] = 255
    # columns 5, and row 1 cols 4:6 stay nodata (alpha already 0)

    # Cell (0,3)'s 2x2 block (columns 6:8): zero valid children -> NODATA.
    # (already the default: alpha 0, values NODATA)

    parent_values, parent_alpha = majority_vote_downsample(values, alpha)

    assert parent_values[0, 0] == 2, f'expected 2, got {parent_values[0, 0]}'
    assert parent_alpha[0, 0] == 255
    assert parent_values[0, 1] == 1, f'tie-break: expected 1 (lower index), got {parent_values[0, 1]}'
    assert parent_values[0, 2] == 3, f'single valid vote: expected 3, got {parent_values[0, 2]}'
    assert parent_alpha[0, 2] == 255
    assert parent_values[0, 3] == NODATA, f'no valid children: expected NODATA, got {parent_values[0, 3]}'
    assert parent_alpha[0, 3] == 0

    print('lineage_downsample._self_test: all assertions passed')


if __name__ == '__main__':
    _self_test()
