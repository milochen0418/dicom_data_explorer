#!/usr/bin/env python
"""Read-only inventory of DICOM series folders.

Usage:
    py $LAB/scripts/inventory.py <root_dir> [--json out.json]

<root_dir> holds one sub-folder per series (as the dicom-data-explorer
collection does: <root>/<SeriesInstanceUID>/*.dcm). Files are only opened
for reading (header only, no pixel data). Nothing is written next to them.
"""
import argparse
import json
import os
import sys
from collections import Counter

from pydicom import dcmread
from pydicom.uid import UID


def referenced_series(ds):
    """Series this object points at (SEG, RTSTRUCT, SR, PR ...)."""
    refs = set()
    # SEG / SR / PR: ReferencedSeriesSequence (top level)
    for item in ds.get("ReferencedSeriesSequence", []):
        if "SeriesInstanceUID" in item:
            refs.add(str(item.SeriesInstanceUID))
    # RTSTRUCT: ReferencedFrameOfReferenceSequence > RTReferencedStudySequence
    #           > RTReferencedSeriesSequence
    for fr in ds.get("ReferencedFrameOfReferenceSequence", []):
        for st in fr.get("RTReferencedStudySequence", []):
            for se in st.get("RTReferencedSeriesSequence", []):
                if "SeriesInstanceUID" in se:
                    refs.add(str(se.SeriesInstanceUID))
    # SR / KOS evidence
    for st in ds.get("CurrentRequestedProcedureEvidenceSequence", []):
        for se in st.get("ReferencedSeriesSequence", []):
            if "SeriesInstanceUID" in se:
                refs.add(str(se.SeriesInstanceUID))
    return sorted(refs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--json")
    args = ap.parse_args()

    rows = []
    for name in sorted(os.listdir(args.root)):
        folder = os.path.join(args.root, name)
        if name.startswith(".") or not os.path.isdir(folder):
            continue
        files = [
            os.path.join(folder, f)
            for f in sorted(os.listdir(folder))
            if not f.startswith(".") and os.path.isfile(os.path.join(folder, f))
        ]
        if not files:
            continue
        size = sum(os.path.getsize(f) for f in files)
        ts = Counter()
        first = None
        for f in files:
            try:
                ds = dcmread(f, stop_before_pixels=True)
            except Exception as exc:  # not DICOM
                print(f"  skip {f}: {exc}", file=sys.stderr)
                continue
            ts[str(ds.file_meta.get("TransferSyntaxUID", "?"))] += 1
            if first is None:
                first = ds
        if first is None:
            continue
        ds = first
        rows.append(
            {
                "folder": name,
                "files": len(files),
                "mb": round(size / 1e6, 1),
                "modality": str(ds.get("Modality", "")),
                "sop_class": UID(ds.SOPClassUID).name,
                "sop_class_uid": str(ds.SOPClassUID),
                "transfer_syntaxes": {UID(k).name: v for k, v in ts.items()},
                "patient_id": str(ds.get("PatientID", "")),
                "patient_name": str(ds.get("PatientName", "")),
                "study_uid": str(ds.get("StudyInstanceUID", "")),
                "study_date": str(ds.get("StudyDate", "")),
                "study_desc": str(ds.get("StudyDescription", "")),
                "series_uid": str(ds.get("SeriesInstanceUID", "")),
                "series_desc": str(ds.get("SeriesDescription", "")),
                "series_number": str(ds.get("SeriesNumber", "")),
                "frames": str(ds.get("NumberOfFrames", "")),
                "refs": referenced_series(ds),
                "collection": str(ds.get("ClinicalTrialProtocolID", "")),
            }
        )

    by_series = {r["series_uid"]: r for r in rows}
    for r in rows:
        r["refs_present_locally"] = [s for s in r["refs"] if s in by_series]
        print(
            f"{r['modality']:8} {r['files']:4} files {r['mb']:7} MB  "
            f"pid={r['patient_id']!r} date={r['study_date']} "
            f"ts={r['transfer_syntaxes']}\n"
            f"         {r['sop_class']} | {r['series_desc']!r} | "
            f"coll={r['collection']!r}\n"
            f"         series={r['series_uid']}\n"
            f"         study ={r['study_uid']}"
            + (
                f"\n         refs={r['refs']} local={r['refs_present_locally']}"
                if r["refs"]
                else ""
            )
        )
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(rows, fh, indent=2)
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
