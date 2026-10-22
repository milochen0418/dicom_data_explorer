#!/usr/bin/env python
"""DICOMweb (QIDO-RS search + WADO-RS retrieve) against a PUBLIC demo server.

READ-ONLY: this script only sends HTTP GET requests. It never uploads (no
STOW-RS, no POST/PUT/DELETE).

Usage:
  py $LAB/scripts/dicomweb_demo.py \
      [--root https://orthanc.uclouvain.be/demo/dicom-web] [--out DIR]   (default: $PACS_LAB_HOME/received/dicomweb)

Steps:
  1. QIDO-RS  GET {root}/studies?limit=..&includefield=..     (search studies)
  2. QIDO-RS  GET {root}/studies/{study}/series                (series of a study)
  3. QIDO-RS  GET {root}/studies/{s}/series/{se}/instances?limit=2&offset=0 / 2
  4. WADO-RS  GET {root}/studies/{s}/series/{se}/instances/{i}/metadata
  5. WADO-RS  GET {root}/studies/{s}/series/{se}/instances/{i}
              Accept: multipart/related; type="application/dicom"
  6. The same search/retrieve through the dicomweb-client library
"""
import argparse
import email.parser
import email.policy
import hashlib
import json
import os
import time
from io import BytesIO

import requests
from pydicom import dcmread

HDRS = ("Content-Type", "Content-Length", "Transfer-Encoding", "Server",
        "Access-Control-Allow-Origin", "Cache-Control", "ETag", "Content-Encoding")


def tag(js, t, default=""):
    v = js.get(t, {}).get("Value")
    if not v:
        return default
    v = v[0]
    return v.get("Alphabetic", "") if isinstance(v, dict) else v


def get(session, url, accept, log, label, params=None):
    t0 = time.perf_counter()
    r = session.get(url, headers={"Accept": accept}, params=params, timeout=60)
    ms = round((time.perf_counter() - t0) * 1000)
    entry = {"step": label, "url": r.url, "accept": accept, "status": r.status_code,
             "ms": ms, "bytes": len(r.content),
             "headers": {h: r.headers[h] for h in HDRS if h in r.headers}}
    log.append(entry)
    print(f"\n[{label}] GET {r.url}\n  Accept: {accept}\n  -> HTTP {r.status_code} in {ms} ms, "
          f"{len(r.content)} bytes\n  headers: {entry['headers']}")
    r.raise_for_status()
    return r


def split_multipart(resp):
    """Return the parts (bytes) of a multipart/related response."""
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + resp.headers["Content-Type"].encode() + b"\r\n\r\n" + resp.content)
    return [(p.get_content_type(), dict(p.items()), p.get_payload(decode=True))
            for p in msg.iter_parts()]


