"""The download queue with PACS sources: lanes, the journal, files that never came."""

from __future__ import annotations

import contextlib
import threading
import time

import pytest
from reflex_ddns_auth.collections import Collection

from dicom_data_explorer.services import downloader, sources
from dicom_data_explorer.services.downloader import DownloadManager, Series
from dicom_data_explorer.services.sources.base import RemoteFile, Source


class GatedSource(Source):
    """One file per series, read file by file; it waits at the gate until it opens."""

    parallel = 1

    def __init__(self, source_id: str, gate: threading.Event | None = None):
        self.id = self.label = source_id
        self.gate = gate
        self.broken = False  # stands for a process that died: everything fails

    def list_files(self, series, session):
        if self.broken:
            raise RuntimeError("gone")
        return [RemoteFile(rel="a.dcm", size=4, ref="a")]

    @contextlib.contextmanager
    def open_file(self, remote, session):
        if self.gate is not None:
            assert self.gate.wait(10)
        if self.broken:
            raise RuntimeError("gone")
        yield [b"aaaa"]

    def origin(self, series):
        return f"{self.id}:{series.uid}"


class WholeSeriesSource(Source):
    """Sends a whole series at once, like C-GET or C-MOVE, and does not say the sizes."""

    per_file = False

    def __init__(self):
        self.id, self.label = "pacs", "Test PACS"
        self.files = {f"{n}.dcm": bytes([n]) * 10 for n in (1, 2, 3)}
        self.sends = ["1.dcm", "2.dcm"]
        self.asked: list[list[str]] = []

    def list_files(self, series, session):
        return [RemoteFile(rel=name, size=0, ref=name) for name in self.files]

    def fetch(self, series, todo, sink):
        self.asked.append(sorted(remote.rel for remote in todo))
        for name in self.sends:
            sink.write(name, self.files[name])

    def origin(self, series):
        return f"pacs:{series.uid}"


def wait_for(check, timeout: float = 10) -> None:
    deadline = time.time() + timeout
    while not check():
        assert time.time() < deadline, "timed out"
        time.sleep(0.02)


def statuses(manager: DownloadManager) -> dict[str, str]:
    return {job["series"].uid: job["status"] for job in manager.snapshot()["jobs"]}


def make_manager(root, journal_dir=None) -> DownloadManager:
    return DownloadManager(
        collection_factory=lambda: Collection("dicom", app_id="test-app", root=root),
        parallel=2,
        session_factory=lambda: None,
        retry_delay=0,
        journal_dir=journal_dir,
    )


def test_a_slow_pacs_does_not_hold_up_the_others(tmp_path, monkeypatch):
    gate = threading.Event()
    monkeypatch.setitem(sources._SOURCES, "slow", GatedSource("slow", gate))
    monkeypatch.setitem(sources._SOURCES, "fast", GatedSource("fast"))
    manager = make_manager(tmp_path)
    for uid, source in (("1.1", "slow"), ("1.2", "slow"), ("2.1", "fast")):
        manager.add(Series(uid=uid, aws_url="", source=source))
    wait_for(lambda: manager.collection().has("2.1"))
    jobs = {job["series"].uid: job for job in manager.snapshot()["jobs"]}
    assert jobs["1.1"]["status"] == downloader.RUNNING
    assert (jobs["1.2"]["status"], jobs["1.2"]["position"]) == (downloader.QUEUED, 1)
    gate.set()
    wait_for(lambda: not manager.busy())
    assert manager.collection().has("1.1") and manager.collection().has("1.2")


def test_a_restart_brings_back_pacs_jobs_from_the_journal(tmp_path, monkeypatch):
    gate = threading.Event()
    slow = GatedSource("slow", gate)
    monkeypatch.setitem(sources._SOURCES, "slow", slow)
    journal = tmp_path / "jobs"
    started = Series(uid="1.1", aws_url="", source="slow", study_uid="1", images=1)
    waiting = Series(uid="1.2", aws_url="", source="slow", study_uid="1", images=1)

    before = make_manager(tmp_path / "col", journal)
    before.add(started)
    before.add(waiting)
    wait_for(lambda: statuses(before) == {"1.1": downloader.RUNNING, "1.2": downloader.QUEUED})
    assert sorted(p.name for p in journal.iterdir()) == ["1.1.json", "1.2.json"]
    slow.broken = True  # the app goes down: what was on disk stays
    gate.set()
    wait_for(lambda: not before.busy())

    after = make_manager(tmp_path / "col", journal)
    asked = []
    assert after.recover(lambda uids: asked.append(uids) or {}) == 2
    assert asked == []  # IDC cannot know PACS series: the journal does
    assert statuses(after) == {"1.1": downloader.INTERRUPTED, "1.2": downloader.INTERRUPTED}
    assert {job["series"].uid: job["series"] for job in after.snapshot()["jobs"]}["1.2"] == waiting

    slow.broken = False
    after.retry("1.1")
    after.retry("1.2")
    wait_for(lambda: not after.busy())
    assert after.collection().has("1.1") and after.collection().has("1.2")
    assert list(journal.iterdir()) == []


def test_files_a_pacs_did_not_send_are_reported_and_retry_asks_only_for_those(tmp_path, monkeypatch):
    pacs = WholeSeriesSource()
    monkeypatch.setitem(sources._SOURCES, "pacs", pacs)
    manager = make_manager(tmp_path)
    manager.add(Series(uid="3.3", aws_url="", source="pacs"))
    wait_for(lambda: not manager.busy())
    (job,) = manager.snapshot()["jobs"]
    assert job["status"] == downloader.FAILED
    assert job["error"] == "1 of 3 files did not arrive. Retry fetches the rest."
    assert (job["files_done"], job["bytes_total"], downloader.percent(job)) == (2, 0, 66)

    pacs.sends = list(pacs.files)  # this time everything, the two it sent before too
    manager.retry("3.3")
    wait_for(lambda: not manager.busy())
    assert pacs.asked == [["1.dcm", "2.dcm", "3.dcm"], ["3.dcm"]]
    (item,) = manager.library()
    assert {f["path"] for f in item.files} == set(pacs.files)
    assert manager.snapshot()["finished"][-1]["outcome"] == "done"


@pytest.mark.parametrize(
    "job, expected",
    [
        ({"bytes_total": 200, "bytes_done": 50, "files_total": 4, "files_done": 3}, 25),
        ({"bytes_total": 0, "bytes_done": 50, "files_total": 4, "files_done": 1}, 25),
        ({"bytes_total": 0, "bytes_done": 0, "files_total": 0, "files_done": 0}, 0),
    ],
)
def test_progress_goes_by_files_when_the_sizes_are_unknown(job, expected):
    assert downloader.percent(job) == expected
