"""Data sources: a second source plugs into the same queue and the same lookup."""

from __future__ import annotations

import contextlib
import time

import pytest
from reflex_ddns_auth.collections import Collection

from dicom_data_explorer.services import downloader, sources
from dicom_data_explorer.services.downloader import DownloadManager, Series
from dicom_data_explorer.services.sources.base import RemoteFile, Source


class FakeSource(Source):
    """A source that keeps its series in memory, the way a PACS source would answer."""

    label = "Fake PACS"

    def __init__(self, source_id: str, files: dict[str, bytes], known: set[str]):
        self.id = source_id
        self.files = files
        self.known = known
        self.asked: list[list[str]] = []

    def find_series(self, uids, study_uid=""):
        self.asked.append(list(uids))
        return {uid: Series(uid=uid, aws_url="", source=self.id) for uid in uids if uid in self.known}

    def list_files(self, series, session):
        return [RemoteFile(rel=name, size=len(data), ref=name) for name, data in self.files.items()]

    @contextlib.contextmanager
    def open_file(self, remote, session):
        yield [self.files[remote.ref]]

    def origin(self, series):
        return f"{self.id}:{series.uid}"


def wait_idle(manager: DownloadManager, timeout: float = 10) -> None:
    deadline = time.time() + timeout
    while manager.busy():
        assert time.time() < deadline, "download did not finish"
        time.sleep(0.02)


@pytest.fixture
def manager(tmp_path):
    return DownloadManager(
        collection_factory=lambda: Collection("dicom", app_id="test-app", root=tmp_path),
        parallel=2,
        session_factory=lambda: None,
        retry_delay=0,
    )


def test_index_rows_are_idc_series_and_keep_their_study():
    s = Series.from_index({"SeriesInstanceUID": "1.2", "StudyInstanceUID": "1.1", "series_aws_url": "s3://b/p/*"})
    assert (s.source, s.study_uid) == ("idc", "1.1")
    assert sources.get("idc").origin(s) == "s3://b/p/*"


def test_a_series_from_another_source_downloads_through_the_same_queue(manager, monkeypatch):
    fake = FakeSource("fake", {"a.dcm": b"aaaa", "b.dcm": b"bb"}, known={"9.9"})
    monkeypatch.setitem(sources._SOURCES, "fake", fake)
    assert manager.add(Series(uid="9.9", aws_url="", source="fake", modality="CT")) == "queued"
    wait_idle(manager)
    (item,) = manager.library()
    assert (item.path / "a.dcm").read_bytes() == b"aaaa"
    assert item.manifest["source"] == "fake:9.9"


def test_find_series_asks_each_source_only_for_what_is_still_missing(monkeypatch):
    first = FakeSource("first", {}, known={"1"})
    second = FakeSource("second", {}, known={"1", "2"})
    monkeypatch.setattr(sources, "_SOURCES", {"first": first, "second": second})
    found = sources.find_series(["1", "2", "3"])
    assert {uid: s.source for uid, s in found.items()} == {"1": "first", "2": "second"}
    assert second.asked == [["2", "3"]]


def test_an_unknown_source_fails_the_download_with_a_message(manager):
    manager.add(Series(uid="7.7", aws_url="", source="nowhere"))
    wait_idle(manager)
    (job,) = manager.snapshot()["jobs"]
    assert job["status"] == downloader.FAILED
    assert job["error"] == "Unknown data source: nowhere"
