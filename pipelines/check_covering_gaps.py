"""Phase C1 (mapterhorn-japan-bridge DECISIONS.md D52/D56 plan): sample
downsampling items with missing references, and for each missing
reference determine whether it's a genuine no-source-coverage gap
(no downsampling.csv AND no native aggregation coverage anywhere
beneath that position) or something else worth investigating further.
Read-only, no side effects."""
from glob import glob
import os

import mercantile

import utils

AGG_ID = '01M0MWK852631SHCHPA66F21WQ'
AGG_DIR = f'aggregation-store/{AGG_ID}'
SAMPLE_SIZE = 20


def has_native_aggregation_beneath(tile, native_zooms=(12, 13, 14, 16)):
    """Any *-aggregation.csv at or below `tile` (descendant positions)
    at one of the known native resolutions?"""
    for z in native_zooms:
        if z < tile.z:
            continue
        if z == tile.z:
            candidates = [tile]
        else:
            candidates = mercantile.children(tile, zoom=z)
        for c in candidates:
            if os.path.isfile(f'{AGG_DIR}/{c.z}-{c.x}-{c.y}-{z}-aggregation.csv'):
                return True
    return False


def main():
    checked = 0
    genuine_gap = 0
    csv_missing_examples = []

    for filepath in sorted(glob(f'{AGG_DIR}/*-downsampling.csv')):
        if checked >= SAMPLE_SIZE:
            break
        filename = os.path.basename(filepath)
        if os.path.isfile(filepath.replace('-downsampling.csv', '-downsampling.done')):
            continue
        with open(filepath) as f:
            referenced = [a.strip() for a in f.readlines()[1:]]
        for ref in referenced:
            rz, rx, ry, r_parent_zoom = [int(a) for a in ref.replace('.pmtiles', '').split('-')]
            # Fixed 2026-09-04: this call predated D95/D107's required
            # `layer` argument (it would have raised TypeError if run) --
            # a referenced child may be either layer, so resolve it the
            # same way downsampling_run.py does.
            ref_layer = utils.resolve_layer(AGG_ID, rz, rx, ry, r_parent_zoom)
            folder = utils.get_pmtiles_folder(rx, ry, rz, layer=ref_layer, generation_id=AGG_ID)
            if os.path.isfile(f'{folder}/{ref}'):
                continue  # this reference is fine, not a gap
            ref_csv = f'{AGG_DIR}/{rz}-{rx}-{ry}-{r_parent_zoom}-downsampling.csv'
            if os.path.isfile(ref_csv):
                continue  # backlog, not a covering gap -- has a real csv, just not done yet
            # missing reference AND no downsampling.csv exists for it at all --
            # exactly D52's own "11-1728-880-13" pattern.
            checked += 1
            tile = mercantile.Tile(x=rx, y=ry, z=rz)
            is_genuine = not has_native_aggregation_beneath(tile)
            if is_genuine:
                genuine_gap += 1
            else:
                csv_missing_examples.append((filename, ref, tile))
            if checked >= SAMPLE_SIZE:
                break

    print(f'sampled {checked} "csv genuinely missing" references')
    print(f'genuine no-native-coverage gaps: {genuine_gap}')
    print(f'has native coverage but no downsampling.csv (real covering bug candidates): {checked - genuine_gap}')
    if csv_missing_examples:
        print('\nbug candidates (referencing item, missing ref, tile):')
        for filename, ref, tile in csv_missing_examples:
            print(f'  {filename} -> {ref}  ({tile})')


if __name__ == '__main__':
    main()
