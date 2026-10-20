"""The `dicom.fetch-series` intent: download the series an annotation needs, then hand back.

DICOM Viewer opens this page in a dialog with the SeriesInstanceUIDs an
annotation object (SEG, RTSTRUCT, SR, PR) refers to, and the annotation's
label. The page lists those series from the IDC index, downloads them with
the shared queue, and finishes the intent once every one of them is in the
collection, so the viewer can open them with the annotation on top.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging

import reflex as rx
from reflex_ddns_auth.intent import IntentPage

from dicom_data_explorer import formatting
from dicom_data_explorer.services import downloader, idc_service
from dicom_data_explorer.services.downloader import Series

POLL_SECONDS = 0.5
ACTIVE = (downloader.QUEUED, downloader.RUNNING, downloader.CANCELING)


@dataclasses.dataclass
class Wanted:
    """One series the annotation needs, as the dialog lists it."""

    uid: str = ""
    title: str = ""
    subtitle: str = ""
    in_index: bool = True
    status: str = ""  # "" (not downloaded), queued, running, canceling, done, failed, interrupted
    percent: int = 0
    note: str = ""


def _title(s: Series) -> str:
    return s.description or f"Series …{s.uid[-12:]}"


def _subtitle(s: Series) -> str:
    parts = [s.collection, s.modality, s.body_part]
    if s.images:
        parts.append(f"{formatting.count(s.images)} images")
    parts.append(formatting.size_mb(s.size_mb))
    return " · ".join(p for p in parts if p)


def _status(uid: str, manager: downloader.DownloadManager, jobs: dict[str, dict]) -> tuple[str, int, str]:
    """(status, percent, note) of a series in the shared queue and the collection."""
    if manager.collection().has(uid):
        return "done", 100, ""
    job = jobs.get(uid)
    if job is None:
        return "", 0, ""
    done, total = job["bytes_done"], job["bytes_total"]
    percent = int(done * 100 / total) if total else 0
    note = ""
    if job["status"] == downloader.FAILED:
        note = str(job.get("error") or "The download failed.")
    elif job["status"] == downloader.INTERRUPTED:
        note = "Stopped when the app restarted."
    elif job["status"] == downloader.QUEUED:
        note = "Waiting in the download queue"
    return job["status"], percent, note


class FetchState(rx.State):
    title: str = ""  # the annotation, as the caller names it
    wanted: list[Wanted] = []
    loaded: bool = False
    error: str = ""
    _uids: list[str] = []
    _rows: dict[str, dict] = {}
    _watching: bool = False

    @rx.var
    def fetchable(self) -> list[Wanted]:
        return [w for w in self.wanted if w.in_index]

    @rx.var
    def all_done(self) -> bool:
        return bool(self.fetchable) and all(w.status == "done" for w in self.fetchable)

    @rx.var
    def active(self) -> bool:
        return any(w.status in ACTIVE for w in self.wanted)

    @rx.var
    def unknown_count(self) -> int:
        return sum(1 for w in self.wanted if not w.in_index)

    @rx.var
    def download_label(self) -> str:
        pending = [w for w in self.fetchable if w.status not in ("done", *ACTIVE)]
        if not pending:
            return "Download"
        size = sum(float(self._rows.get(w.uid, {}).get("series_size_MB") or 0) for w in pending)
        count = f"{len(pending)} series" if len(pending) > 1 else "the series"
        return f"Download {count} ({formatting.size_mb(size)})"

    def _refresh(self, manager: downloader.DownloadManager) -> None:
        jobs = {job["series"].uid: job for job in manager.snapshot()["jobs"]}
        updated = []
        for w in self.wanted:
            if w.in_index:
                status, percent, note = _status(w.uid, manager, jobs)
                w = Wanted(w.uid, w.title, w.subtitle, True, status, percent, note)
            updated.append(w)
        self.wanted = updated

    def _start_watching(self, manager: downloader.DownloadManager):
        if not self._watching and manager.busy():
            self._watching = True
            return FetchState.watch
        return None

    @rx.event
    async def open_intent(self, params: dict):
        """Entry point of the intent: `series` (comma-separated UIDs) and `title`."""
        self.title = str(params.get("title") or "").strip()
        uids = []
        for uid in str(params.get("series") or "").split(","):
            uid = uid.strip()
            if uid and uid not in uids:
                uids.append(uid)
        self._uids = uids
        self.error = "" if uids else "The annotation names no image series."
        try:
            rows = await asyncio.to_thread(idc_service.series_by_uid, uids) if uids else {}
        except Exception:  # noqa: BLE001 - shown in the dialog
            logging.exception("Looking up the series of an annotation failed")
            rows = {}
            self.error = "The IDC index could not be read."
        self._rows = rows
        wanted = []
        for uid in uids:
            row = rows.get(uid)
            if row is None:
                wanted.append(Wanted(uid=uid, title=f"Series …{uid[-12:]}", subtitle="Not in the IDC index", in_index=False))
            else:
                s = Series.from_index(row)
                wanted.append(Wanted(uid=uid, title=_title(s), subtitle=_subtitle(s)))
        self.wanted = wanted
        manager = await asyncio.to_thread(downloader.get_manager)
        self._refresh(manager)
        self.loaded = True
        return self._start_watching(manager)

    @rx.event
    def download_all(self):
        """Queue every series that is not downloaded or already in the queue."""
        manager = downloader.get_manager()
        for w in self.wanted:
            if w.in_index and w.status not in ("done", *ACTIVE):
                manager.add(Series.from_index(self._rows[w.uid]))
        self._refresh(manager)
        return self._start_watching(manager)

    @rx.event
    def retry(self, uid: str):
        manager = downloader.get_manager()
        manager.retry(uid)
        self._refresh(manager)
        return self._start_watching(manager)

    @rx.event(background=True)
    async def watch(self):
        """Follow the queue until the wanted series are all in, then hand back to the caller."""
        manager = downloader.get_manager()
        while True:
            busy = manager.busy()  # before the refresh, so an idle queue is final
            async with self:
                self._refresh(manager)
                finished = self.all_done
                stop = finished or (not busy and not manager.busy())
                if stop:
                    self._watching = False
            if finished:
                yield FetchState.finish_now
            if stop:
                return
            await asyncio.sleep(POLL_SECONDS)

    @rx.event
    def finish_now(self):
        """Tell the caller which series are in the collection now and close the dialog."""
        return IntentPage.finish(
            {
                "series": list(self._uids),
                "downloaded": [w.uid for w in self.wanted if w.status == "done"],
            }
        )

    @rx.event
    def cancel(self):
        return IntentPage.cancel
