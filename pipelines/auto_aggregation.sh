#!/bin/bash
set -m
cd /Users/hfu/photosynthesis/mapterhorn/pipelines
source .venv/bin/activate
source ./disk_safety_guard.sh

LOG=/Users/hfu/photosynthesis/mapterhorn/pipelines/auto_aggregation.log
exec >> "$LOG" 2>&1

echo "===== $(date) : auto_aggregation.sh started ====="
echo "polygon-store/freetown.gpkg already verified (1 feature, real footprint) - skipping polygonize wait"
df -h / | tail -1

AGG_ID=$(python -c "import utils; ids = utils.get_aggregation_ids(); print(ids[-1] if ids else '')")
TOTAL=$(find "aggregation-store/$AGG_ID" -name "*-aggregation.csv" 2>/dev/null | wc -l | tr -d ' ')

echo "===== $(date) : starting AGGREGATION_WORKERS=4 python aggregation_run.py (aggregation_id=$AGG_ID, total=$TOTAL) ====="
AGGREGATION_WORKERS=4 python aggregation_run.py &
AGG_PID=$!
echo "aggregation_run.py PID=$AGG_PID"

wait_with_disk_guard "$AGG_PID" 20 "aggregation_run.py" 300

wait "$AGG_PID" 2>/dev/null
AGG_EXIT=$?

echo "===== $(date) : aggregation_run.py ended (exit=$AGG_EXIT) ====="
DONE_COUNT=$(find "aggregation-store/$AGG_ID" -name "*.done" 2>/dev/null | wc -l | tr -d ' ')
echo "aggregation done: ${DONE_COUNT} / ${TOTAL}"
df -h / | tail -1
echo "===== $(date) : auto_aggregation.sh finished ====="