def main():
    work = os.environ.get("PACS_LAB_HOME", os.path.expanduser("~/.cache/dicom_data_explorer/pacs_lab"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="https://orthanc.uclouvain.be/demo/dicom-web")
    ap.add_argument("--out", default=os.path.join(work, "received", "dicomweb"))
    ap.add_argument("--json", default=os.path.join(work, "out", "dicomweb_log.json"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
    root = a.root.rstrip("/")
    s = requests.Session()
    s.headers["User-Agent"] = "pacs-lab-readonly/0.1 (+requests)"
    log = []

    # 1. QIDO-RS studies
    r = get(s, f"{root}/studies", "application/dicom+json", log, "QIDO studies",
            params={"limit": "10", "includefield": "ModalitiesInStudy,NumberOfStudyRelatedInstances,StudyDescription"})
    studies = r.json()
    print(f"  {len(studies)} studies:")
    for st in studies:
        print(f"   PatientID={tag(st, '00100020')!r:22} StudyDate={tag(st, '00080020')} "
              f"Modalities={'/'.join(st.get('00080061', {}).get('Value', []))!r:12} "
              f"N={tag(st, '00201208')!s:>5} desc={str(tag(st, '00081030'))[:30]!r}")

    # Pick a study containing CT or MR with a modest number of instances
    def n_inst(st):
        try:
            return int(tag(st, "00201208", 10 ** 9))
        except ValueError:
            return 10 ** 9
    candidates = [st for st in studies if set(st.get("00080061", {}).get("Value", [])) & {"CT", "MR"}]
    study = min(candidates or studies, key=n_inst)
    st_uid = tag(study, "0020000D")
    print(f"\n  chosen study {st_uid} (PatientID {tag(study, '00100020')!r}, {n_inst(study)} instances)")

    # 1b. QIDO-RS with a matching key (PatientID of the chosen study)
    get(s, f"{root}/studies", "application/dicom+json", log, "QIDO studies?PatientID=",
        params={"PatientID": tag(study, "00100020")})

    # 2. QIDO-RS series of that study
    r = get(s, f"{root}/studies/{st_uid}/series", "application/dicom+json", log, "QIDO series")
    series = r.json()
    for se in series:
        print(f"   series Modality={tag(se, '00080060'):4} N={tag(se, '00201209')!s:>4} "
              f"desc={str(tag(se, '0008103E'))[:30]!r} uid={tag(se, '0020000E')}")
    img_series = [se for se in series if tag(se, "00080060") in ("CT", "MR")] or series
    se = min(img_series, key=lambda x: int(tag(x, "00201209", 10 ** 9) or 10 ** 9))
    se_uid = tag(se, "0020000E")

    # 3. QIDO-RS instances with paging
    pages = []
    for offset in (0, 2):
        r = get(s, f"{root}/studies/{st_uid}/series/{se_uid}/instances", "application/dicom+json",
                log, f"QIDO instances offset={offset}", params={"limit": "2", "offset": str(offset)})
        pages.append([tag(i, "00080018") for i in r.json()])
        print(f"   page offset={offset}: {pages[-1]}")
    print(f"   pages overlap: {bool(set(pages[0]) & set(pages[1]))}")
    sop_uid = pages[0][0]
    base = f"{root}/studies/{st_uid}/series/{se_uid}/instances/{sop_uid}"

    # 4. WADO-RS metadata
    r = get(s, f"{base}/metadata", "application/dicom+json", log, "WADO metadata")
    meta = r.json()[0]
    print(f"   metadata: {len(meta)} attributes, Rows={tag(meta, '00280010')} Columns={tag(meta, '00280011')} "
          f"BulkDataURI(PixelData)={meta.get('7FE00010', {}).get('BulkDataURI', '-')}")

    # 5. WADO-RS instance (multipart/related; type="application/dicom")
    r = get(s, base, 'multipart/related; type="application/dicom"', log, "WADO instance")
    parts = split_multipart(r)
    ctype, phdrs, payload = parts[0]
    path = os.path.join(a.out, f"{sop_uid}.dcm")
    with open(path, "wb") as fh:
        fh.write(payload)
    ds = dcmread(path)
    res5 = {"parts": len(parts), "part_content_type": ctype, "part_headers": phdrs,
            "saved": path, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
            "SOPInstanceUID_matches": ds.SOPInstanceUID == sop_uid,
            "TransferSyntax": ds.file_meta.TransferSyntaxUID.name,
            "Modality": ds.get("Modality"), "Rows": ds.get("Rows")}
    log[-1]["result"] = res5
    print(f"   multipart parts={len(parts)}, part Content-Type={ctype}, part headers={phdrs}\n"
          f"   saved {len(payload)} bytes -> {path}; SOPInstanceUID matches={res5['SOPInstanceUID_matches']}, "
          f"TS={res5['TransferSyntax']}")

    # 6. Same thing through dicomweb-client
    from dicomweb_client.api import DICOMwebClient
    client = DICOMwebClient(url=root)
    t0 = time.perf_counter()
    found = client.search_for_studies(limit=3)
    ser = client.search_for_series(study_instance_uid=st_uid)
    insts = client.search_for_instances(study_instance_uid=st_uid, series_instance_uid=se_uid,
                                        limit=2, offset=2)
    md = client.retrieve_instance_metadata(st_uid, se_uid, sop_uid)
    inst = client.retrieve_instance(st_uid, se_uid, sop_uid)
    ms = round((time.perf_counter() - t0) * 1000)
    res6 = {"studies": len(found), "series": len(ser), "instances_page": len(insts),
            "metadata_attrs": len(md), "retrieved_SOPInstanceUID": str(inst.SOPInstanceUID),
            "retrieved_TS": inst.file_meta.TransferSyntaxUID.name, "total_ms": ms}
    log.append({"step": "dicomweb-client", "result": res6})
    print(f"\n[dicomweb-client] {res6}")

    with open(a.json, "w") as fh:
        json.dump(log, fh, indent=2, default=str)
    print(f"\nlog written to {a.json}")


if __name__ == "__main__":
    main()
