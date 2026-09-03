import json, mercantile, os, glob
from datetime import datetime, timezone, timedelta
import utils

JST = timezone(timedelta(hours=9))
gen = utils.get_aggregation_ids()[-1]

rows = []
seen = set()
for ext in ['.done', '.todo']:
    for fp in glob.glob(f'aggregation-store/{gen}/*-aggregation.csv{ext}'):
        fn = fp.split('/')[-1].replace('-aggregation.csv' + ext, '')
        z, x, y, childz = [int(a) for a in fn.split('-')]
        if (z, x, y) in seen:
            continue
        seen.add((z, x, y))
        b = mercantile.bounds(x, y, z)
        done = 1 if ext == '.done' else 0
        rows.append([round(b.west, 4), round(b.south, 4), round(b.east, 4), round(b.north, 4), z, done])
with open('/tmp/agg_tiles_fresh.json', 'w') as f:
    json.dump(rows, f)

started_at = datetime(2026, 8, 31, 5, 29, 0, tzinfo=JST)
started_epoch = started_at.timestamp()
baseline = 3029
files = glob.glob(f'aggregation-store/{gen}/*-aggregation.csv.done')
mtimes = []
for fp in files:
    fn = fp.split('/')[-1].replace('-aggregation.csv.done', '')
    z, x, y, childz = [int(a) for a in fn.split('-')]
    folder = utils.get_pmtiles_folder(x, y, z, layer='aggregation', generation_id=gen)
    p = f'{folder}/{fn}.pmtiles'
    if os.path.isfile(p):
        m = os.path.getmtime(p)
        if m >= started_epoch - 5:
            mtimes.append(m)
mtimes.sort()
now = datetime.now(JST).timestamp()
buckets = []
t = started_epoch
idx = 0
while t <= now + 1:
    while idx < len(mtimes) and mtimes[idx] <= t:
        idx += 1
    buckets.append({'t': datetime.fromtimestamp(t, JST).isoformat(), 'done': baseline + idx, 'repaired': idx})
    t += 15 * 60
while idx < len(mtimes) and mtimes[idx] <= now:
    idx += 1
buckets.append({'t': datetime.fromtimestamp(now, JST).isoformat(), 'done': baseline + idx, 'repaired': idx})
with open('/tmp/history_fresh.json', 'w') as f:
    json.dump(buckets, f, indent=2)

print(json.dumps({'done_count': len(files), 'agg_rows': len(rows), 'history_points': len(buckets)}))
