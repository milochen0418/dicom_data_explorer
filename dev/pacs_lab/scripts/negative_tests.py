#!/usr/bin/env python
"""Negative / edge-case tests against one fake PACS. Records exact status codes.

Usage (PACS A = qrscp):
  py $LAB/scripts/negative_tests.py --port 11112 --aec PACS_A \
      --study-uid <KiTS study> --series-uid <KiTS CT series> \
      --wildcards 'PTEN*,pten*,KiTS-0006?,*,*409,PTEN_409,pten_409' \
      --json out/negative_PACS_A.json

  --only wildcards   run only the PatientName wildcard queries
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pacs_client import (  # noqa: E402
    build_identifier, c_echo, c_find, c_get, c_move, retrieve_identifier,
)


def summarize(name, res):
    keep = ("established", "rejected", "aborted", "reject", "status", "status_text",
            "final_counters", "pending", "error_comment", "files_written",
            "unique_StudyInstanceUID", "unique_SeriesInstanceUID", "assoc_ms",
            "failed_sop_instance_uids", "query", "move_destination")
    s = {k: res[k] for k in keep if k in res}
    if "progress" in res:
        s["pending"] = len(res["progress"])
        if res["progress"]:
            s["first_pending"] = res["progress"][0]
    if "rows" in res:
        s["distinct_patient_names"] = sorted({r.get("PatientName", "") for r in res["rows"]})
    print(f"\n### {name}\n{json.dumps(s, indent=2, default=str)}")
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--aec", required=True)
    ap.add_argument("--study-uid")
    ap.add_argument("--series-uid")
    ap.add_argument("--wildcards", default="")
    ap.add_argument("--only", choices=["wildcards"])
    ap.add_argument("--json")
    a = ap.parse_args()
    H, P, AEC = a.host, a.port, a.aec
    results = {}
    tmp = tempfile.mkdtemp(prefix="neg_get_")

    if a.only != "wildcards":
        # 1. Wrong called AE title (the PACS is PACS_A/PACS_B; we call it WRONG_AET)
        results["wrong_called_aet"] = summarize(
            "1. C-ECHO with wrong called AE title 'WRONG_AET'",
            c_echo(H, P, "WRONG_AET"))

        # 2. Unknown move destination
        results["move_unknown_destination"] = summarize(
            "2. C-MOVE to unknown destination 'NOWHERE'",
            c_move(H, P, AEC, retrieve_identifier(a.study_uid, a.series_uid), "NOWHERE", every=0))

        # 3. Known destination whose Storage SCP is not running
        results["move_offline_destination"] = summarize(
            "3. C-MOVE to known destination 'OFFLINE' (nothing listening on its port)",
            c_move(H, P, AEC, retrieve_identifier(a.study_uid, a.series_uid), "OFFLINE", every=0))

        # 4. C-GET without proposing ANY storage SOP class
        results["get_no_storage_contexts"] = summarize(
            "4. C-GET proposing no storage presentation contexts",
            c_get(H, P, AEC, retrieve_identifier(a.study_uid, a.series_uid),
                  os.path.join(tmp, "4"), storage="none", every=0))

        # 5. C-GET proposing storage contexts but forgetting SCP/SCU role selection
        results["get_no_role_selection"] = summarize(
            "5. C-GET with storage contexts but WITHOUT SCP role selection",
            c_get(H, P, AEC, retrieve_identifier(a.study_uid, a.series_uid),
                  os.path.join(tmp, "5"), storage="all", role=False, every=0))

        # 6. STUDY-level C-GET offering only CT storage (study also holds a SEG)
        results["get_study_only_ct_offered"] = summarize(
            "6. STUDY-level C-GET offering only CTImageStorage (study has CT + SEG)",
            c_get(H, P, AEC, retrieve_identifier(a.study_uid),
                  os.path.join(tmp, "6"), storage="CTImageStorage", every=0))

        # 7. SERIES-level C-FIND without StudyInstanceUID at all
        results["series_find_without_study_uid"] = summarize(
            "7. SERIES-level C-FIND without StudyInstanceUID",
            c_find(H, P, AEC, build_identifier(
                "SERIES", return_keys=["SeriesInstanceUID", "Modality", "SeriesNumber"]),
                show=False))

        # 8. SERIES-level C-FIND with an EMPTY StudyInstanceUID (universal match)
        results["series_find_empty_study_uid"] = summarize(
            "8. SERIES-level C-FIND with empty StudyInstanceUID",
            c_find(H, P, AEC, build_identifier("SERIES"), show=False))

        # 9. STUDY-level C-FIND that carries a SERIES-level key (Modality)
        results["study_find_with_series_key"] = summarize(
            "9. STUDY-level C-FIND with a SERIES-level key Modality=CT",
            c_find(H, P, AEC, build_identifier("STUDY", Modality="CT"), show=False))

        # 10. C-GET for a series UID that does not exist
        results["get_unknown_series"] = summarize(
            "10. C-GET of a non-existent SeriesInstanceUID",
            c_get(H, P, AEC, retrieve_identifier(a.study_uid, "1.2.3.4.5.6.7.8.9"),
                  os.path.join(tmp, "10"), every=0))

        # 12./13. C-GET and C-MOVE at SERIES level WITHOUT StudyInstanceUID
        bad = retrieve_identifier(a.study_uid, a.series_uid)
        del bad.StudyInstanceUID
        results["get_series_without_study_uid"] = summarize(
            "12. SERIES-level C-GET without StudyInstanceUID",
            c_get(H, P, AEC, bad, os.path.join(tmp, "12"), every=0))
        results["move_series_without_study_uid"] = summarize(
            "13. SERIES-level C-MOVE without StudyInstanceUID",
            c_move(H, P, AEC, bad, "EXPLORER", every=0))

    # 11. PatientName wildcard queries at STUDY level
    for pattern in [w for w in a.wildcards.split(",") if w]:
        res = c_find(H, P, AEC, build_identifier("STUDY", PatientName=pattern), show=False)
        results[f"patientname {pattern}"] = summarize(
            f"11. STUDY-level C-FIND PatientName={pattern!r}", res)

    shutil.rmtree(tmp, ignore_errors=True)
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(results, fh, indent=2, default=str)

    print("\n==== summary ====")
    for k, v in results.items():
        st = v.get("status_text") or (
            f"association {'REJECTED ' + v['reject']['reason_str'] if v.get('reject') else 'failed'}"
            if not v.get("established", True) else "")
        extra = []
        if "final_counters" in v:
            extra.append(f"counters={v['final_counters']}")
        if "pending" in v:
            extra.append(f"pending={v['pending']}")
        if "unique_StudyInstanceUID" in v:
            extra.append(f"studies={v['unique_StudyInstanceUID']}")
        if "unique_SeriesInstanceUID" in v:
            extra.append(f"series={v['unique_SeriesInstanceUID']}")
        if v.get("distinct_patient_names"):
            extra.append(f"names={v['distinct_patient_names']}")
        print(f"{k:34} {st:70} {' '.join(extra)}")


if __name__ == "__main__":
    main()
