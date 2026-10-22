#!/usr/bin/env python
"""mini_pacs.py - a small fake PACS built directly on pynetdicom.

Written because the bundled `qrscp` app falls short as a PACS stand-in:
  * qrscp answers one C-FIND response per stored *instance* (a 29-slice
    series shows up 29 times at SERIES level);
  * it drops every optional key (ModalitiesInStudy, StudyDescription,
    SeriesDescription, NumberOf...RelatedInstances, SOPClassUID ...), so you
    cannot filter studies by modality;
  * it accepts any called AE title;
  * its wildcard matching goes through SQL LIKE without escaping, so '_' in a
    query also acts as a wildcard.

What this one does:
  C-ECHO; C-STORE (keeps files in --store, indexed in memory, re-indexed at
  start); C-FIND / C-GET / C-MOVE with the Study Root model at STUDY, SERIES
  and IMAGE level; one response per study/series/instance; computed
  ModalitiesInStudy, NumberOfStudyRelatedSeries/Instances and
  NumberOfSeriesRelatedInstances; DICOM wildcard (* ?), list-of-UID and
  date/time range matching; PN matching case-insensitive; rejects
  associations whose called AE title is not ours.

Usage:
  py $LAB/scripts/mini_pacs.py --aet MINI_PACS --port 11115 \
      --store mini_pacs --dest EXPLORER=127.0.0.1:11114 [--dest OTHER=host:port]
"""
import argparse
import logging
import os
import re
import threading
from collections import defaultdict

from pydicom import dcmread
from pydicom.dataset import Dataset
from pydicom.multival import MultiValue
from pydicom.uid import (
    DeflatedExplicitVRLittleEndian, ExplicitVRLittleEndian, ImplicitVRLittleEndian,
)
from pynetdicom import ALL_TRANSFER_SYNTAXES, AE, AllStoragePresentationContexts, build_context, evt
from pynetdicom.sop_class import (
    StudyRootQueryRetrieveInformationModelFind as FIND,
    StudyRootQueryRetrieveInformationModelGet as GET,
    StudyRootQueryRetrieveInformationModelMove as MOVE,
    Verification,
)

LOG = logging.getLogger("mini_pacs")

# Attributes we index, per level (anything else in a query is returned empty)
LEVEL_KEYS = {
    "STUDY": ["StudyInstanceUID", "PatientID", "PatientName", "PatientBirthDate",
              "PatientSex", "StudyDate", "StudyTime", "AccessionNumber", "StudyID",
              "StudyDescription", "ReferringPhysicianName"],
    "SERIES": ["SeriesInstanceUID", "Modality", "SeriesNumber", "SeriesDescription",
               "SeriesDate", "BodyPartExamined"],
    "IMAGE": ["SOPInstanceUID", "SOPClassUID", "InstanceNumber", "Rows", "Columns",
              "NumberOfFrames"],
}
LEVELS = ["STUDY", "SERIES", "IMAGE"]
UNIQUE = {"STUDY": "StudyInstanceUID", "SERIES": "SeriesInstanceUID", "IMAGE": "SOPInstanceUID"}
COMPUTED = {"STUDY": ["ModalitiesInStudy", "NumberOfStudyRelatedSeries",
                      "NumberOfStudyRelatedInstances"],
            "SERIES": ["NumberOfSeriesRelatedInstances"], "IMAGE": []}
UID_KEYS = {"StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID", "SOPClassUID"}
RANGE_KEYS = {"StudyDate", "StudyTime", "SeriesDate", "PatientBirthDate"}
# Prefer Explicit VR Little Endian when a sender offers several, so files keep
# their VRs (the order of the acceptor's list decides in pynetdicom).
TS_PREF = [ExplicitVRLittleEndian, ImplicitVRLittleEndian, DeflatedExplicitVRLittleEndian] + [
    t for t in ALL_TRANSFER_SYNTAXES
    if t not in (ExplicitVRLittleEndian, ImplicitVRLittleEndian, DeflatedExplicitVRLittleEndian)]


class BadIdentifier(Exception):
    pass


class Index:
    """In-memory index: SOPInstanceUID -> record (path, ts, attrs)."""

    def __init__(self, folder):
        self.folder = folder
        self.lock = threading.Lock()
        self.recs = {}
        os.makedirs(folder, exist_ok=True)
        for name in sorted(os.listdir(folder)):
            if name.endswith(".dcm"):
                self.add(os.path.join(folder, name))
        LOG.info(f"indexed {len(self.recs)} instances from {folder}")

    def add(self, path):
        ds = dcmread(path, stop_before_pixels=True)
        attrs = {}
        for kws in LEVEL_KEYS.values():
            for kw in kws:
                v = ds.get(kw)          # keyword -> value (not the element)
                attrs[kw] = "" if v is None else str(v)
        rec = {"path": path, "ts": str(ds.file_meta.TransferSyntaxUID),
               "sop_class": str(ds.SOPClassUID), "attrs": attrs}
        with self.lock:
            self.recs[attrs["SOPInstanceUID"]] = rec

    def snapshot(self):
        with self.lock:
            return list(self.recs.values())


