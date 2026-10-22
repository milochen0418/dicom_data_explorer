"""The DICOMweb source against a fake QIDO-RS / WADO-RS server."""

from __future__ import annotations

import time
from urllib.parse import urlparse

import pytest
import requests
from reflex_ddns_auth.collections import Collection

from dicom_data_explorer.services import downloader, sources
from dicom_data_explorer.services.downloader import DownloadManager
from dicom_data_explorer.services.pacs_config import PacsProfile
from dicom_data_explorer.services.sources import dicomweb
from dicom_data_explorer.services.sources.base import StudyQuery

ROOT = "http://pacs.test/dicom-web"
STUDY, SERIES = "1.2.3", "1.2.3.4"
FILES = {f"1.2.3.4.{n}": bytes([n]) * (100 + n) for n in range(1, 4)}  # SOPInstanceUID -> DICOM bytes


def tag(value, vr="UI"):
    return {"vr": vr, "Value": [value]}


class Response:
    def __init__(self, status=200, json_body=None, body=b"", headers=None):
        self.status_code = status
        self._json = json_body
        self.content = body if json_body is None else b"[...]"
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class FakeServer:
    """Answers like a small DICOMweb server; records what it was asked."""

    def __init__(self):
        self.calls: list[tuple[str, dict, dict]] = []
        self.studies = [
            {"0020000D": tag(STUDY), "00100020": tag("P1", "LO"), "00100010": {"vr": "PN", "Value": [{"Alphabetic": "WANG^XIAO"}]},
             "00080020": tag("20260901", "DA"), "00080061": {"vr": "CS", "Value": ["CT", "SEG"]}, "00201208": tag(3, "IS")},
        ]

    def session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, params=None, headers=None, auth=None, verify=None, timeout=None):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        path = urlparse(url).path.replace("/dicom-web", "")
        if path == "/studies":
            return Response(json_body=self.studies)
        if path in (f"/studies/{STUDY}/series", "/series"):
            row = {"0020000E": tag(SERIES), "0020000D": tag(STUDY), "00080060": tag("CT", "CS"), "00201209": tag(3, "IS")}
            return Response(json_body=[row])
        if path == f"/studies/{STUDY}/series/{SERIES}/instances":
            offset, limit = int(params["offset"]), int(params["limit"])
            rows = [{"00080018": tag(uid), "00080016": tag("1.2.840.10008.5.1.4.1.1.2")} for uid in FILES]
            return Response(json_body=rows[offset:offset + limit])
        sop = path.rsplit("/", 1)[-1]
        boundary = "b0und"
        body = (
            f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n".encode()
            + FILES[sop]
            + f"\r\n--{boundary}--\r\n".encode()
        )
        return Response(body=body, headers={"Content-Type": f'multipart/related; type="application/dicom"; boundary="{boundary}"'})


@pytest.fixture
def server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr(dicomweb.requests, "Session", fake.session)
    return fake


@pytest.fixture
def source(monkeypatch):
    src = dicomweb.DICOMwebSource(PacsProfile(id="web", label="Web PACS", url=ROOT, auth="bearer", token="t0k"))
    monkeypatch.setitem(sources._SOURCES, "web", src)
    return src


def test_multipart_answers_are_split_into_their_parts():
    body = b"--x\r\nContent-Type: application/dicom\r\n\r\nAB\r\n--x\r\n\r\nC\r\n--x--\r\n"
    assert dicomweb.multipart_parts(body, 'multipart/related; type="application/dicom"; boundary=x') == [b"AB", b"C"]
    assert dicomweb.multipart_parts(b"DICM", "application/dicom") == [b"DICM"]


def test_search_sends_the_form_and_reads_the_studies(server, source):
    result = source.search(StudyQuery(patient_id="P1", date_from="20260901", date_to="20260930", modality="CT"))
    (study,) = result.studies
    assert (study.uid, study.patient_name, study.modalities, study.image_count) == (STUDY, "WANG^XIAO", ["CT", "SEG"], 3)
    url, params, headers = server.calls[-1]
    assert params["PatientID"] == "P1" and params["StudyDate"] == "20260901-20260930"
    assert params["ModalitiesInStudy"] == "CT" and params["limit"] == "201"
    assert headers["Authorization"] == "Bearer t0k"
    assert not result.capped


def test_a_search_that_hits_the_cap_says_so(server, source):
    source.profile.max_results = 1
    server.studies = server.studies * 2
    assert source.search(StudyQuery(patient_id="P1")).capped


def test_a_series_is_found_without_its_study(server, source):
    found = source.find_series([SERIES])
    assert found[SERIES].study_uid == STUDY
    assert server.calls[-1][0].endswith("/series")


def test_instances_are_listed_page_by_page_and_downloaded(server, source, tmp_path, monkeypatch):
    monkeypatch.setattr(dicomweb, "PAGE", 2)
    (series,) = source.series_of(STUDY)
    manager = DownloadManager(
        collection_factory=lambda: Collection("dicom", app_id="test-app", root=tmp_path),
        session_factory=server.session,
        retry_delay=0,
    )
    assert manager.add(series) == "queued"
    while manager.busy():
        time.sleep(0.02)
    (item,) = manager.library()
    assert {f["path"] for f in item.files} == {f"{uid}.dcm" for uid in FILES}
    assert all((item.path / f"{uid}.dcm").read_bytes() == data for uid, data in FILES.items())
    assert item.manifest["source"] == "dicomweb:web"
    pages = [p for url, p, _ in server.calls if url.endswith("/instances")]
    assert [p["offset"] for p in pages] == ["0", "2"]


def test_errors_are_told_in_words(source):
    resp = Response(status=401)
    assert "refused the login" in source.describe_error(requests.HTTPError(response=resp))
    assert source.is_transient(requests.HTTPError(response=Response(status=503)))
    assert not source.is_transient(requests.HTTPError(response=Response(status=404)))


def test_test_connection_reports_what_works(server, source):
    assert source.test()[0][0] == "ok"
