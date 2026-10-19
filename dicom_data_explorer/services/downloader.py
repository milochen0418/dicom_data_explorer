"""Download IDC series into the shared "dicom" collection.

One queue for the whole app (the backend runs a single worker), so every tab
sees the same downloads and a series is never fetched twice at the same time.
Series are downloaded one after another, and the files of a series over
parallel connections: IDC files are small and served from us-east-1, so
fetching them one at a time spends nearly all its time waiting.

Files are written into a ``Collection.publish(resume=True)`` staging folder.
A failed series keeps the files it already has, so Retry fetches only the
rest; a restart leaves the folder too, and it comes back as "interrupted".
"""

from __future__ import annotations

import dataclasses
import errno
import logging
import shutil
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from typing import Callable
from urllib.parse import quote, urlparse

import requests
from reflex_ddns_auth.collections import TMP_DIR, Collection, Item

COLLECTION = "dicom"
KIND = "dicom.series"
PARALLEL_FILES = 8
CHUNK_BYTES = 256 * 1024
ATTEMPTS = 3
FINISHED_KEPT = 50

QUEUED = "queued"
RUNNING = "running"
CANCELING = "canceling"
FAILED = "failed"
INTERRUPTED = "interrupted"  # left over from before a restart


@dataclasses.dataclass
class Series:
    uid: str
    aws_url: str
    collection: str = ""
    modality: str = ""
    body_part: str = ""
    description: str = ""
    date: str = ""
    images: int = 0
    size_mb: float = 0.0

    @classmethod
    def from_index(cls, row: dict) -> "Series":
        return cls(
            uid=str(row.get("SeriesInstanceUID") or ""),
            aws_url=str(row.get("series_aws_url") or ""),
            collection=str(row.get("collection_id") or ""),
            modality=str(row.get("Modality") or ""),
            body_part=str(row.get("BodyPartExamined") or ""),
            description=str(row.get("SeriesDescription") or ""),
            date=str(row.get("SeriesDate") or ""),
            images=int(row.get("instanceCount") or 0),
            size_mb=float(row.get("series_size_MB") or 0),
        )


@dataclasses.dataclass
class Job:
    series: Series
    status: str = QUEUED
    files_total: int = 0
    files_done: int = 0
    bytes_total: int = 0
    bytes_done: int = 0
    bytes_at_start: int = 0  # already on disk when this attempt started
    started_at: float = 0.0
    error: str = ""
    stop: bool = False  # canceled: abort every file, even half-written ones
    halt: bool = False  # a file failed: start no new files, let the others finish


class DownloadError(Exception):
    pass


class _Stopped(Exception):
    pass


def parse_s3_url(url: str) -> tuple[str, str]:
    """``s3://bucket/prefix/*`` (or an https S3 URL) → (bucket, prefix)."""
    if not url:
        raise DownloadError("IDC has no storage location for this series.")
    parsed = urlparse(url)
    if parsed.scheme == "s3":
        return parsed.netloc, parsed.path.lstrip("/").replace("*", "")
    if parsed.scheme in {"http", "https"}:
        host = parsed.netloc
        if host.endswith(".s3.amazonaws.com"):
            return host.split(".s3.amazonaws.com")[0], parsed.path.lstrip("/").replace("*", "")
        if host == "s3.amazonaws.com":
            bucket, _, prefix = parsed.path.lstrip("/").partition("/")
            return bucket, prefix.replace("*", "")
    raise DownloadError(f"Unsupported storage location: {url}")


def list_objects(session, bucket: str, prefix: str) -> list[tuple[str, int]]:
    """Every file under ``prefix`` with its size (S3 ListObjectsV2, anonymous)."""
    found: list[tuple[str, int]] = []
    token = None
    while True:
        params = {"list-type": "2", "prefix": prefix}
        if token:
            params["continuation-token"] = token
        resp = session.get(f"https://{bucket}.s3.amazonaws.com", params=params, timeout=(10, 60))
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for node in root.findall("{*}Contents"):
            key = node.findtext("{*}Key") or ""
            if key and not key.endswith("/"):
                found.append((key, int(node.findtext("{*}Size") or 0)))
        token = root.findtext("{*}NextContinuationToken")
        if not token:
            return found


