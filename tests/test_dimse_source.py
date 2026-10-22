"""The DIMSE source against a small fake PACS running in the test (pynetdicom)."""

from __future__ import annotations

import socket
import time

import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian
from pynetdicom import AE, evt
from pynetdicom.sop_class import (
    CTImageStorage,
    StudyRootQueryRetrieveInformationModelFind as FIND,
    StudyRootQueryRetrieveInformationModelGet as GET,
    StudyRootQueryRetrieveInformationModelMove as MOVE,
    Verification,
)
from reflex_ddns_auth.collections import Collection

from dicom_data_explorer.services import downloader, receiver, sources
from dicom_data_explorer.services.downloader import DownloadManager
from dicom_data_explorer.services.pacs_config import PacsProfile
from dicom_data_explorer.services.sources import dimse
from dicom_data_explorer.services.sources.base import StudyQuery

STUDY = "1.2.826.0.1.3680043.8.498.1"
SERIES = STUDY + ".1"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ct(n: int) -> Dataset:
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = f"{SERIES}.{n}"
    ds.StudyInstanceUID, ds.SeriesInstanceUID = STUDY, SERIES
    ds.PatientID, ds.PatientName, ds.StudyDate = "P1", "WANG^XIAO", "20260901"
    ds.Modality, ds.SeriesDescription, ds.InstanceNumber = "CT", "Chest", n
    ds.Rows = ds.Columns = 2
    ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 0
    ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
    ds.PixelData = bytes([n]) * 8
    return ds


class FakePacs:
    """FAKE_PACS: one study, one CT series of three images."""

    def __init__(self, receiver_port: int, allow_get: bool = True, knows: str = "DDE_EXPLORER"):
        self.port, self.receiver_port, self.knows = free_port(), receiver_port, knows
        self.images = [ct(n) for n in (1, 2, 3)]
        ae = AE(ae_title="FAKE_PACS")
        ae.require_called_aet = True
        for context in (Verification, FIND, MOVE):
            ae.add_supported_context(context)
        ae.add_requested_context(CTImageStorage, ExplicitVRLittleEndian)  # to send C-MOVE images
        if allow_get:
            ae.add_supported_context(GET)
            ae.add_supported_context(CTImageStorage, scu_role=False, scp_role=True)
        self.server = ae.start_server(
            ("127.0.0.1", self.port),
            block=False,
            evt_handlers=[(evt.EVT_C_FIND, self.on_find), (evt.EVT_C_GET, self.on_get), (evt.EVT_C_MOVE, self.on_move)],
        )

    def _matches(self, ident: Dataset) -> list[Dataset]:
        study = str(ident.get("StudyInstanceUID", "") or "")
        series = str(ident.get("SeriesInstanceUID", "") or "")
        patient = str(ident.get("PatientID", "") or "")
        return [
            ds for ds in self.images
            if (not study or study == STUDY) and (not series or series == SERIES) and (not patient or patient == "P1")
        ]

    def on_find(self, event):
        ident = event.identifier
        matches = self._matches(ident)
        level = ident.QueryRetrieveLevel
        if level == "IMAGE":
            rows = matches
        else:
            rows = matches[:1]
        for ds in rows:
            answer = Dataset()
            answer.QueryRetrieveLevel = level
            answer.StudyInstanceUID = STUDY
            if level == "STUDY":
                answer.PatientID, answer.PatientName, answer.StudyDate = "P1", "WANG^XIAO", "20260901"
                answer.ModalitiesInStudy = "CT"
                answer.NumberOfStudyRelatedInstances = len(self.images)
            if level in ("SERIES", "IMAGE"):
                answer.SeriesInstanceUID, answer.Modality = SERIES, "CT"
                answer.SeriesDescription = "Chest"
                answer.NumberOfSeriesRelatedInstances = len(self.images)
            if level == "IMAGE":
                answer.SOPInstanceUID, answer.SOPClassUID = ds.SOPInstanceUID, ds.SOPClassUID
            yield 0xFF00, answer

    def on_get(self, event):
        matches = self._matches(event.identifier)
        yield len(matches)
        for ds in matches:
            yield 0xFF00, ds

    def on_move(self, event):
        destination = event.move_destination
        destination = destination.decode() if isinstance(destination, bytes) else destination
        if destination.strip() != self.knows:
            yield None, None
            return
        yield "127.0.0.1", self.receiver_port
        matches = self._matches(event.identifier)
        yield len(matches)
        for ds in matches:
            yield 0xFF00, ds

    def stop(self):
        self.server.shutdown()


