#!/bin/bash
cd /Users/hfu/photosynthesis/mapterhorn/pipelines
source .venv/bin/activate

LOG=/Users/hfu/photosynthesis/mapterhorn/pipelines/auto_downsampling_orchestration.log
exec >> "$LOG" 2>&1

echo "===== $(date) : auto_downsampling.sh started ====="

AGG_ID=$(python -c "import utils; ids = utils.get_aggregation_ids(); print(ids[-1] if ids else '')")
if [ -z "$AGG_ID" ]; then
  echo "ERROR: no aggregation_id found. Aborting."
  exit 1
fi
echo "aggregation_id: $AGG_ID"

echo "waiting for aggregation to reach 187/187 done (or for aggregation_run.py to exit)..."
while true; do
  DONE=$(find "aggregation-store/$AGG_ID" -name "*.done" 2>/dev/null | wc -l | tr -d ' ')
  TOTAL=$(find "aggregation-store/$AGG_ID" -name "*-aggregation.csv" 2>/dev/null | wc -l | tr -d ' ')
  echo "[$(date +%H:%M:%S)] aggregation done=${DONE}/${TOTAL}"
  if [ "$DONE" -ge "$TOTAL" ] && [ "$TOTAL" -gt 0 ]; then
    echo "===== $(date) : aggregation complete ====="
    break
  fi
  if ! pgrep -f "^python aggregation_run.py$" > /dev/null; then
    echo "===== $(date) : aggregation_run.py no longer running but done(${DONE}) < total(${TOTAL}) - proceeding anyway with what's available ====="
    break
  fi
  sleep 120
done

echo "===== $(date) : running downsampling_covering.py ====="
python downsampling_covering.py

echo "===== $(date) : starting DOWNSAMPLING_WORKERS=4 python -u downsampling_run.py ====="
rm -f downsampling.log
DOWNSAMPLING_WORKERS=4 python -u downsampling_run.py > downsampling.log 2>&1 &
DS_PID=$!
echo "downsampling_run.py PID=$DS_PID"

MIN_FREE_GB=20
while kill -0 "$DS_PID" 2>/dev/null; do
  FREE_KB=$(df -k / | tail -1 | awk '{print $4}')
  FREE_GB=$((FREE_KB / 1024 / 1024))
  LAST_LINE=$(tail -1 downsampling.log 2>/dev/null)
  echo "[$(date +%H:%M:%S)] free=${FREE_GB}GB last: ${LAST_LINE}"
  if [ "$FREE_GB" -lt "$MIN_FREE_GB" ]; then
    echo "===== $(date) : DISK CRITICAL (${FREE_GB}GB free < ${MIN_FREE_GB}GB threshold) - ABORTING downsampling_run.py ====="
    pkill -TERM -f "downsampling_run.py"
    sleep 3
    echo "===== $(date) : downsampling_run.py aborted for disk safety ====="
    break
  fi
  sleep 300
done

wait "$DS_PID" 2>/dev/null
DS_EXIT=$?

echo "===== $(date) : downsampling_run.py ended (exit=$DS_EXIT) ====="
df -h / | tail -1
echo "===== $(date) : auto_downsampling.sh finished ====="
