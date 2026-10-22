#!/usr/bin/env python
"""Check that retrieved DICOM files match the originals.

Usage:
    py $LAB/scripts/verify.py --orig data/kits_ct --recv received/get_kits_ct \
        [--series-uid <uid>]   # only look at received files of this series

Checks, per SOP Instance UID:
  * same set of SOPInstanceUIDs (none missing, none extra)
  * PixelData bytes identical (SHA-256), or both without PixelData
  * key header values identical (Patient/Study/Series/SOP UIDs, Rows, Columns,
    BitsAllocated, InstanceNumber, SOPClassUID)
  * decoded pixel arrays equal for a sample (needs numpy; skipped if absent)
Also reports whether whole files are byte-identical and which transfer
syntaxes the received files use (a PACS may legally re-encode).
"""
import argparse
import hashlib
import json
import os
import sys

from pydicom import dcmread
from pydicom.uid import UID

KEYS = ["PatientID", "StudyInstanceUID", "SeriesInstanceUID", "SOPClassUID",
        "InstanceNumber", "Rows", "Columns", "BitsAllocated", "NumberOfFrames"]


def index(folder, series_uid=None):
    out = {}
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if name.startswith(".") or not os.path.isfile(path):
            continue
        ds = dcmread(path)
        if series_uid and ds.SeriesInstanceUID != series_uid:
            continue
        pix = ds.get("PixelData")
        out[str(ds.SOPInstanceUID)] = {
            "path": path,
            "ts": str(ds.file_meta.TransferSyntaxUID),
            "pixel_sha256": hashlib.sha256(pix).hexdigest() if pix is not None else None,
            "file_sha256": hashlib.sha256(open(path, "rb").read()).hexdigest(),
            "keys": {k: str(ds.get(k, "")) for k in KEYS},
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orig", required=True)
    ap.add_argument("--recv", required=True)
    ap.add_argument("--series-uid")
    ap.add_argument("--sample-arrays", type=int, default=3)
    a = ap.parse_args()

    orig = index(a.orig)
    recv = index(a.recv, a.series_uid)
    o, r = set(orig), set(recv)
    res = {
        "orig_dir": a.orig, "recv_dir": a.recv,
        "orig_files": len(orig), "recv_files": len(recv),
        "missing": len(o - r), "extra": len(r - o),
        "pixel_equal": 0, "pixel_diff": 0, "no_pixel_both": 0,
        "keys_equal": 0, "keys_diff": 0, "file_bytes_identical": 0,
        "orig_ts": sorted({UID(v["ts"]).name for v in orig.values()}),
        "recv_ts": sorted({UID(v["ts"]).name for v in recv.values()}),
    }
    for uid in sorted(o & r):
        A, B = orig[uid], recv[uid]
        if A["pixel_sha256"] is None and B["pixel_sha256"] is None:
            res["no_pixel_both"] += 1
        elif A["pixel_sha256"] == B["pixel_sha256"]:
            res["pixel_equal"] += 1
        else:
            res["pixel_diff"] += 1
        res["keys_equal" if A["keys"] == B["keys"] else "keys_diff"] += 1
        res["file_bytes_identical"] += A["file_sha256"] == B["file_sha256"]

    # Decoded pixel arrays for a few instances (numpy comes with dicomweb-client)
    try:
        import numpy as np  # noqa: F401
        checked = 0
        for uid in sorted(o & r)[: a.sample_arrays]:
            da, db = dcmread(orig[uid]["path"]), dcmread(recv[uid]["path"])
            if "PixelData" in da:
                assert (da.pixel_array == db.pixel_array).all()
                checked += 1
        res["pixel_array_equal_sample"] = checked
    except ImportError:
        res["pixel_array_equal_sample"] = "numpy not installed"

    ok = (res["missing"] == 0 and res["extra"] == 0 and res["pixel_diff"] == 0
          and res["keys_diff"] == 0 and len(o) > 0)
    res["VERDICT"] = "MATCH" if ok else "MISMATCH"
    print(json.dumps(res, indent=2))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
