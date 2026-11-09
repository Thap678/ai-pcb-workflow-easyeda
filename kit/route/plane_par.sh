#!/bin/bash
# Run several route_plane.py variants at once, one core each (the A* search is single-threaded).
# usage: bash kit/route/plane_par.sh <dump.json> <out_dir> "name:args" ["name:args" ...]
#   e.g. bash kit/route/plane_par.sh dump.json route_plane_runs "base:" "dir:--layer-dir" "c22:--clearance 0.22"
# Writes <out_dir>/<name>.json and <name>.log; prints "<name> done: <result line>" as each one finishes.
# (A loop of subshells, not a shell function sent to the background with &: the function is then defined only
#  inside the first background job and every other variant fails with "command not found".)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
DUMP=$1; OUT=$2; shift 2
mkdir -p "$OUT"
for spec in "$@"; do
  name=${spec%%:*}; args=${spec#*:}
  ( python "$HERE/route_plane.py" "$DUMP" "$OUT/$name.json" $args > "$OUT/$name.log" 2>&1
    echo "$name done: $(grep -E '^\[signal\] routed|STALL|Traceback' "$OUT/$name.log" | tail -1)" ) &
done
wait
echo ALL DONE
