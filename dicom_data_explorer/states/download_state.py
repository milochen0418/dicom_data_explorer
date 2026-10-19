"""Each tab's view of the shared download queue and of the downloaded series."""

from __future__ import annotations

import asyncio
import dataclasses

import reflex as rx

from dicom_data_explorer import formatting
from dicom_data_explorer.services import downloader, idc_service
from dicom_data_explorer.services.downloader import Series

# Ask before downloading a series bigger than this (MB).
BIG_DOWNLOAD_MB = 1000
POLL_SECONDS = 0.5


@dataclasses.dataclass
class JobCard:
    uid: str = ""
    title: str = ""
    subtitle: str = ""
    status: str = ""
    percent: int = 0
    progress: str = ""  # "34 of 50 files · 18 of 26 MB"
    rate: str = ""  # "3.4 MB/s · about 3 s left"
    note: str = ""


@dataclasses.dataclass
class SavedSeries:
    uid: str = ""
    title: str = ""
    subtitle: str = ""
    size: str = ""
    saved_at: str = ""
    folder: str = ""
    path: str = ""


def _title(s: Series) -> str:
    return s.description or f"Series …{s.uid[-12:]}"


def _subtitle(s: Series) -> str:
    parts = [s.collection, s.modality, s.body_part]
    if s.images:
        parts.append(f"{formatting.count(s.images)} images")
    parts.append(formatting.size_mb(s.size_mb))
    return " · ".join(p for p in parts if p)


def _card(job: dict) -> JobCard:
    s: Series = job["series"]
    status = job["status"]
    done, total = job["bytes_done"], job["bytes_total"]
    percent = int(done * 100 / total) if total else 0
    progress = rate = note = ""
    if job["files_total"]:
        progress = (
            f"{formatting.count(job['files_done'])} of {formatting.count(job['files_total'])} files"
            f" · {formatting.size_bytes(done) if done else '0 MB'} of {formatting.size_bytes(total)}"
        )
    if status == downloader.RUNNING:
        if not job["files_total"]:
            note = "Asking IDC for the file list…"
        elif job["speed"] > 0:
            rate = formatting.speed(job["speed"])
            if job["eta"] >= 0:
                rate += f" · about {formatting.duration(job['eta'])} left"
    elif status == downloader.QUEUED:
        note = "Next" if job["position"] == 1 else f"Waiting · #{job['position']} in line"
    elif status == downloader.CANCELING:
        note = "Canceling…"
    elif status == downloader.FAILED:
        note = f"{job['error']} Retry continues where it stopped."
    elif status == downloader.INTERRUPTED:
        note = (
            f"Stopped when the app restarted. {formatting.count(job['files_done'])} files "
            "are kept; Resume fetches the rest."
        )
    return JobCard(
        uid=s.uid, title=_title(s), subtitle=_subtitle(s), status=status,
        percent=percent, progress=progress, rate=rate, note=note,
    )


def _load_saved(manager: downloader.DownloadManager) -> list[SavedSeries]:
    items = manager.library()
    # Series saved before descriptions went into the manifest: ask the index.
    missing = [i.id for i in items if not i.meta.get("SeriesDescription")]
    index = idc_service.series_by_uid(missing) if missing else {}
    saved = []
    for item in items:
        m = item.meta
        row = index.get(item.id)
        s = Series.from_index(row) if row else Series(
            uid=item.id, aws_url="",
            collection=str(m.get("Collection") or ""), modality=str(m.get("Modality") or ""),
            body_part=str(m.get("BodyPartExamined") or ""),
            description=str(m.get("SeriesDescription") or ""),
            images=int(m.get("ImageCount") or 0),
        )
        s.size_mb = item.bytes / 1e6
        saved.append(
            SavedSeries(
                uid=item.id, title=_title(s), subtitle=_subtitle(s),
                size=formatting.size_bytes(item.bytes), saved_at=item.created_at,
                folder=f"{item.owner}/{item.id}", path=str(item.path),
            )
        )
    return saved


