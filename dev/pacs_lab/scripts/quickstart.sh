#!/bin/bash
# From nothing to two running fake PACS that hold real (public) data:
# venv, data, configs, start PACS_A + PACS_B + the C-MOVE receiver, load the
# data if the PACS are still empty, then C-ECHO both.
#
# Stop everything with servers.sh stop.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/env.sh"

"$HERE/setup.sh" || exit 1
[ -d "$WORK/data" ] || "$HERE/copy_data.sh"
"$HERE/make_configs.sh" >/dev/null || exit 1
"$HERE/servers.sh" start a b rx || exit 1
if [ -z "$(ls -A "$WORK/pacs_a/instances" 2>/dev/null)" ]; then
  "$HERE/load_data.sh" ab
fi
for pacs in "11112 PACS_A" "11113 PACS_B"; do
  set -- $pacs
  "$PY" -I -B "$LAB/scripts/pacs_client.py" --port "$1" --aec "$2" echo | grep '"status_text"'
done
echo "Running. Work folder: $WORK"
echo "Stop with: $HERE/servers.sh stop"
