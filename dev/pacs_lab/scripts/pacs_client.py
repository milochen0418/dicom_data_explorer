#!/usr/bin/env python
"""A small DICOM DIMSE client for the PACS lab (pynetdicom + pydicom).

It plays the role of the "explorer" app talking to a PACS:

  echo         C-ECHO  ("DICOM ping")
  find         C-FIND  at STUDY / SERIES / IMAGE level (Study Root model)
  get          C-GET   the PACS sends the files back on the SAME association
  move         C-MOVE  the PACS opens a NEW association to a Storage SCP
                       (the "move destination") and pushes the files there

Examples (PACS A = qrscp on 127.0.0.1:11112, AE title PACS_A):

  py $LAB/scripts/pacs_client.py --port 11112 --aec PACS_A echo
  py $LAB/scripts/pacs_client.py --port 11112 --aec PACS_A find --level STUDY --patient-id KiTS-00061
  py $LAB/scripts/pacs_client.py --port 11112 --aec PACS_A find --level STUDY --study-date 20000101-20101231
  py $LAB/scripts/pacs_client.py --port 11112 --aec PACS_A find --level SERIES --study-uid <uid>
  py $LAB/scripts/pacs_client.py --port 11112 --aec PACS_A get  --study-uid <uid> --series-uid <uid> --out received/get_kits_ct
  py $LAB/scripts/pacs_client.py --port 11112 --aec PACS_A move --study-uid <uid> --series-uid <uid> --dest EXPLORER

(py, $LAB and the working folder are set up as in dev/pacs_lab/README.md)

The functions c_echo / c_find / c_get / c_move are imported by the other lab
scripts (negative_tests.py, multi_pacs.py).
"""
import argparse
import json
import logging
import os
import sys
import time

from pydicom.dataset import Dataset
from pydicom.uid import (
    UID,
    DeflatedExplicitVRLittleEndian,
    ExplicitVRBigEndian,
    ExplicitVRLittleEndian,
    ImplicitVRLittleEndian,
    JPEG2000,
    JPEG2000Lossless,
    JPEGBaseline8Bit,
    JPEGExtended12Bit,
    JPEGLossless,
    JPEGLosslessSV1,
    RLELossless,
)
from pynetdicom import AE, build_role, evt
from pynetdicom import sop_class as sc
from pynetdicom.status import (
    QR_FIND_SERVICE_CLASS_STATUS,
    QR_GET_SERVICE_CLASS_STATUS,
    QR_MOVE_SERVICE_CLASS_STATUS,
    VERIFICATION_SERVICE_CLASS_STATUS,
)

# Information models (Study Root is what most PACS and viewers use)
MODELS = {
    "study": {
        "find": sc.StudyRootQueryRetrieveInformationModelFind,
        "get": sc.StudyRootQueryRetrieveInformationModelGet,
        "move": sc.StudyRootQueryRetrieveInformationModelMove,
    },
    "patient": {
        "find": sc.PatientRootQueryRetrieveInformationModelFind,
        "get": sc.PatientRootQueryRetrieveInformationModelGet,
        "move": sc.PatientRootQueryRetrieveInformationModelMove,
    },
}

# Storage SOP classes we are willing to RECEIVE during a C-GET. With C-GET the
# client must propose each of these (with the SCP role) up front - the PACS can
# only send an object whose SOP class (+ a transfer syntax) we accepted.
GET_STORAGE_CLASSES = [
    "CTImageStorage", "EnhancedCTImageStorage",
    "MRImageStorage", "EnhancedMRImageStorage",
    "PositronEmissionTomographyImageStorage", "EnhancedPETImageStorage",
    "NuclearMedicineImageStorage",
    "SegmentationStorage",
    "RTStructureSetStorage", "RTDoseStorage", "RTPlanStorage",
    "EnhancedSRStorage", "ComprehensiveSRStorage", "Comprehensive3DSRStorage",
    "BasicTextSRStorage", "KeyObjectSelectionDocumentStorage",
    "GrayscaleSoftcopyPresentationStateStorage",
    "SecondaryCaptureImageStorage",
    "DigitalXRayImageStorageForPresentation", "ComputedRadiographyImageStorage",
    "UltrasoundImageStorage", "UltrasoundMultiFrameImageStorage",
    "XRayAngiographicImageStorage",
    "DigitalMammographyXRayImageStorageForPresentation",
]
# Newer SOP class (2024+) not named in pydicom 3.0.2 - add by UID
LABEL_MAP_SEGMENTATION_STORAGE = "1.2.840.10008.5.1.4.1.1.66.7"