def _relative(key: str, prefix: str) -> str:
    rel = key[len(prefix):] if prefix and key.startswith(prefix) else key.rsplit("/", 1)[-1]
    return rel.lstrip("/") or key.rsplit("/", 1)[-1]


def _transient(exc: BaseException) -> bool:
    if isinstance(exc, requests.HTTPError):
        code = exc.response.status_code if exc.response is not None else 0
        return code == 429 or code >= 500
    return isinstance(
        exc, (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError)
    )


def describe_error(exc: BaseException) -> str:
    """A short message for the user (requests errors are OSErrors too: check them first)."""
    if isinstance(exc, requests.Timeout):
        return "IDC stopped responding."
    if isinstance(exc, requests.ConnectionError):
        return "Can't reach IDC. Check the internet connection."
    if isinstance(exc, requests.HTTPError):
        code = exc.response.status_code if exc.response is not None else "?"
        return f"IDC answered with HTTP {code}."
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return "The disk is full."
    if isinstance(exc, OSError):
        return f"Couldn't save the files ({exc.strerror or exc})."
    return str(exc) or exc.__class__.__name__


class DownloadManager:
    def __init__(
        self,
        collection_factory: Callable[[], Collection] | None = None,
        parallel: int = PARALLEL_FILES,
        session_factory: Callable[[], requests.Session] = requests.Session,
        retry_delay: float = 1.0,
    ):
        self._collection_factory = collection_factory or (lambda: Collection(COLLECTION))
        self._session_factory = session_factory
        self._retry_delay = retry_delay
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}  # queue order
        self._finished: list[dict] = []
        self._seq = 0
        self._version = 0
        self._worker: threading.Thread | None = None
        self._pool = ThreadPoolExecutor(parallel, thread_name_prefix="idc-file")
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
            self._changed()
            self._ensure_worker()
            return "queued"

    def retry(self, uid: str) -> None:
        with self._lock:
            job = self._jobs.get(uid)
            if job is None or job.status not in (FAILED, INTERRUPTED):
                return
            del self._jobs[uid]
            self._jobs[uid] = Job(series=job.series)  # back of the queue
            self._changed()
            self._ensure_worker()

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
        self._discard_partial(uid)

    def busy(self) -> bool:
        with self._lock:
            return any(j.status in (QUEUED, RUNNING, CANCELING) for j in self._jobs.values())

    def snapshot(self) -> dict:
        now = time.monotonic()
        with self._lock:
            jobs, position = [], 0
            for job in self._jobs.values():
                if job.status == QUEUED:
                    position += 1
                elapsed = now - job.started_at if job.status == RUNNING else 0
                speed = (job.bytes_done - job.bytes_at_start) / elapsed if elapsed >= 1 else 0.0
                left = job.bytes_total - job.bytes_done
                jobs.append(
                    {
                        "series": job.series,
                        "status": job.status,
                        "position": position if job.status == QUEUED else 0,
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
        return self.collection().remove(uid)

    def recover(self, lookup: Callable[[list[str]], dict[str, Series]]) -> int:
        """List staging folders left by a restart as interrupted downloads."""
        col = self.collection()
        try:
            folders = [
                p for p in (col.own_dir / TMP_DIR).iterdir()
                if p.is_dir() and ".replica." not in p.name
            ]
        except OSError:
            return 0
        partial: dict[str, list] = {}
        for folder in folders:
            uid = folder.name.rsplit(".", 2)[0]
            if col.has(uid):
                shutil.rmtree(folder, ignore_errors=True)  # published after all
                continue
            partial.setdefault(uid, []).append(folder)
        known = lookup(list(partial)) if partial else {}
        added = 0
        with self._lock:
            for uid, series in known.items():
                if uid in self._jobs:
                    continue
                kept = [
                    f for folder in partial[uid] for f in folder.rglob("*")
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

    # -- the worker -------------------------------------------------------------

    def _changed(self) -> None:
        self._version += 1

    def _ensure_worker(self) -> None:
        if self._worker is None:
            self._worker = threading.Thread(target=self._run, name="idc-download", daemon=True)
            self._worker.start()

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

    def _run(self) -> None:
        while True:
            with self._lock:
                job = next((j for j in self._jobs.values() if j.status == QUEUED), None)
                if job is None:
                    self._worker = None
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
            except Exception as exc:  # noqa: BLE001 - reported on the job
                logging.exception("Download of %s failed", job.series.uid)
                with self._lock:
                    job.status, job.error = FAILED, describe_error(exc)
                    job.stop = job.halt = False
                    self._finish(job, "failed")
            else:
                with self._lock:
                    self._jobs.pop(job.series.uid, None)
                    self._finish(job, "done")

    def _with_retries(self, job: Job, fn: Callable[[], object]):
        for attempt in range(1, ATTEMPTS + 1):
            if job.stop or job.halt:
                raise _Stopped()
            try:
                return fn()
            except _Stopped:
                raise
            except Exception as exc:
                if attempt == ATTEMPTS or not _transient(exc):
                    raise
                time.sleep(self._retry_delay * attempt)

    def _download(self, job: Job) -> None:
        s = job.series
        bucket, prefix = parse_s3_url(s.aws_url)
        objects = self._with_retries(job, lambda: list_objects(self._session(), bucket, prefix))
        if not objects:
            raise DownloadError("IDC lists no files for this series.")
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
            source=s.aws_url,
            meta={k: v for k, v in meta.items() if v},
            resume=True,
        ) as item:
            todo, kept_files, kept_bytes = [], 0, 0
            for key, size in objects:
                rel = _relative(key, prefix)
                dest = item.file(rel)
                if dest.is_file() and dest.stat().st_size == size:
                    kept_files, kept_bytes = kept_files + 1, kept_bytes + size
                else:
                    todo.append((key, rel))
            with self._lock:
                job.files_total, job.files_done = len(objects), kept_files
                job.bytes_total = sum(size for _, size in objects)
                job.bytes_done = job.bytes_at_start = kept_bytes
                job.started_at = time.monotonic()
                self._changed()
            futures = [
                self._pool.submit(self._fetch_file, job, bucket, key, item, rel)
                for key, rel in todo
            ]
            done, _ = wait(futures, return_when=FIRST_EXCEPTION)
            failure = next((f.exception() for f in done if f.exception() is not None), None)
            if failure is not None:
                job.halt = True
                wait(futures)
            if job.status == CANCELING:
                item.discard()
                raise _Stopped()
            if failure is not None:
                raise failure  # leaves the staging folder for Retry

    def _fetch_file(self, job: Job, bucket: str, key: str, item, rel: str) -> None:
        url = f"https://{bucket}.s3.amazonaws.com/{quote(key, safe='/')}"

        def fetch() -> None:
            written = 0
            try:
                with self._session().get(url, stream=True, timeout=(10, 60)) as resp:
                    resp.raise_for_status()
                    with item.open(rel) as fh:
                        for chunk in resp.iter_content(CHUNK_BYTES):
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

        self._with_retries(job, fetch)
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
    from dicom_data_explorer.services import idc_service

    return {uid: Series.from_index(row) for uid, row in idc_service.series_by_uid(uids).items()}


def get_manager() -> DownloadManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = DownloadManager()
            try:
                _manager.recover(_lookup)
            except Exception:  # noqa: BLE001 - the queue works without it
                logging.exception("Could not look for interrupted downloads")
        return _manager


def lookup_series(uid: str) -> Series | None:
    return _lookup([uid]).get(uid)
