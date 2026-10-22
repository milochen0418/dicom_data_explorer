"""Download series into the shared "dicom" collection.

One queue for the whole app (the backend runs a single worker), so every tab
sees the same downloads and a series is never fetched twice at the same time.
Each source has its own lane: its series download one after another, and a
slow PACS never holds up IDC. The files of a series come over parallel
connections: IDC files are small and served from us-east-1, so fetching them
one at a time spends nearly all its time waiting.

Where the files come from is up to the series' source (services/sources):
the queue asks it for the file list, then either for each file's bytes or,
for C-GET and C-MOVE, to fetch the whole series and hand each file over.

Files are written into a ``Collection.publish(resume=True)`` staging folder.
A failed series keeps the files it already has, so Retry fetches only the
rest; a restart leaves the folder too, and it comes back as "interrupted".
A journal (one small JSON file per job, outside the collection) remembers
what each job was, so a PACS series, or one still waiting, comes back too.
"""

from __future__ import annotations

import dataclasses
import errno
import json
import logging
import re
import shutil
import threading
import time
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable

import requests
from reflex_ddns_auth.collections import TMP_DIR, Collection, Item

from dicom_data_explorer.services import pacs_config, sources
from dicom_data_explorer.services.sources.base import DownloadError, RemoteFile, Series, Source

COLLECTION = "dicom"
KIND = "dicom.series"
PARALLEL_FILES = 8
ATTEMPTS = 3
FINISHED_KEPT = 50

QUEUED = "queued"
RUNNING = "running"
CANCELING = "canceling"
FAILED = "failed"
INTERRUPTED = "interrupted"  # left over from before a restart


@dataclasses.dataclass
class Job:
    series: Series
    status: str = QUEUED
    files_total: int = 0
    files_done: int = 0
    bytes_total: int = 0  # 0 when the source does not say how big the files are
    bytes_done: int = 0
    bytes_at_start: int = 0  # already on disk when this attempt started
    started_at: float = 0.0
    error: str = ""
    stop: bool = False  # canceled: abort every file, even half-written ones
    halt: bool = False  # a file failed: start no new files, let the others finish


class _Stopped(Exception):
    pass


def percent(job: dict) -> int:
    """How far a snapshot's job is: by bytes when the source said how big the
    files are, else by files."""
    if job["bytes_total"]:
        return int(job["bytes_done"] * 100 / job["bytes_total"])
    return int(job["files_done"] * 100 / job["files_total"]) if job["files_total"] else 0


def describe_error(exc: BaseException, source: Source | None = None) -> str:
    """A short message for the user. The source names its own errors first:
    requests errors are OSErrors too."""
    text = source.describe_error(exc) if source is not None else None
    if text:
        return text
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return "The disk is full."
    if isinstance(exc, OSError):
        return f"Couldn't save the files ({exc.strerror or exc})."
    return str(exc) or exc.__class__.__name__


class _Sink:
    """Where a source that fetches a whole series (C-GET, C-MOVE) puts each file.

    Files already on disk and files the listing did not name are skipped, so
    a resumed retrieve that sends everything again costs time, not files.
    """

    def __init__(self, manager: DownloadManager, job: Job, item, todo: list[RemoteFile]):
        self._manager, self._job, self._item = manager, job, item
        self._wanted = {remote.rel for remote in todo}

    def stopped(self) -> bool:
        return self._job.stop

    def write(self, rel: str, data: bytes) -> None:
        manager = self._manager
        with manager._lock:
            if self._job.stop or rel not in self._wanted:
                return
            self._wanted.discard(rel)
        with self._item.open(rel) as fh:
            fh.write(data)
        with manager._lock:
            self._job.bytes_done += len(data)
            self._job.files_done += 1
            manager._changed()


