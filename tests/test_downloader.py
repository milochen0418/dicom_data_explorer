"""The download queue against a fake S3, writing into a temporary collection."""

from __future__ import annotations

import threading
import time
from urllib.parse import unquote

import pytest
import requests
from reflex_ddns_auth.collections import TMP_DIR, Collection

from dicom_data_explorer.services import downloader
from dicom_data_explorer.services.downloader import DownloadManager, Series
from dicom_data_explorer.services.sources import idc

BUCKET = "idc-open-data"
PREFIX = "series-uuid/"


class FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b"", gate: threading.Event | None = None):
        self.status_code = status
        self.content = body
        self._gate = gate

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def iter_content(self, size):
        for i in range(0, len(self.content), 4):
            if self._gate is not None:
                self._gate.wait(5)
            yield self.content[i : i + 4]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeS3:
    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.errors: dict[str, list] = {}  # key -> things to raise/return first
        self.gets: list[str] = []
        self.gate: threading.Event | None = None
        self.lock = threading.Lock()

    def session(self):
        return self

    def get(self, url, params=None, stream=False, timeout=None):
        if params is not None:
            contents = "".join(
                f"<Contents><Key>{PREFIX}{name}</Key><Size>{len(body)}</Size></Contents>"
                for name, body in self.files.items()
            )
            xml = f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">{contents}</ListBucketResult>'
            return FakeResponse(body=xml.encode())
        name = unquote(url.split(".s3.amazonaws.com/", 1)[1])[len(PREFIX):]
        with self.lock:
            self.gets.append(name)
            queued = self.errors.get(name)
            first = queued.pop(0) if queued else None
        if isinstance(first, Exception):
            raise first
        if isinstance(first, int):
            return FakeResponse(status=first)
        return FakeResponse(body=self.files[name], gate=self.gate)


def series(uid: str = "1.2.3.4", **kw) -> Series:
    return Series(uid=uid, aws_url=f"s3://{BUCKET}/{PREFIX}*", collection="demo", modality="CT",
                  description="Chest", images=3, size_mb=0.001, **kw)


@pytest.fixture
def s3():
    return FakeS3({"a.dcm": b"aaaaaaaa", "b.dcm": b"bbbbbbbbbbbb", "c.dcm": b"cc"})


@pytest.fixture
def make(tmp_path, s3):
    def _make(**kw):
        return DownloadManager(
            collection_factory=lambda: Collection("dicom", app_id="test-app", root=tmp_path),
            parallel=4,
            session_factory=s3.session,
            retry_delay=0,
            **kw,
        )

    return _make


def wait_idle(manager: DownloadManager, timeout: float = 10) -> None:
    deadline = time.time() + timeout
    while manager.busy():
        assert time.time() < deadline, "download did not finish"
        time.sleep(0.02)


def tmp_dirs(manager: DownloadManager) -> list:
    root = manager.collection().own_dir / TMP_DIR
    return [p for p in root.iterdir()] if root.exists() else []


def test_downloads_a_series_into_the_collection(make, s3):
    manager = make()
    assert manager.add(series()) == "queued"
    wait_idle(manager)
    (item,) = manager.library()
    assert item.id == "1.2.3.4"
    assert {f["path"] for f in item.files} == {"a.dcm", "b.dcm", "c.dcm"}
    assert (item.path / "b.dcm").read_bytes() == b"bbbbbbbbbbbb"
    assert item.meta["SeriesDescription"] == "Chest"
    snap = manager.snapshot()
    assert snap["jobs"] == []
    assert [e["outcome"] for e in snap["finished"]] == ["done"]
    assert manager.add(series()) == "downloaded"


def test_a_failed_file_keeps_the_rest_and_retry_fetches_only_what_is_missing(make, s3):
    manager = make()
    s3.errors["b.dcm"] = [403]
    manager.add(series())
    wait_idle(manager)
    (job,) = manager.snapshot()["jobs"]
    assert job["status"] == downloader.FAILED
    assert "403" in job["error"]
    assert manager.library() == []
    (staging,) = tmp_dirs(manager)
    kept = {p.name for p in staging.iterdir() if p.is_file()}
    assert "b.dcm" not in kept

    s3.gets.clear()
    manager.retry("1.2.3.4")
    wait_idle(manager)
    assert sorted(s3.gets) == sorted({"a.dcm", "b.dcm", "c.dcm"} - kept)
    assert len(manager.library()) == 1
    assert tmp_dirs(manager) == []


def test_transient_errors_are_retried(make, s3):
    manager = make()
    s3.errors["a.dcm"] = [requests.ConnectionError("reset"), 503]
    manager.add(series())
    wait_idle(manager)
    assert len(manager.library()) == 1
    assert s3.gets.count("a.dcm") == 3


def test_cancel_stops_the_download_and_discards_its_files(make, s3):
    manager = make()
    s3.gate = threading.Event()  # hold every file mid-way
    manager.add(series())
    manager.add(series("5.6.7"))
    deadline = time.time() + 5
    while manager.snapshot()["jobs"][0]["files_total"] == 0:
        assert time.time() < deadline
        time.sleep(0.02)
    manager.cancel("5.6.7")  # queued: just leaves the queue
    manager.cancel("1.2.3.4")  # running: stops
    assert manager.snapshot()["jobs"][0]["status"] == downloader.CANCELING
    s3.gate.set()
    wait_idle(manager)
    assert manager.snapshot()["jobs"] == []
    assert manager.library() == []
    assert tmp_dirs(manager) == []
    assert [e["outcome"] for e in manager.snapshot()["finished"]] == ["canceled"]


def test_a_series_is_queued_once(make, s3):
    manager = make()
    s3.gate = threading.Event()
    assert manager.add(series()) == "queued"
    assert manager.add(series()) == "active"
    s3.gate.set()
    wait_idle(manager)


def test_leftovers_from_a_restart_come_back_as_interrupted(make, s3):
    first = make()
    s3.errors["c.dcm"] = [404]
    first.add(series())
    wait_idle(first)

    (staging,) = tmp_dirs(first)
    kept = {p.name for p in staging.iterdir() if p.is_file()}

    second = make()  # a new process finds the staging folder
    assert second.recover(lambda uids: {u: series(u) for u in uids}) == 1
    (job,) = second.snapshot()["jobs"]
    assert job["status"] == downloader.INTERRUPTED
    assert job["files_done"] == len(kept)

    s3.gets.clear()
    second.retry("1.2.3.4")
    wait_idle(second)
    assert sorted(s3.gets) == sorted({"a.dcm", "b.dcm", "c.dcm"} - kept)
    assert len(second.library()) == 1


def test_delete_removes_a_downloaded_series(make, s3):
    manager = make()
    manager.add(series())
    wait_idle(manager)
    assert manager.delete("1.2.3.4") is True
    assert manager.library() == []


def test_parse_s3_url():
    assert idc.parse_s3_url("s3://idc-open-data/abc/*") == ("idc-open-data", "abc/")
    assert idc.parse_s3_url("https://b.s3.amazonaws.com/x/y") == ("b", "x/y")
    with pytest.raises(downloader.DownloadError):
        idc.parse_s3_url("ftp://nope")
