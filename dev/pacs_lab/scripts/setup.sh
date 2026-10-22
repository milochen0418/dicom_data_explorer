#!/bin/bash
# Create the lab's Python environment in $PACS_LAB_HOME/.venv (once).
set -euo pipefail
source "$(dirname "$0")/env.sh"
mkdir -p "$WORK"
if [ -x "$PY" ]; then
  echo "venv: $WORK/.venv"
  exit 0
fi
# The versions the lab was tested with on 2026-10-22; qrscp needs sqlalchemy.
PACKAGES=(pynetdicom==3.0.4 pydicom==3.0.2 sqlalchemy==2.1.5 requests dicomweb-client==0.61.2)
if command -v uv >/dev/null 2>&1; then
  uv venv --python 3.11 "$WORK/.venv"
  uv pip install --python "$PY" "${PACKAGES[@]}"
else
  python3 -m venv "$WORK/.venv"
  "$PY" -m pip install "${PACKAGES[@]}"
fi
