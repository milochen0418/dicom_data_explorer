#!/bin/bash
# Start / stop / show the lab's DICOM servers (all bound to 127.0.0.1).
#
#   servers.sh start   [a] [b] [rx] [mini]   (default: a b rx)
#   servers.sh stop                            (stops every server we started)
#   servers.sh status
#
#   a    = fake PACS A : qrscp, AE PACS_A,   port 11112
#   b    = fake PACS B : qrscp, AE PACS_B,   port 11113
#   rx   = receiver    : storescp, AE EXPLORER, port 11114 (the C-MOVE destination)
#   mini = fake PACS C : mini_pacs.py, AE MINI_PACS, port 11115
#
# PIDs go to $PACS_LAB_HOME/run/<name>.pid, logs to $PACS_LAB_HOME/logs/<name>.log.
# Before binding, the port is checked with lsof; if it is busy we refuse to
# start (we never kill processes we did not start).
set -uo pipefail
source "$(dirname "$0")/env.sh"
mkdir -p "$WORK/run" "$WORK/logs" "$WORK/received/explorer"

port_of() { case "$1" in a) echo 11112;; b) echo 11113;; rx) echo 11114;; mini) echo 11115;; esac; }

cmd_of() {
  case "$1" in
    a)    echo "$PY -I -m pynetdicom qrscp -c $WORK/config/pacs_a.ini -v" ;;
    b)    echo "$PY -I -m pynetdicom qrscp -c $WORK/config/pacs_b.ini -v" ;;
    rx)   echo "$PY -I -m pynetdicom storescp 11114 -aet EXPLORER -ba 127.0.0.1 -od $WORK/received/explorer -v" ;;
    mini) echo "$PY -I -B $LAB/scripts/mini_pacs.py --aet MINI_PACS --port 11115 --store $WORK/mini_pacs --dest EXPLORER=127.0.0.1:11114 --dest DDE_EXPLORER=127.0.0.1:11120" ;;
  esac
}

start_one() {
  local name=$1 port; port=$(port_of "$name")
  if [ -z "$port" ]; then echo "unknown server: $name"; return 1; fi
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[$name] port $port is BUSY - not starting:"; lsof -nP -iTCP:"$port" -sTCP:LISTEN
    return 1
  fi
  # Run from the work folder. `exec` matters: macOS ships bash 3.2, which
  # otherwise keeps a bash wrapper as the parent and $! would be the wrapper.
  local cmd; cmd=$(cmd_of "$name")
  (cd "$WORK" && exec nohup $cmd >"$WORK/logs/$name.log" 2>&1) &
  local spawned=$!
  for _ in $(seq 1 100); do
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 && break; sleep 0.1
  done
  # Record the PID that really owns the listening socket, and double-check
  # that it is the process we just spawned.
  local listener; listener=$(lsof -nP -t -iTCP:"$port" -sTCP:LISTEN | head -1)
  if [ -z "$listener" ]; then echo "[$name] FAILED to start, see $WORK/logs/$name.log"; tail -5 "$WORK/logs/$name.log"; return 1; fi
  [ "$listener" = "$spawned" ] || echo "[$name] note: spawned pid $spawned, listener pid $listener"
  echo "$listener" >"$WORK/run/$name.pid"
  echo "[$name] pid $listener listening on 127.0.0.1:$port"
}

stop_all() {
  for f in "$WORK"/run/*.pid; do
    [ -e "$f" ] || continue
    local name pid; name=$(basename "$f" .pid); pid=$(cat "$f")
    if kill -0 "$pid" 2>/dev/null; then kill "$pid" && echo "[$name] stopped pid $pid"; else echo "[$name] pid $pid not running"; fi
    rm -f "$f"
  done
  sleep 0.5
  for p in 11112 11113 11114 11115; do
    if lsof -nP -iTCP:$p -sTCP:LISTEN >/dev/null 2>&1; then echo "port $p STILL BUSY"; lsof -nP -iTCP:$p -sTCP:LISTEN; else echo "port $p free"; fi
  done
}

case "${1:-}" in
  start) shift; names=("$@"); [ ${#names[@]} -eq 0 ] && names=(a b rx)
         failed=0
         for n in "${names[@]}"; do start_one "$n" || failed=1; done
         exit $failed ;;
  stop)  stop_all ;;
  status) for f in "$WORK"/run/*.pid; do [ -e "$f" ] && echo "$(basename "$f" .pid): pid $(cat "$f") $(kill -0 "$(cat "$f")" 2>/dev/null && echo running || echo dead)"; done
          for p in 11112 11113 11114 11115; do echo "port $p: $(lsof -nP -iTCP:$p -sTCP:LISTEN | tail -n +2 | awk '{print $1, $2}' | head -1)"; done ;;
  *) echo "usage: $0 start [a] [b] [rx] [mini] | stop | status"; exit 2 ;;
esac
