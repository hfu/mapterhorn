#!/bin/bash
cd /Users/hfu/photosynthesis/mapterhorn/pipelines
LOG=/Users/hfu/photosynthesis/mapterhorn/pipelines/auto_aggregation.log
exec >> "$LOG" 2>&1

echo "===== $(date) : monitor attached to already-running aggregation_run.py (PID 16143) ====="
MIN_FREE_GB=20
while pgrep -f "^python aggregation_run.py$" > /dev/null; do
  FREE_KB=$(df -k / | tail -1 | awk '{print $4}')
  FREE_GB=$((FREE_KB / 1024 / 1024))
  DONE=$(find aggregation-store -name "*.done" 2>/dev/null | wc -l | tr -d ' ')
  echo "[$(date +%H:%M:%S)] free=${FREE_GB}GB done=${DONE}/187"
  if [ "$FREE_GB" -lt "$MIN_FREE_GB" ]; then
    echo "===== $(date) : DISK CRITICAL (${FREE_GB}GB free < ${MIN_FREE_GB}GB threshold) - ABORTING aggregation_run.py ====="
    pkill -TERM -f "aggregation_run.py"
    sleep 3
    pkill -TERM -f "gdal_translate.*aggregation-store"
    echo "===== $(date) : aggregation_run.py aborted for disk safety ====="
    break
  fi
  sleep 300
done

DONE_COUNT=$(find aggregation-store -name "*.done" 2>/dev/null | wc -l | tr -d ' ')
TOTAL_COUNT=$(find aggregation-store -name "*-aggregation.csv" 2>/dev/null | wc -l | tr -d ' ')
echo "===== $(date) : aggregation_run.py process ended. done: ${DONE_COUNT} / ${TOTAL_COUNT} ====="
df -h / | tail -1
echo "===== $(date) : monitor finished ====="