GET_TRANSFER_SYNTAXES = [
    ExplicitVRLittleEndian, ImplicitVRLittleEndian, DeflatedExplicitVRLittleEndian,
    JPEGBaseline8Bit, JPEGExtended12Bit, JPEGLossless, JPEGLosslessSV1,
    JPEG2000Lossless, JPEG2000, RLELossless, ExplicitVRBigEndian,
]

DEFAULT_RETURN_KEYS = {
    "STUDY": ["PatientID", "PatientName", "StudyInstanceUID", "StudyDate",
              "StudyTime", "AccessionNumber", "StudyID", "StudyDescription",
              "ModalitiesInStudy", "NumberOfStudyRelatedSeries",
              "NumberOfStudyRelatedInstances"],
    "SERIES": ["StudyInstanceUID", "SeriesInstanceUID", "Modality", "SeriesNumber",
               "SeriesDescription", "NumberOfSeriesRelatedInstances"],
    "IMAGE": ["StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID",
              "SOPClassUID", "InstanceNumber"],
}


def dicom_str(value):
    """Multi-valued elements as DICOM writes them: CT\\SEG."""
    if isinstance(value, (list, tuple)) or type(value).__name__ == "MultiValue":
        return "\\".join(str(v) for v in value)
    return str(value)


def status_text(code, table):
    if code is None:
        return "no status (connection lost / timed out)"
    cat, desc = table.get(code, ("Unknown", "not in pynetdicom's table"))
    return f"0x{code:04X} {cat}: {desc}"


def storage_uids(selection="all"):
    """Storage SOP Class UIDs to offer during C-GET."""
    if selection == "none":
        return []
    names = GET_STORAGE_CLASSES if selection == "all" else selection.split(",")
    uids = []
    for name in names:
        uid = getattr(sc, name, None)
        if uid is None:
            raise SystemExit(f"unknown SOP class name: {name}")
        uids.append(uid)
    if selection == "all":
        uids.append(UID(LABEL_MAP_SEGMENTATION_STORAGE))
    return uids


def associate(ae, host, port, aec, **kw):
    """Associate and return (assoc, info). info explains a failure."""
    t0 = time.perf_counter()
    assoc = ae.associate(host, port, ae_title=aec, **kw)
    info = {"assoc_ms": round((time.perf_counter() - t0) * 1000, 1)}
    if assoc.is_established:
        info["established"] = True
        info["accepted_contexts"] = len(assoc.accepted_contexts)
        info["rejected_contexts"] = len(assoc.rejected_contexts)
        return assoc, info
    info["established"] = False
    info["rejected"] = assoc.is_rejected
    info["aborted"] = assoc.is_aborted
    prim = getattr(assoc.acceptor, "primitive", None)
    if assoc.is_rejected and prim is not None:
        info["reject"] = {
            "result": prim.result, "result_str": prim.result_str,
            "source": prim.result_source, "source_str": prim.source_str,
            "reason": prim.diagnostic, "reason_str": prim.reason_str,
        }
    return assoc, info


def make_ae(aet):
    ae = AE(ae_title=aet)
    ae.acse_timeout = 10
    ae.dimse_timeout = 60
    ae.network_timeout = 30
    return ae