@pytest.fixture
def rx(monkeypatch):
    our = receiver.Receiver("DDE_EXPLORER", free_port(), "127.0.0.1")
    monkeypatch.setattr(receiver, "_receiver", our)
    yield our
    our.stop()


@pytest.fixture
def make_pacs(rx):
    started = []

    def _make(**kw) -> FakePacs:
        pacs = FakePacs(rx.port, **kw)
        started.append(pacs)
        return pacs

    yield _make
    for pacs in started:
        pacs.stop()


def source_for(pacs: FakePacs, monkeypatch, retrieve: str = "C-GET", called: str = "FAKE_PACS") -> dimse.DIMSESource:
    src = dimse.DIMSESource(PacsProfile(
        id="fake", label="Fake PACS", kind="dimse", host="127.0.0.1", port=pacs.port,
        called_aet=called, calling_aet="DDE_EXPLORER", retrieve=retrieve,
    ))
    monkeypatch.setitem(sources._SOURCES, "fake", src)
    return src


def download(series, tmp_path) -> DownloadManager:
    manager = DownloadManager(
        collection_factory=lambda: Collection("dicom", app_id="test-app", root=tmp_path), retry_delay=0
    )
    manager.add(series)
    deadline = time.time() + 20
    while manager.busy():
        assert time.time() < deadline, "download did not finish"
        time.sleep(0.05)
    return manager


def test_search_lists_studies_series_and_images(make_pacs, monkeypatch):
    src = source_for(make_pacs(), monkeypatch)
    (study,) = src.search(StudyQuery(patient_id="P1")).studies
    assert (study.uid, study.patient_name, study.modalities, study.image_count) == (STUDY, "WANG^XIAO", ["CT"], 3)
    (series,) = src.series_of(STUDY)
    assert (series.uid, series.modality, series.images, series.source) == (SERIES, "CT", 3, "fake")
    files = src.list_files(series, None)
    assert [f.rel for f in files] == [f"{SERIES}.{n}.dcm" for n in (1, 2, 3)]
    assert {f.sop_class for f in files} == {CTImageStorage}
    assert src.find_series([SERIES]) == {}  # level by level: needs its study
    assert list(src.find_series([SERIES], STUDY)) == [SERIES]


@pytest.mark.parametrize("retrieve", ["C-GET", "C-MOVE"])
def test_a_series_downloads_through_the_queue(make_pacs, monkeypatch, tmp_path, retrieve):
    src = source_for(make_pacs(), monkeypatch, retrieve)
    (series,) = src.series_of(STUDY)
    manager = download(series, tmp_path)
    assert [e["outcome"] for e in manager.snapshot()["finished"]] == ["done"]
    (item,) = manager.library()
    assert len(item.files) == 3
    assert item.manifest["source"] == "dimse:fake"
    assert "PatientName" not in item.meta and "PatientID" not in item.meta


def test_a_pacs_that_does_not_know_our_receiver_says_0xa801(make_pacs, monkeypatch, tmp_path):
    src = source_for(make_pacs(knows="SOMEONE_ELSE"), monkeypatch, "C-MOVE")
    (series,) = src.series_of(STUDY)
    (job,) = download(series, tmp_path).snapshot()["jobs"]
    assert job["status"] == downloader.FAILED
    assert "0xA801" in job["error"] and "DDE_EXPLORER" in job["error"]


def test_a_pacs_without_c_get_is_told_apart(make_pacs, monkeypatch, tmp_path):
    src = source_for(make_pacs(allow_get=False), monkeypatch, "C-GET")
    report = dict((text.split(" ")[0], state) for state, text in src.test())
    assert report["C-ECHO"] == "ok" and report["C-GET"] == "fail" and report["C-MOVE:"] == "ok"
    (series,) = src.series_of(STUDY)
    (job,) = download(series, tmp_path).snapshot()["jobs"]
    assert job["status"] == downloader.FAILED
    assert "C-MOVE" in job["error"]


def test_a_wrong_ae_title_is_refused_with_a_reason(make_pacs, monkeypatch):
    src = source_for(make_pacs(), monkeypatch, called="NOT_IT")
    ((state, text),) = src.test()
    assert state == "fail" and "refused the connection" in text and "NOT_IT" in text


def test_nothing_listening_is_unreachable(monkeypatch):
    src = dimse.DIMSESource(PacsProfile(id="x", label="Gone", kind="dimse", host="127.0.0.1", port=free_port(), called_aet="X"))
    ((state, text),) = src.test()
    assert state == "fail" and "Can't reach" in text
