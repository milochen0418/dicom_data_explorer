#!/bin/bash
# Copy the lab's seven public IDC series out of the explorer's downloads into
# $PACS_LAB_HOME/data/<label>. The source folder is only read.
#
# Download them with the explorer first if one is missing (search the UID).
set -uo pipefail
source "$(dirname "$0")/env.sh"

copy() { # label SeriesInstanceUID
  local src="$SOURCE/$2" dest="$WORK/data/$1"
  if [ ! -d "$src" ]; then
    echo "skip $1: $src not found"
    return
  fi
  mkdir -p "$dest"
  find "$src" -maxdepth 1 -type f -name '*.dcm' -exec cp {} "$dest/" \;
  echo "$1: $(find "$dest" -type f | wc -l | tr -d ' ') files"
}

copy kits_ct           1.3.6.1.4.1.14519.5.2.1.6919.4624.322088738018313179675994057197   # CT, 29 files
copy kits_seg          1.2.276.0.7230010.3.1.3.0.75019.1588584314.382026                  # SEG of kits_ct
copy pten409_pt        2.25.334617998896703213530746478604223875589                       # PT, 159 files
copy pten409_rtstruct  2.16.840.1.114033.42.4510.20260112.12592989.1.481.3                # RTSTRUCT of pten409_pt
copy nlst_ct           1.2.840.113654.2.55.262564043997006039201662725545109911310        # CT, 162 files
copy nlst_seg          1.2.276.0.7230010.3.1.3.481037312.100731.1761242635.594453         # SEG of nlst_ct
copy edge_labelmap_seg 1.2.276.0.7230010.3.1.3.0.56050.1787705408.974747                  # Label Map SEG, Deflated