def as_list(value):
    if value is None or value == "":
        return []
    if isinstance(value, (MultiValue, list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def wildcard_regex(pattern, ignore_case):
    # DICOM PS3.4 C.2.2.2.4: '*' any sequence, '?' one character, all else literal
    rx = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern)
    return re.compile(rf"^{rx}$", re.IGNORECASE if ignore_case else 0)


def value_matches(kw, query, stored):
    """Match one key. query is the request value, stored the indexed string."""
    if kw == "ModalitiesInStudy":                       # multi-valued: any overlap
        return bool(set(as_list(query)) & set(stored))
    if kw in UID_KEYS:                                  # list of UID matching
        return stored in as_list(query)
    q = str(query)
    if kw in RANGE_KEYS and "-" in q:                   # range matching
        lo, hi = q.split("-", 1)
        s = stored[: len(lo or hi)] if stored else ""
        return bool(stored) and (not lo or s >= lo) and (not hi or stored[: len(hi)] <= hi)
    ignore_case = kw.endswith("Name")                   # PN: case-insensitive
    if "*" in q or "?" in q:
        return bool(wildcard_regex(q, ignore_case).match(stored))
    return stored.lower() == q.lower() if ignore_case else stored == q


def check_identifier(ident, retrieve=False):
    level = ident.get("QueryRetrieveLevel")
    if level not in LEVELS:
        raise BadIdentifier(f"QueryRetrieveLevel {level!r} not one of {LEVELS}")
    # Hierarchical model: every level ABOVE the requested one needs its unique
    # key with a value (PS3.4 C.4.1.2.1 / C.4.2.2.1)
    for upper in LEVELS[: LEVELS.index(level)]:
        if not as_list(ident.get(UNIQUE[upper])):
            raise BadIdentifier(f"{level}-level request needs a {UNIQUE[upper]} value")
    if retrieve and not as_list(ident.get(UNIQUE[level])):
        raise BadIdentifier(f"retrieve at {level} level needs {UNIQUE[level]}")
    return level


def find_entities(index, ident):
    level = check_identifier(ident)
    keys = [e.keyword for e in ident if e.keyword and e.keyword != "QueryRetrieveLevel"]
    groups = defaultdict(list)
    for rec in index.snapshot():
        groups[rec["attrs"][UNIQUE[level]]].append(rec)
    out = []
    for uid, recs in groups.items():
        rep = dict(recs[0]["attrs"])
        rep["ModalitiesInStudy"] = sorted({r["attrs"]["Modality"] for r in recs if r["attrs"]["Modality"]})
        rep["NumberOfStudyRelatedSeries"] = str(len({r["attrs"]["SeriesInstanceUID"] for r in recs}))
        rep["NumberOfStudyRelatedInstances"] = str(len(recs))
        rep["NumberOfSeriesRelatedInstances"] = str(len(recs))
        allowed = sum((LEVEL_KEYS[lv] + COMPUTED[lv] for lv in LEVELS[: LEVELS.index(level) + 1]), [])
        ok = True
        for kw in keys:
            q = ident[kw].value
            if q in (None, "") or (isinstance(q, MultiValue) and len(q) == 0):
                continue                                # return key only
            if kw not in allowed:
                continue                                # unsupported key: ignore
            if not value_matches(kw, q, rep[kw]):
                ok = False
                break
        if ok:
            rsp = Dataset()
            rsp.QueryRetrieveLevel = level
            for kw in keys:
                if kw in allowed:
                    val = rep[kw]
                    setattr(rsp, kw, val if val != "" else None)
                else:
                    setattr(rsp, kw, None)
            out.append((rep, rsp))
    sort_key = {"STUDY": ("StudyDate", "StudyInstanceUID"),
                "SERIES": ("SeriesNumber", "SeriesInstanceUID"),
                "IMAGE": ("InstanceNumber", "SOPInstanceUID")}[level]
    out.sort(key=lambda t: tuple(str(t[0].get(k, "")).zfill(12) for k in sort_key))
    return [rsp for _, rsp in out]


def select_instances(index, ident):
    level = check_identifier(ident, retrieve=True)
    st, se, so = (as_list(ident.get(k)) for k in UNIQUE.values())
    return [r for r in index.snapshot()
            if r["attrs"]["StudyInstanceUID"] in st
            and (level == "STUDY" or r["attrs"]["SeriesInstanceUID"] in se)
            and (level != "IMAGE" or r["attrs"]["SOPInstanceUID"] in so)]


def main():
    ap = argparse.ArgumentParser(description="small fake PACS on pynetdicom")
    ap.add_argument("--aet", default="MINI_PACS")
    ap.add_argument("--port", type=int, default=11115)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--store", required=True, help="folder for received/held files")
    ap.add_argument("--dest", action="append", default=[], help="AET=host:port (C-MOVE destination)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname).1s %(message)s")
    index = Index(os.path.abspath(a.store))
    dests = {}
    for d in a.dest:
        aet, hp = d.split("=", 1)
        host, port = hp.rsplit(":", 1)
        dests[aet] = (host, int(port))
    LOG.info(f"AE {a.aet} on {a.bind}:{a.port}, move destinations {dests}")

    def on_store(event):
        uid = event.request.AffectedSOPInstanceUID
        path = os.path.join(index.folder, f"{uid}.dcm")
        with open(path, "wb") as fh:
            fh.write(event.encoded_dataset(include_meta=True))
        index.add(path)
        return 0x0000

    def on_find(event):
        try:
            matches = find_entities(index, event.identifier)
        except BadIdentifier as exc:
            LOG.warning(f"C-FIND rejected: {exc}")
            st = Dataset()
            st.Status = 0xA900
            st.ErrorComment = str(exc)[:64]
            yield st, None
            return
        LOG.info(f"C-FIND {event.identifier.QueryRetrieveLevel}: {len(matches)} matches")
        for rsp in matches:
            if event.is_cancelled:
                yield 0xFE00, None
                return
            rsp.RetrieveAETitle = a.aet
            yield 0xFF00, rsp

    def _retrieve(event, kind):
        try:
            recs = select_instances(index, event.identifier)
        except BadIdentifier as exc:
            LOG.warning(f"{kind} rejected: {exc}")
            st = Dataset()
            st.Status = 0xA900
            st.ErrorComment = str(exc)[:64]
            return None, st
        LOG.info(f"{kind} {event.identifier.QueryRetrieveLevel}: {len(recs)} instances")
        return recs, None

    # pynetdicom expects a C-GET handler to yield the number of sub-operations
    # FIRST (a status yielded first becomes 0xC413) and a C-MOVE handler to
    # yield the destination first (a status there becomes 0xA801). To report
    # 0xA900 for a bad identifier we announce 1 sub-operation, then fail it.
    def on_get(event):
        recs, err = _retrieve(event, "C-GET")
        if err is not None:
            yield 1
            yield err, None
            return
        yield len(recs)
        for r in recs:
            if event.is_cancelled:
                yield 0xFE00, None
                return
            yield 0xFF00, dcmread(r["path"])

    def on_move(event):
        dest = dests.get(event.move_destination.strip() if isinstance(event.move_destination, str)
                         else event.move_destination.decode().strip())
        if dest is None:
            LOG.warning(f"C-MOVE unknown destination {event.move_destination!r}")
            yield None, None
            return
        recs, err = _retrieve(event, "C-MOVE")
        if err is not None:
            # pynetdicom still opens the sub-association before reading our
            # status, so it needs at least one context to propose
            yield dest[0], dest[1], {"contexts": [build_context(Verification)]}
            yield 1
            yield err, None
            return
        contexts = sorted({(r["sop_class"], r["ts"]) for r in recs})
        yield dest[0], dest[1], {"contexts": [build_context(c, t) for c, t in contexts][:128]}
        yield len(recs)
        for r in recs:
            if event.is_cancelled:
                yield 0xFE00, None
                return
            yield 0xFF00, dcmread(r["path"])

    ae = AE(ae_title=a.aet)
    ae.require_called_aet = True          # reject associations meant for another AE
    ae.add_supported_context(Verification)
    for cx in AllStoragePresentationContexts:
        ae.add_supported_context(cx.abstract_syntax, TS_PREF, scu_role=False, scp_role=True)
    for model in (FIND, GET, MOVE):
        ae.add_supported_context(model)
    ae.start_server((a.bind, a.port), block=True, evt_handlers=[
        (evt.EVT_C_STORE, on_store), (evt.EVT_C_FIND, on_find),
        (evt.EVT_C_GET, on_get), (evt.EVT_C_MOVE, on_move)])


if __name__ == "__main__":
    main()