class DownloadManager:
    def __init__(
        self,
        collection_factory: Callable[[], Collection] | None = None,
        parallel: int = PARALLEL_FILES,
        session_factory: Callable[[], requests.Session] = requests.Session,
        retry_delay: float = 1.0,
        journal_dir: Path | None = None,
    ):
        self._collection_factory = collection_factory or (lambda: Collection(COLLECTION))
        self._session_factory = session_factory
        self._retry_delay = retry_delay
        self._journal = journal_dir
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}  # queue order
        self._finished: list[dict] = []
        self._seq = 0
        self._version = 0
        self._workers: dict[str, threading.Thread] = {}  # one per source with work queued
        self._parallel = parallel
        self._pool = ThreadPoolExecutor(parallel, thread_name_prefix="download-file")
        self._local = threading.local()

    # -- public API (called from the UI; never blocks on the network) ---------

    def collection(self) -> Collection:
        return self._collection_factory()

    def add(self, series: Series) -> str:
        """Queue a series: "queued", "active" (already in the queue) or "downloaded"."""
        with self._lock:
            job = self._jobs.get(series.uid)
            if job is not None and job.status in (QUEUED, RUNNING, CANCELING):
                return "active"
            if self.collection().has(series.uid):
                return "downloaded"
            if job is not None:  # failed or interrupted: try again
                del self._jobs[series.uid]
            self._jobs[series.uid] = Job(series=series)
            self._remember(series)
            self._changed()
            self._ensure_worker(series.source)
            return "queued"

    def retry(self, uid: str) -> None:
        with self._lock:
            job = self._jobs.get(uid)
            if job is None or job.status not in (FAILED, INTERRUPTED):
                return
            del self._jobs[uid]
            self._jobs[uid] = Job(series=job.series)  # back of the queue
            self._changed()
            self._ensure_worker(job.series.source)

    def cancel(self, uid: str) -> None:
        """Stop or unqueue a download and throw its partial files away."""
        with self._lock:
            job = self._jobs.get(uid)
            if job is None or job.status == CANCELING:
                return
            if job.status == RUNNING:
                job.status, job.stop = CANCELING, True
                self._changed()
                return
            del self._jobs[uid]
            self._changed()
        self._forget(uid)
        self._discard_partial(uid)

    def busy(self) -> bool:
        with self._lock:
            return any(j.status in (QUEUED, RUNNING, CANCELING) for j in self._jobs.values())

    def snapshot(self) -> dict:
        now = time.monotonic()
        with self._lock:
            jobs, positions = [], {}
            for job in self._jobs.values():
                lane = job.series.source
                if job.status == QUEUED:
                    positions[lane] = positions.get(lane, 0) + 1
                elapsed = now - job.started_at if job.status == RUNNING else 0
                speed = (job.bytes_done - job.bytes_at_start) / elapsed if elapsed >= 1 else 0.0
                left = job.bytes_total - job.bytes_done
                jobs.append(
                    {
                        "series": job.series,
                        "status": job.status,
                        "position": positions[lane] if job.status == QUEUED else 0,
                        "files_done": job.files_done,
                        "files_total": job.files_total,
                        "bytes_done": job.bytes_done,
                        "bytes_total": job.bytes_total,
                        "speed": speed,
                        "eta": left / speed if speed > 0 and left > 0 else -1.0,
                        "error": job.error,
                    }
                )
            return {"version": self._version, "jobs": jobs, "finished": list(self._finished)}

    def library(self) -> list[Item]:
        """The series this app has downloaded, newest first."""
        col = self.collection()
        items = [i for i in col.local_items(kind=KIND) if i.owner == col.app_id]
        items.sort(key=lambda i: i.created_at, reverse=True)
        return items

    def delete(self, uid: str) -> bool:
        with self._lock:
            if uid in self._jobs:
                return False
        self._forget(uid)
        return self.collection().remove(uid)

    def recover(self, lookup: Callable[[list[str]], dict[str, Series]]) -> int:
        """List what a restart interrupted as interrupted downloads: staging
        folders, and journaled jobs that had not started yet."""
        col = self.collection()
        journal = self._journaled()
        try:
            folders = [
                p for p in (col.own_dir / TMP_DIR).iterdir()
                if p.is_dir() and ".replica." not in p.name
            ]
        except OSError:
            folders = []
        partial: dict[str, list] = {}
        for folder in folders:
            uid = folder.name.rsplit(".", 2)[0]
            if col.has(uid):
                shutil.rmtree(folder, ignore_errors=True)  # published after all
                continue
            partial.setdefault(uid, []).append(folder)
        for uid in [uid for uid in journal if col.has(uid)]:
            self._forget(uid)  # finished just before the restart
            del journal[uid]
        known = {uid: journal[uid] for uid in partial if uid in journal}
        missing = [uid for uid in partial if uid not in journal]
        if missing:
            known.update(lookup(missing))
        known.update({uid: series for uid, series in journal.items() if uid not in partial})
        added = 0
        with self._lock:
            for uid, series in known.items():
                if uid in self._jobs:
                    continue
                kept = [
                    f for folder in partial.get(uid, []) for f in folder.rglob("*")
                    if f.is_file() and not f.name.endswith(".part")
                    and ".item" not in f.relative_to(folder).parts
                ]
                self._jobs[uid] = Job(
                    series=series,
                    status=INTERRUPTED,
                    files_total=series.images,
                    files_done=len(kept),
                    bytes_total=int(series.size_mb * 1e6),
                    bytes_done=sum(f.stat().st_size for f in kept),
                )
                added += 1
            if added:
                self._changed()
        return added

    # -- the journal ------------------------------------------------------------

    def _journal_file(self, uid: str) -> Path | None:
        if self._journal is None or not re.fullmatch(r"[0-9.]+", uid):
            return None
        return self._journal / f"{uid}.json"

    def _remember(self, series: Series) -> None:
        path = self._journal_file(series.uid)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(dataclasses.asdict(series)))
        except OSError:
            logging.exception("Could not journal the download of %s", series.uid)

    def _forget(self, uid: str) -> None:
        path = self._journal_file(uid)
        if path is not None:
            path.unlink(missing_ok=True)

    def _journaled(self) -> dict[str, Series]:
        if self._journal is None:
            return {}
        fields = {f.name for f in dataclasses.fields(Series)}
        found = {}
        for path in self._journal.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                series = Series(**{k: v for k, v in data.items() if k in fields})
            except (OSError, ValueError, TypeError):
                continue
            found[series.uid] = series
        return found

    # -- the workers --------------------------------------------------------------

    def _changed(self) -> None:
        self._version += 1

    def _ensure_worker(self, lane: str) -> None:
        if lane not in self._workers:
            worker = threading.Thread(target=self._run, args=(lane,), name=f"download-{lane}", daemon=True)
            self._workers[lane] = worker
            worker.start()

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = self._local.session = self._session_factory()
        return session

    def _finish(self, job: Job, outcome: str) -> None:
        self._seq += 1
        self._finished.append(
            {"seq": self._seq, "uid": job.series.uid, "outcome": outcome,
             "series": job.series, "error": job.error}
        )
        del self._finished[:-FINISHED_KEPT]
        self._changed()

    def _run(self, lane: str) -> None:
        while True:
            with self._lock:
                job = next(
                    (j for j in self._jobs.values() if j.status == QUEUED and j.series.source == lane), None
                )
                if job is None:
                    del self._workers[lane]
                    return
                job.status, job.stop, job.halt, job.error = RUNNING, False, False, ""
                job.started_at = time.monotonic()
                self._changed()
            try:
                self._download(job)
            except _Stopped:
                self._discard_partial(job.series.uid)
                with self._lock:
                    self._jobs.pop(job.series.uid, None)
                    self._finish(job, "canceled")
                self._forget(job.series.uid)
            except Exception as exc:  # noqa: BLE001 - reported on the job
                logging.exception("Download of %s failed", job.series.uid)
                with self._lock:
                    job.status, job.error = FAILED, describe_error(exc, sources.get(job.series.source))
                    job.stop = job.halt = False
                    self._finish(job, "failed")
            else:
                with self._lock:
                    self._jobs.pop(job.series.uid, None)
                    self._finish(job, "done")
                self._forget(job.series.uid)

    def _with_retries(self, job: Job, source: Source, fn: Callable[[], object]):
        for attempt in range(1, ATTEMPTS + 1):
            if job.stop or job.halt:
                raise _Stopped()
            try:
                return fn()
            except _Stopped:
                raise
            except Exception as exc:
                if attempt == ATTEMPTS or not source.is_transient(exc):
                    raise
                time.sleep(self._retry_delay * attempt)

    def _download(self, job: Job) -> None:
        s = job.series
        source = sources.get(s.source)
        if source is None:
            raise DownloadError(f"Unknown data source: {s.source}")
        files = self._with_retries(job, source, lambda: source.list_files(s, self._session()))
        if not files:
            raise DownloadError(f"{source.label} lists no files for this series.")
        col = self.collection()
        if col.has(s.uid):
            return
        meta = {
            "Collection": s.collection, "Modality": s.modality, "BodyPartExamined": s.body_part,
            "ImageCount": s.images, "SeriesDescription": s.description, "SeriesDate": s.date,
        }
        with col.publish(
            s.uid,
            kind=KIND,
            title=" · ".join(v for v in (s.collection, s.modality) if v),
            source=source.origin(s),
            meta={k: v for k, v in meta.items() if v},
            resume=True,
        ) as item:
            todo, kept_files, kept_bytes = [], 0, 0
            for remote in files:
                dest = item.file(remote.rel)
                # Complete files are kept: the right size, or, when the source does
                # not say, just being there (a .part file is renamed only when whole).
                if dest.is_file() and remote.size in (0, dest.stat().st_size):
                    kept_files, kept_bytes = kept_files + 1, kept_bytes + dest.stat().st_size
                else:
                    todo.append(remote)
            with self._lock:
                job.files_total, job.files_done = len(files), kept_files
                job.bytes_total = sum(remote.size for remote in files)
                job.bytes_done = job.bytes_at_start = kept_bytes
                job.started_at = time.monotonic()
                self._changed()
            if source.per_file:
                failure = self._fetch_files(job, source, todo, item)
            else:
                failure = self._fetch_series(job, source, todo, item)
            if job.status == CANCELING:
                item.discard()
                raise _Stopped()
            if failure is not None:
                raise failure  # leaves the staging folder for Retry
            missing = sum(1 for remote in files if not item.file(remote.rel).is_file())
            if missing:
                raise DownloadError(f"{missing} of {len(files)} files did not arrive. Retry fetches the rest.")

    def _fetch_files(self, job: Job, source: Source, todo: list[RemoteFile], item) -> BaseException | None:
        """Read each file through the source, at most `source.parallel` at a time.

        The lane thread waits for a free slot, so a slow PACS never holds pool
        threads that IDC could use.
        """
        slots = threading.Semaphore(min(source.parallel or self._parallel, self._parallel))
        failed = threading.Event()

        def one(remote: RemoteFile) -> None:
            try:
                self._fetch_file(job, source, remote, item)
            except BaseException:
                failed.set()
                raise
            finally:
                slots.release()

        futures = []
        for remote in todo:
            slots.acquire()
            if failed.is_set() or job.stop:
                slots.release()
                break
            futures.append(self._pool.submit(one, remote))
        done, _ = wait(futures, return_when=FIRST_EXCEPTION)
        errors = [f.exception() for f in done if f.exception() is not None]
        if errors:
            job.halt = True
            wait(futures)
        return next((e for e in errors if not isinstance(e, _Stopped)), errors[0] if errors else None)

    def _fetch_series(self, job: Job, source: Source, todo: list[RemoteFile], item) -> BaseException | None:
        if not todo:
            return None
        try:
            source.fetch(job.series, todo, _Sink(self, job, item, todo))
        except Exception as exc:  # noqa: BLE001 - _download decides: canceled or failed
            return exc
        return None

    def _fetch_file(self, job: Job, source: Source, remote: RemoteFile, item) -> None:
        def fetch() -> None:
            written = 0
            try:
                with source.open_file(remote, self._session()) as chunks, item.open(remote.rel) as fh:
                    for chunk in chunks:
                        if job.stop:
                            raise _Stopped()
                        fh.write(chunk)
                        written += len(chunk)
                        with self._lock:
                            job.bytes_done += len(chunk)
                            self._changed()
            except BaseException:
                with self._lock:
                    job.bytes_done -= written
                raise

        self._with_retries(job, source, fetch)
        with self._lock:
            job.files_done += 1
            self._changed()

    def _discard_partial(self, uid: str) -> None:
        tmp_root = self.collection().own_dir / TMP_DIR
        for folder in tmp_root.glob(f"{uid}.*"):
            if folder.is_dir() and ".replica." not in folder.name and folder.name.rsplit(".", 2)[0] == uid:
                shutil.rmtree(folder, ignore_errors=True)


_manager: DownloadManager | None = None
_manager_lock = threading.Lock()


def _lookup(uids: list[str]) -> dict[str, Series]:
    return sources.find_series(uids)


def get_manager() -> DownloadManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = DownloadManager(journal_dir=pacs_config.data_dir() / "jobs")
            try:
                _manager.recover(_lookup)
            except Exception:  # noqa: BLE001 - the queue works without it
                logging.exception("Could not look for interrupted downloads")
        return _manager


def lookup_series(uid: str) -> Series | None:
    return _lookup([uid]).get(uid)
