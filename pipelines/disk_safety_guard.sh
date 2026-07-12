#!/bin/bash
# Sourced helper, not meant to be run directly.
#
# Kills the whole process group (not just the named script) once free disk
# drops below a threshold. Pattern-matching on a command line (e.g.
# `pkill -f "aggregation_run.py"`) misses multiprocessing workers on macOS:
# Python's default 'spawn' start method launches workers as
# `python -c "from multiprocessing.spawn import spawn_main; ..."`, which
# never contains the target script's name. Process-group kill catches the
# parent and every worker/subprocess it spawned, since they all inherit the
# same PGID unless they explicitly detach.
#
# Usage: wait_with_disk_guard <PID> <MIN_FREE_GB> <label> [poll_seconds]
wait_with_disk_guard() {
  local target_pid="$1"
  local min_free_gb="$2"
  local label="$3"
  local poll_seconds="${4:-300}"
  local pgid
  pgid=$(ps -o pgid= -p "$target_pid" 2>/dev/null | tr -d ' ')

  while kill -0 "$target_pid" 2>/dev/null; do
    local free_kb free_gb
    free_kb=$(df -k / | tail -1 | awk '{print $4}')
    free_gb=$((free_kb / 1024 / 1024))
    echo "[$(date +%H:%M:%S)] ${label}: free=${free_gb}GB"
    if [ "$free_gb" -lt "$min_free_gb" ]; then
      echo "===== $(date) : DISK CRITICAL (${free_gb}GB free < ${min_free_gb}GB) - ABORTING ${label} (pgid=${pgid}) ====="
      kill -TERM -- "-${pgid}" 2>/dev/null
      sleep 3
      kill -KILL -- "-${pgid}" 2>/dev/null
      echo "===== $(date) : ${label} aborted for disk safety ====="
      return 1
    fi
    sleep "$poll_seconds"
  done
  return 0
}
