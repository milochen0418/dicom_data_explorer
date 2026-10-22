"""Each tab's view of the shared download queue and of the downloaded series."""

from __future__ import annotations

import asyncio
import dataclasses

import reflex as rx

from dicom_data_explorer import formatting
from dicom_data_explorer.services import downloader, idc_service, references, sources
from dicom_data_explorer.services.downloader import Series
from dicom_data_explorer.states.fetch_state import ACTIVE, Wanted, queue_status

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
    annotation: bool = False  # a SEG, RTSTRUCT, SR, ... that belongs to other images
    # For an annotation: "present" when every series it refers to is downloaded,
    # "missing" when some are not, "unknown" when its files cannot be read.
    images: str = ""
    needs: list[str] = dataclasses.field(default_factory=list)  # the series it refers to


def _title(s: Series) -> str:
    return s.description or f"Series …{s.uid[-12:]}"


def _subtitle(s: Series) -> str:
    # A PACS series has no IDC collection: name its PACS instead.
    parts = ["" if s.source == "idc" else sources.label_of(s.source), s.collection, s.modality, s.body_part]
    if s.images:
        parts.append(f"{formatting.count(s.images)} image{'' if s.images == 1 else 's'}")
    if s.size_mb > 0:
        parts.append(formatting.size_mb(s.size_mb))
    return " · ".join(p for p in parts if p)


def _card(job: dict) -> JobCard:
    s: Series = job["series"]
    status = job["status"]
    done, total = job["bytes_done"], job["bytes_total"]
    percent = downloader.percent(job)
    progress = rate = note = ""
    if job["files_total"]:
        files = f"{formatting.count(job['files_done'])} of {formatting.count(job['files_total'])} files"
        got = formatting.size_bytes(done) if done else "0 MB"
        progress = f"{files} · {got} of {formatting.size_bytes(total)}" if total else f"{files} · {got} so far"
    if status == downloader.RUNNING:
        if not job["files_total"]:
            note = f"Asking {sources.label_of(s.source)} for the file list…"
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


# What each downloaded annotation refers to; its files never change once published.
_references_cache: dict[str, set[str] | None] = {}


def _images_state(item, collection) -> tuple[str, list[str]]:
    """("present" | "missing" | "unknown", the series this annotation refers to)."""
    if item.id not in _references_cache:
        try:
            _references_cache[item.id] = references.series_referenced_by(item.path, item.id)
        except references.ReferencesUnavailable:
            return "unknown", []
    needed = sorted(_references_cache[item.id] or [])
    if not needed:
        return "unknown", []
    return ("present" if all(collection.has(uid) for uid in needed) else "missing"), needed


def _images_of(saved: list[SavedSeries], status: dict[str, str], percent: dict[str, int]) -> tuple[dict, dict, dict]:
    """Per downloaded annotation: the live state of the images it refers to, from the queue.

    Returns (state, percent, text) dicts by the annotation's uid. The state is
    the static one ("present", "missing", "unknown") unless one of its series
    is in the queue: then "queued", "running", "canceling" or "failed".
    """
    states: dict[str, str] = {}
    percents: dict[str, int] = {}
    texts: dict[str, str] = {}
    for s in saved:
        if not s.annotation:
            continue
        state, pct, text = s.images, 0, ""
        if s.needs:
            seen = [status.get(uid, "") for uid in s.needs]
            if all(st == "done" for st in seen):
                state, pct, text = "present", 100, "with its images"
            elif "running" in seen:
                pct = percent.get(s.needs[seen.index("running")], 0)
                state, text = "running", f"Downloading images · {pct}%"
            elif "canceling" in seen:
                state, text = "canceling", "Canceling…"
            elif "queued" in seen:
                state, text = "queued", "Images waiting in the queue"
            elif "failed" in seen or "interrupted" in seen:
                state, text = "failed", "The image download stopped"
            else:
                state, text = "missing", ""
        elif state == "present":
            text = "with its images"
        states[s.uid], percents[s.uid], texts[s.uid] = state, pct, text
    return states, percents, texts


def _load_saved(manager: downloader.DownloadManager) -> list[SavedSeries]:
    items = manager.library()
    collection = manager.collection()
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
        kind, _, source_id = str(item.manifest.get("source") or "").partition(":")
        if kind in ("dimse", "dicomweb"):  # from a PACS (see the sources' origin()): name it
            s.source = source_id
        annotation = s.modality in references.ANNOTATION_MODALITIES
        images, needs = _images_state(item, collection) if annotation else ("", [])
        saved.append(
            SavedSeries(
                uid=item.id, title=_title(s), subtitle=_subtitle(s),
                size=formatting.size_bytes(item.bytes), saved_at=item.created_at,
                folder=f"{item.owner}/{item.id}", path=str(item.path),
                annotation=annotation, images=images, needs=needs,
            )
        )
    return saved


