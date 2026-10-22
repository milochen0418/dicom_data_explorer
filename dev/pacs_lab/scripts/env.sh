# Paths shared by the lab scripts; each script sources this file.
#
# The scripts live in the repo (dev/pacs_lab). Everything they write -- the
# venv, the copied data, what the fake PACS store, logs, retrieved files --
# lives in $PACS_LAB_HOME, outside the repo: Reflex's dev reloader watches
# every top-level folder of the repo, so files written there would restart a
# running dev backend.
LAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${PACS_LAB_HOME:-$HOME/.cache/dicom_data_explorer/pacs_lab}"
PY="$WORK/.venv/bin/python"
# The explorer's own downloads; copy_data.sh only reads from here.
SOURCE="${PACS_LAB_SOURCE:-/Users/Shared/re-ddns/collections/dicom/by-app/dicom-data-explorer}"
