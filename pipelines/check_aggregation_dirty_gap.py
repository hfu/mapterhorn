"""Quantify the scope of the aggregation_covering.py dirty-filter issue
found while investigating D52's covering gaps: how many current-
generation *-aggregation.csv items were judged "not dirty" relative to
the old Kyushu generation (and so never got a .todo marker, so
aggregation_run.py never processed them) AND have no corresponding
pmtiles-store output at all? Read-only, no side effects."""
from glob import glob
import os

import utils

CURRENT_ID = '01M0MWK852631SHCHPA66F21WQ'
LAST_ID = '01M0FNHYXSAMNVTV430XD3XB5T'  # Kyushu test generation
AGG_DIR = f'aggregation-store/{CURRENT_ID}'

all_csv = sorted(glob(f'{AGG_DIR}/*-aggregation.csv'))
print(f'total current-generation aggregation.csv items: {len(all_csv):_}')

dirty_filenames = set(utils.get_dirty_aggregation_filenames(CURRENT_ID, LAST_ID))
print(f'dirty (would get a .todo marker): {len(dirty_filenames):_}')
not_dirty_count = len(all_csv) - len(dirty_filenames)
print(f'NOT dirty (never got a .todo marker, assumed already-built): {not_dirty_count:_}')

missing_output = 0
missing_examples = []
for filepath in all_csv:
    filename = os.path.basename(filepath)
    if filename in dirty_filenames:
        continue  # this one did get processed via .todo -> skip
    base = filename.replace('-aggregation.csv', '')
    z, x, y, child_z = [int(a) for a in base.split('-')]
    folder = utils.get_pmtiles_folder(x, y, z)
    out_filepath = f'{folder}/{base}.pmtiles'
    if not os.path.isfile(out_filepath):
        missing_output += 1
        if len(missing_examples) < 10:
            missing_examples.append(base)

print(f'\nof the not-dirty items, missing pmtiles-store output entirely: {missing_output:_}')
if missing_examples:
    print('examples:', missing_examples)