class DownloadState(rx.State):
    jobs: list[JobCard] = []
    saved: list[SavedSeries] = []
    # Per series UID, for the buttons in the search results.
    status_of: dict[str, str] = {}
    percent_of: dict[str, int] = {}

    big_uid: str = ""
    big_text: str = ""
    delete_uid: str = ""
    delete_title: str = ""

    _version: int = -1
    _seq: int = -1  # last finished download this tab has announced
    _watching: bool = False

    @rx.var
    def active_count(self) -> int:
        return sum(1 for j in self.jobs if j.status in ("queued", "running", "canceling"))

    @rx.var
    def problem_count(self) -> int:
        return sum(1 for j in self.jobs if j.status in ("failed", "interrupted"))

    @rx.var
    def current(self) -> JobCard:
        for job in self.jobs:
            if job.status in ("running", "canceling"):
                return job
        return JobCard()

    @rx.var
    def saved_total(self) -> str:
        return f"{formatting.count(len(self.saved))} series"

    # -- syncing with the shared queue ----------------------------------------

    def _apply(self, snap: dict, saved: list[SavedSeries] | None) -> list:
        """Copy a queue snapshot (and maybe a fresh list of saved series) into the state."""
        events = []
        if saved is not None:
            self.saved = saved
        if snap["version"] != self._version or saved is not None:
            self._version = snap["version"]
            self.jobs = [_card(j) for j in snap["jobs"]]
            status = {s.uid: "done" for s in self.saved}
            status.update({j.uid: j.status for j in self.jobs})
            self.status_of = status
            self.percent_of = {j.uid: j.percent for j in self.jobs if j.status == "running"}
        finished = snap["finished"]
        newest = finished[-1]["seq"] if finished else 0
        if self._seq >= 0:
            for event in finished:
                if event["seq"] <= self._seq:
                    continue
                name = _title(event["series"])
                if event["outcome"] == "done":
                    events.append(rx.toast.success(f"Downloaded: {name}"))
                elif event["outcome"] == "failed":
                    events.append(rx.toast.error(f"Download failed: {name}. {event['error']}"))
        self._seq = newest
        return events

    def _start_watching(self, manager: downloader.DownloadManager):
        if not self._watching and manager.busy():
            self._watching = True
            return DownloadState.watch
        return None

    @rx.event
    async def sync(self):
        """Page load: pick up downloads started elsewhere (other tabs, before a reload)."""
        manager = await asyncio.to_thread(downloader.get_manager)
        saved = await asyncio.to_thread(_load_saved, manager)
        self._apply(manager.snapshot(), saved)
        return self._start_watching(manager)

    @rx.event(background=True)
    async def watch(self):
        manager = downloader.get_manager()
        async with self:
            seen = self._seq
        while True:
            busy = manager.busy()  # before the snapshot, so an idle one is final
            snap = manager.snapshot()
            newest = snap["finished"][-1]["seq"] if snap["finished"] else 0
            saved = None
            if newest != seen:
                saved = await asyncio.to_thread(_load_saved, manager)
            seen = newest
            async with self:
                events = self._apply(snap, saved)
                # A download queued meanwhile found us still watching: keep going.
                stop = not busy and not manager.busy()
                if stop:
                    self._watching = False
            for event in events:
                yield event
            if stop:
                return
            await asyncio.sleep(POLL_SECONDS)

    # -- actions ----------------------------------------------------------------

    @rx.event
    async def download(self, uid: str, confirmed: bool = False):
        series = await asyncio.to_thread(downloader.lookup_series, uid)
        if series is None:
            return rx.toast.error("This series is not in the IDC index.")
        if series.size_mb >= BIG_DOWNLOAD_MB and not confirmed:
            self.big_uid = uid
            self.big_text = (
                f"“{_title(series)}” is {formatting.size_mb(series.size_mb)} "
                f"({formatting.count(series.images)} images). It can take a long time "
                "and needs that much free disk space."
            )
            return
        self.big_uid = ""
        manager = downloader.get_manager()
        outcome = manager.add(series)
        self._apply(manager.snapshot(), None)
        if outcome == "downloaded":
            saved = await asyncio.to_thread(_load_saved, manager)
            self._apply(manager.snapshot(), saved)
            return rx.toast.info("Already downloaded — see Downloads.")
        return self._start_watching(manager)

    @rx.event
    def confirm_big(self):
        return DownloadState.download(self.big_uid, True)

    @rx.event
    def cancel_big(self):
        self.big_uid = ""

    @rx.event
    def cancel(self, uid: str):
        manager = downloader.get_manager()
        manager.cancel(uid)
        self._apply(manager.snapshot(), None)
        return self._start_watching(manager)

    @rx.event
    def retry(self, uid: str):
        manager = downloader.get_manager()
        manager.retry(uid)
        self._apply(manager.snapshot(), None)
        return self._start_watching(manager)

    @rx.event
    def ask_delete(self, uid: str, title: str):
        self.delete_uid = uid
        self.delete_title = title

    @rx.event
    def cancel_delete(self):
        self.delete_uid = ""

    @rx.event
    async def confirm_delete(self):
        uid, self.delete_uid = self.delete_uid, ""
        manager = downloader.get_manager()
        removed = await asyncio.to_thread(manager.delete, uid)
        saved = await asyncio.to_thread(_load_saved, manager)
        self._apply(manager.snapshot(), saved)
        if not removed:
            return rx.toast.error("Couldn't delete that series.")
        return rx.toast.success("Deleted from the shared DICOM folder.")

    @rx.event
    def copy_path(self, path: str):
        return [rx.set_clipboard(path), rx.toast.info("Folder path copied.")]