class DownloadState(rx.State):
    jobs: list[JobCard] = []
    saved: list[SavedSeries] = []
    # Per series UID, for the buttons in the search results.
    status_of: dict[str, str] = {}
    percent_of: dict[str, int] = {}
    # Per downloaded annotation, the live state of the images it refers to.
    images_of: dict[str, str] = {}
    images_percent_of: dict[str, int] = {}
    images_text_of: dict[str, str] = {}

    big_uid: str = ""
    big_text: str = ""
    delete_uid: str = ""
    delete_title: str = ""
    # Downloading an annotation: the dialog that offers its images along with it.
    bundle_uid: str = ""  # the annotation; "" closes the dialog
    bundle_title: str = ""
    bundle_subtitle: str = ""
    bundle_loading: bool = False  # its files are being read for their references
    bundle_note: str = ""  # why the list is a guess, when it is
    bundle_rows: list[Wanted] = []
    bundle_selected: dict[str, bool] = {}
    _bundle_rows: dict[str, dict] = {}
    # An annotation downloaded earlier (Downloads page) and the series it belongs to.
    companion_uid: str = ""
    companion_title: str = ""
    companions: list[Wanted] = []
    _companion_rows: dict[str, dict] = {}

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

    @rx.var
    def companions_label(self) -> str:
        pending = [w for w in self.companions if w.in_index and w.status not in ("done", *ACTIVE)]
        if not pending:
            return "Download"
        size = sum(float(self._companion_rows.get(w.uid, {}).get("series_size_MB") or 0) for w in pending)
        count = f"{len(pending)} series" if len(pending) > 1 else "the series"
        return f"Download {count} ({formatting.size_mb(size)})"

    @rx.var
    def companions_pending(self) -> bool:
        return any(w.in_index and w.status not in ("done", *ACTIVE) for w in self.companions)

    @rx.var
    def bundle_label(self) -> str:
        """The confirm button: the annotation plus the selected images, with their size."""
        chosen = [w for w in self.bundle_rows if self.bundle_selected.get(w.uid) and w.status not in ("done", *ACTIVE)]
        size = sum(float(self._bundle_rows.get(w.uid, {}).get("series_size_MB") or 0) for w in chosen)
        size += float(self._bundle_rows.get(self.bundle_uid, {}).get("series_size_MB") or 0)
        count = 1 + len(chosen)
        return f"Download {count} series ({formatting.size_mb(size)})" if chosen else f"Download the annotation only ({formatting.size_mb(size)})"

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
            self.images_of, self.images_percent_of, self.images_text_of = _images_of(
                list(self.saved), status, self.percent_of
            )
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
        if series.modality in references.ANNOTATION_MODALITIES and not confirmed:
            # An annotation belongs to other images: offer to fetch them along with it.
            return DownloadState.ask_bundle(uid)
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
    def follow(self):
        """Show downloads another page queued (the PACS search), and watch them."""
        manager = downloader.get_manager()
        self._apply(manager.snapshot(), None)
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

    # -- downloading an annotation together with the images it belongs to ---------

    @rx.event(background=True)
    async def ask_bundle(self, uid: str):
        """Open the dialog for annotation `uid`, read what it refers to, list it.

        A background task: reading the files from IDC takes seconds, and the
        dialog's Cancel must work meanwhile.
        """
        rows = await asyncio.to_thread(idc_service.series_by_uid, [uid])
        row = rows.get(uid)
        if row is None:
            yield rx.toast.error("This series is not in the IDC index.")
            return
        series = Series.from_index(row)
        async with self:
            self.bundle_uid = uid
            self.bundle_title = _title(series)
            self.bundle_subtitle = _subtitle(series)
            self.bundle_loading = True
            self.bundle_note = ""
            self.bundle_rows = []
            self.bundle_selected = {}
            self._bundle_rows = {uid: row}
        note = ""
        try:
            needed = await asyncio.to_thread(references.peek_series, series.aws_url, uid)
        except references.ReferencesUnavailable as error:
            needed = set()
            note = str(error)
        candidates = {}
        if needed:
            candidates = await asyncio.to_thread(idc_service.series_by_uid, sorted(needed))
            preselect = True
            missing_from_index = sorted(needed - set(candidates))
            if missing_from_index:
                note = f"{len(missing_from_index)} referenced series are not in the IDC index."
        else:
            # Could not tell: offer the image series of the same study, unticked.
            study = [
                r for r in await asyncio.to_thread(
                    idc_service.image_series_in_study, str(row.get("StudyInstanceUID") or ""), references.IMAGE_MODALITIES
                )
                if r["SeriesInstanceUID"] != uid
            ]
            candidates = {r["SeriesInstanceUID"]: r for r in study}
            preselect = False
            if study:
                note = (note + " " if note else "") + "These are the image series of the same study; tick the ones to download."
            elif not note:
                note = "This object does not say which images it belongs to."
        async with self:
            if self.bundle_uid != uid:
                return  # closed meanwhile
            manager = downloader.get_manager()
            jobs = {job["series"].uid: job for job in manager.snapshot()["jobs"]}
            rows_out, selected = [], {}
            for ref, r in candidates.items():
                s = Series.from_index(r)
                status, percent, extra = queue_status(ref, manager, jobs)
                rows_out.append(Wanted(uid=ref, title=_title(s), subtitle=_subtitle(s), status=status, percent=percent, note=extra))
                selected[ref] = preselect and status not in ("done", *ACTIVE)
            self.bundle_rows = rows_out
            self.bundle_selected = selected
            self._bundle_rows = {**self._bundle_rows, **candidates}
            self.bundle_note = note
            self.bundle_loading = False

    @rx.event
    def toggle_bundle(self, uid: str, checked: bool):
        self.bundle_selected = {**self.bundle_selected, uid: bool(checked)}

    @rx.event
    def confirm_bundle(self, only_annotation: bool = False):
        """Queue the annotation and, unless `only_annotation`, the ticked image series."""
        uid = self.bundle_uid
        if not uid:
            return None
        manager = downloader.get_manager()
        queued = 0
        wanted = [uid]
        if not only_annotation:
            wanted += [w.uid for w in self.bundle_rows if self.bundle_selected.get(w.uid)]
        for ref in wanted:
            row = self._bundle_rows.get(ref)
            if row is not None and manager.add(Series.from_index(row)) == "queued":
                queued += 1
        self.bundle_uid = ""
        self.bundle_rows = []
        self.bundle_selected = {}
        self._bundle_rows = {}
        self._apply(manager.snapshot(), None)
        events = [self._start_watching(manager)]
        if queued:
            events.append(rx.toast.info(f"Queued {queued} series." if queued > 1 else "Queued 1 series."))
        else:
            events.append(rx.toast.info("Already downloaded — see Downloads."))
        return [e for e in events if e is not None]

    @rx.event
    def cancel_bundle(self):
        self.bundle_uid = ""
        self.bundle_loading = False
        self.bundle_rows = []
        self.bundle_selected = {}
        self._bundle_rows = {}

    # -- the images an annotation belongs to --------------------------------------

    @rx.event
    async def check_companions(self, uid: str, asked: bool = False):
        """Read what the downloaded annotation `uid` refers to and offer what is missing.

        Runs after an annotation finishes downloading, and from the Downloads
        page (`asked`), where "nothing missing" is worth a word.
        """
        manager = downloader.get_manager()
        collection = manager.collection()
        if not collection.has(uid):
            return rx.toast.error("That series is not in the DICOM folder.") if asked else None
        try:
            needed = await asyncio.to_thread(references.series_referenced_by, collection.own_dir / uid, uid)
        except references.ReferencesUnavailable as error:
            return rx.toast.error(str(error)) if asked else None
        missing = sorted(u for u in needed if not collection.has(u))
        if not missing:
            if asked:
                return rx.toast.info(
                    "Every series this annotation refers to is downloaded."
                    if needed else "This object does not say which images it belongs to."
                )
            return None
        rows = await asyncio.to_thread(idc_service.series_by_uid, missing)
        jobs = {job["series"].uid: job for job in manager.snapshot()["jobs"]}
        companions = []
        for ref in missing:
            row = rows.get(ref)
            if row is None:
                companions.append(Wanted(uid=ref, title=f"Series …{ref[-12:]}", subtitle="Not in the IDC index", in_index=False))
                continue
            s = Series.from_index(row)
            status, percent, note = queue_status(ref, manager, jobs)
            companions.append(Wanted(uid=ref, title=_title(s), subtitle=_subtitle(s), status=status, percent=percent, note=note))
        if not any(w.in_index for w in companions):
            return rx.toast.info("The images this annotation refers to are not in the IDC index.") if asked else None
        saved = next((s for s in self.saved if s.uid == uid), None)
        self.companion_uid = uid
        self.companion_title = saved.title if saved else f"Series …{uid[-12:]}"
        self.companions = companions
        self._companion_rows = rows
        return None

    @rx.event
    def download_companions(self):
        """Queue the series the annotation belongs to, then watch them like any download."""
        manager = downloader.get_manager()
        queued = 0
        for w in self.companions:
            if w.in_index and w.status not in ("done", *ACTIVE):
                manager.add(Series.from_index(self._companion_rows[w.uid]))
                queued += 1
        self.companion_uid = ""
        self.companions = []
        self._companion_rows = {}
        self._apply(manager.snapshot(), None)
        events = [self._start_watching(manager)]
        if queued:
            events.append(rx.toast.info(f"Queued {queued} series the annotation belongs to."))
        return [e for e in events if e is not None]

    @rx.event
    def dismiss_companions(self):
        self.companion_uid = ""
        self.companions = []
        self._companion_rows = {}
