#!/bin/bash
# Load the copied series into the fake PACS with C-STORE (pynetdicom storescu).
#
#   load_data.sh ab     PACS_A (11112): kits_ct + kits_seg (study 1, CT+SEG)
#                                       pten409_pt + pten409_rtstruct (study 2, PT+RTSTRUCT)
#                       PACS_B (11113): nlst_ct + nlst_seg (study 3, CT+SEG)
#                                       kits_seg (the SAME SEG series as on PACS_A, to test merging)
#                                       edge_labelmap_seg (newer SOP class + Deflated transfer syntax)
#   load_data.sh mini   MINI_PACS (11115): the same two studies as PACS_A
#
# Each run writes $PACS_LAB_HOME/logs/load_<AET>_<label>.log and prints the
# number of C-STORE responses with Status 0x0000 and the wall time.
set -uo pipefail
source "$(dirname "$0")/env.sh"
mkdir -p "$WORK/out" "$WORK/logs"
cd "$WORK/out"
TIMEFORMAT="%R"

store() { # port called_aet label [extra storescu flags...]
  local port=$1 aec=$2 label=$3; shift 3
  if [ ! -d "$WORK/data/$label" ]; then echo "skip $label: no $WORK/data/$label (run copy_data.sh)"; return; fi
  local tag=${STORE_TAG:-}
  local log="$WORK/logs/load_${aec}_${label}${tag}.log"
  local n; n=$(find "$WORK/data/$label" -type f | wc -l | tr -d ' ')
  local secs
  secs=$( { time "$PY" -I -m pynetdicom storescu 127.0.0.1 "$port" "$WORK/data/$label" -r \
              -aet LOADER -aec "$aec" -v "$@" >"$log" 2>&1 ; } 2>&1 )
  printf "%-9s <- %-18s files=%-4s ok(0x0000)=%-4s errors=%-3s time=%ss  flags=[%s]\n" \
    "$aec" "$label" "$n" "$(grep -c 'Status: 0x0000' "$log")" \
    "$(grep -ciE 'error|failed|rejected|aborted' "$log")" "$secs" "$*"
}

case "${1:-ab}" in
  ab)
    store 11112 PACS_A kits_ct
    store 11112 PACS_A kits_seg
    store 11112 PACS_A pten409_pt
    store 11112 PACS_A pten409_rtstruct
    store 11113 PACS_B nlst_ct
    store 11113 PACS_B nlst_seg
    store 11113 PACS_B kits_seg
    # Label Map Segmentation Storage (1.2.840.10008.5.1.4.1.1.66.7) is not in
    # storescu's default list of 120 storage classes: propose exactly the
    # contexts the file needs (-cx), or the store fails.
    store 11113 PACS_B edge_labelmap_seg -cx
    ;;
  mini)
    store 11115 MINI_PACS kits_ct
    store 11115 MINI_PACS kits_seg
    store 11115 MINI_PACS pten409_pt
    store 11115 MINI_PACS pten409_rtstruct
    ;;
  *) echo "usage: $0 [ab|mini]"; exit 2 ;;
esac
