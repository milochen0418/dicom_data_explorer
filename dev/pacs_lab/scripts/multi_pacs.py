#!/usr/bin/env python
"""Query several PACS at once and merge the series by SeriesInstanceUID.

Usage:
  py $LAB/scripts/multi_pacs.py \
      --pacs PACS_A@127.0.0.1:11112 --pacs PACS_B@127.0.0.1:11113 \
      [--patient-id ID] [--study-date 19990101-20231231] [--json out/multi.json]

For every PACS (in parallel threads):
  1. STUDY-level C-FIND with the given filters (no filter = all studies)
  2. SERIES-level C-FIND for each StudyInstanceUID that PACS returned
Then all series are merged into one table keyed by SeriesInstanceUID, with
the list of PACS that hold each series ("found on"). A PACS that answers one
response per instance (like qrscp) is de-duplicated here.
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pacs_client import build_identifier, c_find  # noqa: E402


def parse_pacs(spec):
    aet, hp = spec.split("@", 1)
    host, port = hp.rsplit(":", 1)
    return {"aet": aet, "host": host, "port": int(port)}


def query_one(pacs, filters):
    t0 = time.perf_counter()
    studies = c_find(pacs["host"], pacs["port"], pacs["aet"],
                     build_identifier("STUDY", **filters), show=False)
    result = {"pacs": pacs["aet"], "study_status": studies.get("status_text"),
              "study_responses": studies.get("pending", 0), "series": [], "errors": []}
    if not studies.get("established"):
        result["errors"].append(f"association failed: {studies}")
        return result
    study_rows = {}
    for r in studies["rows"]:
        study_rows.setdefault(r["StudyInstanceUID"], r)      # dedupe per study
    result["studies"] = len(study_rows)
    for st_uid, st in study_rows.items():
        se = c_find(pacs["host"], pacs["port"], pacs["aet"],
                    build_identifier("SERIES", StudyInstanceUID=st_uid), show=False)
        if se.get("status") != 0x0000:
            result["errors"].append(f"SERIES query for {st_uid}: {se.get('status_text')}")
        counts = {}
        for r in se["rows"]:
            counts[r["SeriesInstanceUID"]] = counts.get(r["SeriesInstanceUID"], 0) + 1
        seen = set()
        for r in se["rows"]:
            uid = r["SeriesInstanceUID"]
            if uid in seen:
                continue
            seen.add(uid)
            n = r.get("NumberOfSeriesRelatedInstances") or ""
            result["series"].append({
                "SeriesInstanceUID": uid, "StudyInstanceUID": st_uid,
                "PatientID": st.get("PatientID", ""), "StudyDate": st.get("StudyDate", ""),
                "Modality": r.get("Modality", ""), "SeriesNumber": r.get("SeriesNumber", ""),
                "SeriesDescription": r.get("SeriesDescription", "") or "",
                # qrscp has no NumberOfSeriesRelatedInstances, but it sends one
                # response per instance, so the duplicate count is the size
                "instances": int(n) if str(n).isdigit() else counts[uid],
                "instances_source": "NumberOfSeriesRelatedInstances" if str(n).isdigit()
                                    else "count of duplicate responses",
            })
    result["elapsed_s"] = round(time.perf_counter() - t0, 3)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pacs", action="append", required=True, help="AET@host:port")
    ap.add_argument("--patient-id")
    ap.add_argument("--study-date")
    ap.add_argument("--json")
    a = ap.parse_args()
    filters = {"PatientID": a.patient_id, "StudyDate": a.study_date}
    pacs_list = [parse_pacs(p) for p in a.pacs]

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(pacs_list)) as pool:
        per_pacs = list(pool.map(lambda p: query_one(p, filters), pacs_list))
    total = round(time.perf_counter() - t0, 3)

    merged = {}
    for res in per_pacs:
        for s in res["series"]:
            m = merged.setdefault(s["SeriesInstanceUID"], {**s, "found_on": []})
            if not m["SeriesDescription"] and s["SeriesDescription"]:
                m["SeriesDescription"] = s["SeriesDescription"]
            m["found_on"].append(f"{res['pacs']}({s['instances']})")
            if s["StudyInstanceUID"] != m["StudyInstanceUID"]:
                m.setdefault("conflicts", []).append(f"{res['pacs']} says study {s['StudyInstanceUID']}")

    print("Per-PACS:")
    for res in per_pacs:
        print(f"  {res['pacs']:10} studies={res.get('studies')} (from {res['study_responses']} STUDY responses) "
              f"series={len(res['series'])} time={res.get('elapsed_s')}s status={res['study_status']} "
              f"errors={res['errors'] or 'none'}")
    print(f"\nMerged: {len(merged)} unique series from {sum(len(r['series']) for r in per_pacs)} "
          f"per-PACS series rows; wall time {total}s (PACS queried in parallel)\n")
    print(f"{'PatientID':14} {'StudyDate':9} {'Mod':8} {'Ser#':>4}  {'Description':28} {'Found on (instances)':28} SeriesInstanceUID")
    for m in sorted(merged.values(), key=lambda m: (m["PatientID"], m["StudyDate"], str(m["SeriesNumber"]).zfill(6))):
        print(f"{m['PatientID']:14} {m['StudyDate']:9} {m['Modality']:8} {m['SeriesNumber']:>4}  "
              f"{m['SeriesDescription'][:28]:28} {', '.join(m['found_on']):28} {m['SeriesInstanceUID']}")
    if a.json:
        with open(a.json, "w") as fh:
            json.dump({"per_pacs": per_pacs, "merged": list(merged.values()), "wall_s": total}, fh, indent=2)


if __name__ == "__main__":
    main()