# --------------------------------------------------------------------- C-ECHO
def c_echo(host, port, aec, aet="LAB_SCU"):
    ae = make_ae(aet)
    ae.add_requested_context(sc.Verification)
    assoc, info = associate(ae, host, port, aec)
    out = {"op": "C-ECHO", "peer": f"{aec}@{host}:{port}", "calling_aet": aet, **info}
    if assoc.is_established:
        t0 = time.perf_counter()
        status = assoc.send_c_echo()
        out["echo_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        code = status.Status if status else None
        out["status"] = code
        out["status_text"] = status_text(code, VERIFICATION_SERVICE_CLASS_STATUS)
        assoc.release()
    return out


# --------------------------------------------------------------------- C-FIND
def build_identifier(level, **keys):
    """Identifier = the query. A key with a value is a MATCHING key; a key
    with an empty value is a RETURN key ("please send me this")."""
    ds = Dataset()
    ds.QueryRetrieveLevel = level
    wanted = keys.pop("return_keys", None) or DEFAULT_RETURN_KEYS[level]
    for kw in wanted:
        setattr(ds, kw, "")
    for kw, val in keys.items():
        if val is not None:
            setattr(ds, kw, val)
    return ds


def c_find(host, port, aec, identifier, aet="LAB_SCU", model="study", show=True):
    ae = make_ae(aet)
    ae.add_requested_context(MODELS[model]["find"])
    assoc, info = associate(ae, host, port, aec)
    out = {"op": "C-FIND", "peer": f"{aec}@{host}:{port}",
           "level": identifier.QueryRetrieveLevel,
           "query": {e.keyword: str(e.value) for e in identifier if e.value not in (None, "")},
           **info, "pending": 0, "rows": []}
    if not assoc.is_established:
        return out
    t0 = time.perf_counter()
    for status, ident in assoc.send_c_find(identifier, MODELS[model]["find"]):
        code = status.Status if status else None
        if code in (0xFF00, 0xFF01):
            out["pending"] += 1
            out["rows"].append({e.keyword: dicom_str(e.value) for e in ident
                                if e.keyword not in ("QueryRetrieveLevel",)})
        else:
            out["status"] = code
            out["status_text"] = status_text(code, QR_FIND_SERVICE_CLASS_STATUS)
            if status is not None and "ErrorComment" in status:
                out["error_comment"] = str(status.ErrorComment)
            if status is not None and "OffendingElement" in status:
                out["offending"] = [str(t) for t in status.OffendingElement]
    out["find_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    assoc.release()
    # PACS may send one response per matching *instance*; dedupe for display
    for key in ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID"):
        vals = {r.get(key) for r in out["rows"] if r.get(key)}
        if vals:
            out[f"unique_{key}"] = len(vals)
    if show:
        print_find(out)
    return out


def print_find(out):
    print(f"C-FIND {out['level']} on {out['peer']} query={out['query']}")
    if not out.get("established"):
        print(f"  association failed: {out}")
        return
    print(f"  final status: {out.get('status_text')}"
          + (f"  ErrorComment={out['error_comment']!r}" if out.get("error_comment") else ""))
    uniq = {k: v for k, v in out.items() if k.startswith("unique_")}
    print(f"  pending responses (matches): {out['pending']}   {uniq}   "
          f"assoc {out['assoc_ms']} ms, find {out['find_ms']} ms")
    seen = set()
    for r in out["rows"]:
        sig = json.dumps(r, sort_keys=True)
        if sig in seen:
            continue
        seen.add(sig)
        print("   ", {k: v for k, v in r.items() if v not in ("", "None")})
    if len(seen) != len(out["rows"]):
        print(f"  ({len(out['rows'])} responses, {len(seen)} distinct)")


# --------------------------------------------------------------- C-GET / MOVE
def _progress(out, status, every):
    """Collect the sub-operation counters carried by each pending response."""
    c = {
        "remaining": status.get("NumberOfRemainingSuboperations"),
        "completed": status.get("NumberOfCompletedSuboperations"),
        "failed": status.get("NumberOfFailedSuboperations"),
        "warning": status.get("NumberOfWarningSuboperations"),
    }
    out["progress"].append(c)
    n = len(out["progress"])
    if every and (n == 1 or n % every == 0 or c["remaining"] == 0):
        print(f"    pending #{n}: remaining={c['remaining']} completed={c['completed']} "
              f"failed={c['failed']} warning={c['warning']}")


def _final(out, status, table):
    code = status.Status if status else None
    out["status"] = code
    out["status_text"] = status_text(code, table)
    if status is not None:
        for kw, name in (("NumberOfRemainingSuboperations", "remaining"),
                         ("NumberOfCompletedSuboperations", "completed"),
                         ("NumberOfFailedSuboperations", "failed"),
                         ("NumberOfWarningSuboperations", "warning")):
            if kw in status:
                out.setdefault("final_counters", {})[name] = status[kw].value
        if "ErrorComment" in status:
            out["error_comment"] = str(status.ErrorComment)
        if "FailedSOPInstanceUIDList" in status:
            v = status.FailedSOPInstanceUIDList
            uids = [v] if isinstance(v, str) else list(v)
            out["failed_sop_instance_uids"] = {"count": len(uids), "first": uids[:2]}


def retrieve_identifier(study_uid, series_uid=None, sop_uid=None):
    ds = Dataset()
    ds.QueryRetrieveLevel = "IMAGE" if sop_uid else ("SERIES" if series_uid else "STUDY")
    ds.StudyInstanceUID = study_uid
    if series_uid:
        ds.SeriesInstanceUID = series_uid
    if sop_uid:
        ds.SOPInstanceUID = sop_uid
    return ds


def c_get(host, port, aec, identifier, out_dir, aet="LAB_SCU", model="study",
          storage="all", every=10, role=True):
    os.makedirs(out_dir, exist_ok=True)
    received = []

    def on_store(event):
        # Write exactly the bytes we received (no decode / re-encode)
        ds_uid = event.request.AffectedSOPInstanceUID
        path = os.path.join(out_dir, f"{ds_uid}.dcm")
        with open(path, "wb") as fh:
            fh.write(event.encoded_dataset(include_meta=True))
        received.append((ds_uid, str(event.context.transfer_syntax)))
        return 0x0000

    ae = make_ae(aet)
    ae.add_requested_context(MODELS[model]["get"])
    uids = storage_uids(storage)
    for uid in uids:
        ae.add_requested_context(uid, GET_TRANSFER_SYNTAXES)
    ext_neg = [build_role(uid, scp_role=True) for uid in uids] if role else []
    assoc, info = associate(ae, host, port, aec, ext_neg=ext_neg,
                            evt_handlers=[(evt.EVT_C_STORE, on_store)])
    out = {"op": "C-GET", "peer": f"{aec}@{host}:{port}", "out_dir": out_dir,
           "query": {e.keyword: str(e.value) for e in identifier},
           "storage_classes_offered": len(uids), **info, "progress": []}
    if not assoc.is_established:
        return out
    print(f"C-GET {identifier.QueryRetrieveLevel} from {out['peer']} -> {out_dir}")
    t0 = time.perf_counter()
    for status, _ in assoc.send_c_get(identifier, MODELS[model]["get"]):
        code = status.Status if status else None
        if code in (0xFF00, 0xFF01):
            _progress(out, status, every)
        else:
            _final(out, status, QR_GET_SERVICE_CLASS_STATUS)
    out["get_s"] = round(time.perf_counter() - t0, 3)
    assoc.release()
    out["files_written"] = len(received)
    out["transfer_syntaxes_received"] = sorted({UID(t).name for _, t in received})
    out["bytes_written"] = sum(os.path.getsize(os.path.join(out_dir, f"{u}.dcm")) for u, _ in received)
    print(f"  final: {out['status_text']}  counters={out.get('final_counters')}  "
          f"files={out['files_written']}  {out['bytes_written']/1e6:.1f} MB  "
          f"in {out['get_s']} s  TS={out['transfer_syntaxes_received']}")
    return out


def c_move(host, port, aec, identifier, dest, aet="LAB_SCU", model="study", every=10):
    ae = make_ae(aet)
    ae.add_requested_context(MODELS[model]["move"])
    assoc, info = associate(ae, host, port, aec)
    out = {"op": "C-MOVE", "peer": f"{aec}@{host}:{port}", "move_destination": dest,
           "query": {e.keyword: str(e.value) for e in identifier}, **info, "progress": []}
    if not assoc.is_established:
        return out
    print(f"C-MOVE {identifier.QueryRetrieveLevel} on {out['peer']} -> destination AE {dest!r}")
    t0 = time.perf_counter()
    for status, _ in assoc.send_c_move(identifier, dest, MODELS[model]["move"]):
        code = status.Status if status else None
        if code in (0xFF00, 0xFF01):
            _progress(out, status, every)
        else:
            _final(out, status, QR_MOVE_SERVICE_CLASS_STATUS)
    out["move_s"] = round(time.perf_counter() - t0, 3)
    assoc.release()
    print(f"  final: {out['status_text']}  counters={out.get('final_counters')}  in {out['move_s']} s")
    return out


# ------------------------------------------------------------------------ CLI
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=11112)
    ap.add_argument("--aec", default="PACS_A", help="called AE title (the PACS)")
    ap.add_argument("--aet", default="LAB_SCU", help="calling AE title (us)")
    ap.add_argument("--model", choices=["study", "patient"], default="study")
    ap.add_argument("--json", help="also write the result as JSON here")
    ap.add_argument("--debug", action="store_true", help="pynetdicom debug log")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("echo")
    f = sub.add_parser("find")
    f.add_argument("--level", choices=["STUDY", "SERIES", "IMAGE"], default="STUDY")
    f.add_argument("--patient-id")
    f.add_argument("--patient-name")
    f.add_argument("--study-date", help="YYYYMMDD or range YYYYMMDD-YYYYMMDD")
    f.add_argument("--modalities-in-study", help="e.g. CT or CT\\SEG")
    f.add_argument("--study-uid")
    f.add_argument("--series-uid")
    f.add_argument("--modality")
    f.add_argument("--return-keys", help="comma separated keywords")
    for name in ("get", "move"):
        g = sub.add_parser(name)
        g.add_argument("--study-uid", required=True)
        g.add_argument("--series-uid")
        g.add_argument("--every", type=int, default=10, help="print every n-th pending response")
        if name == "get":
            g.add_argument("--out", required=True)
            g.add_argument("--storage", default="all",
                           help="'all', 'none', or comma list like CTImageStorage")
        else:
            g.add_argument("--dest", default="EXPLORER", help="move destination AE title")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.debug else logging.WARNING,
                        format="%(levelname).1s: %(message)s")
    if a.cmd == "echo":
        res = c_echo(a.host, a.port, a.aec, a.aet)
        print(json.dumps(res, indent=2))
    elif a.cmd == "find":
        keys = {
            "PatientID": a.patient_id, "PatientName": a.patient_name,
            "StudyDate": a.study_date, "ModalitiesInStudy": a.modalities_in_study,
            "StudyInstanceUID": a.study_uid, "SeriesInstanceUID": a.series_uid,
            "Modality": a.modality,
        }
        if a.return_keys:
            keys["return_keys"] = a.return_keys.split(",")
        res = c_find(a.host, a.port, a.aec, build_identifier(a.level, **keys), a.aet, a.model)
    elif a.cmd == "get":
        res = c_get(a.host, a.port, a.aec, retrieve_identifier(a.study_uid, a.series_uid),
                    a.out, a.aet, a.model, a.storage, a.every)
    else:
        res = c_move(a.host, a.port, a.aec, retrieve_identifier(a.study_uid, a.series_uid),
                     a.dest, a.aet, a.model, a.every)
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(res, fh, indent=2, default=str)


if __name__ == "__main__":
    main()
