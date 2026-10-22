"""State of the PACS search on the Find series page.

A PACS only answers what it is asked, so the page asks for a patient, an
accession number or a date range, lists the matching studies, and lists a
study's series when it is opened. "All PACS" asks every PACS at once and
merges the answers by UID, noting which PACS has what.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import reflex as rx

from dicom_data_explorer import formatting
from dicom_data_explorer.services import downloader, sources
from dicom_data_explorer.services.sources.base import Series, Study, StudyQuery
from dicom_data_explorer.states.download_state import DownloadState

IDC = "idc"
ALL = "all"
FIELDS = ("patient_id", "patient_name", "accession", "modality", "description", "date_preset", "date_from", "date_to")
DATE_PRESETS = [
    ("7d", "Last 7 days", 6),
    ("30d", "Last 30 days", 29),
    ("365d", "Last 12 months", 364),
    ("today", "Today", 0),
    ("custom", "Custom range", None),
    ("any", "Any date", None),
]


@dataclasses.dataclass
class Choice:
    value: str = ""
    label: str = ""


@dataclasses.dataclass
class StudyCard:
    uid: str = ""
    patient: str = ""
    patient_id: str = ""
    date: str = ""
    description: str = ""
    modalities: str = ""
    counts: str = ""
    found_on: str = ""


@dataclasses.dataclass
class SeriesCard:
    uid: str = ""
    modality: str = ""
    description: str = ""
    date: str = ""
    images: str = ""
    found_on: str = ""


def _name(pn: str) -> str:
    """A DICOM person name ("WANG^XIAOMING") as people write it."""
    return " ".join(part for part in pn.replace("^", " ").split()) if pn else ""


def _date(da: str) -> str:
    return f"{da[:4]}-{da[4:6]}-{da[6:8]}" if len(da) == 8 and da.isdigit() else da


def _describe(source_id: str, exc: BaseException) -> str:
    source = sources.get(source_id)
    text = source.describe_error(exc) if source else None
    return text or f"{sources.label_of(source_id)}: {exc or exc.__class__.__name__}"


def _merge(kept, new):
    """The same study or series from another PACS: fill in what the first one
    left blank, add the modalities it lacks, keep the larger counts."""
    changes = {}
    for f in dataclasses.fields(kept):
        a, b = getattr(kept, f.name), getattr(new, f.name)
        if isinstance(a, list):
            if extra := [x for x in b if x not in a]:
                changes[f.name] = a + extra
        elif isinstance(a, (int, float)):
            if b > a:
                changes[f.name] = b
        elif not a and b:
            changes[f.name] = b
    return dataclasses.replace(kept, **changes) if changes else kept


def _ask_all(ids: list[str], ask):
    """Run `ask(source)` on each source in parallel: (answers by source id, problems)."""
    answers, problems = {}, []
    with ThreadPoolExecutor(max_workers=max(1, len(ids))) as pool:
        futures = {sid: pool.submit(ask, sources.get(sid)) for sid in ids if sources.get(sid)}
    for sid, future in futures.items():
        try:
            answers[sid] = future.result()
        except Exception as exc:  # noqa: BLE001 - shown next to the results
            logging.exception("Asking %s failed", sid)
            problems.append(_describe(sid, exc))
    return answers, problems


class PacsSearchState(rx.State):
    source: str = IDC  # "idc", a PACS id, or "all"
    choices: list[Choice] = []

    patient_id: str = ""
    patient_name: str = ""
    accession: str = ""
    modality: str = ""
    description: str = ""
    date_preset: str = "30d"
    date_from: str = ""  # YYYY-MM-DD, for a custom range
    date_to: str = ""
    date_choices: list[Choice] = [Choice(v, label) for v, label, _ in DATE_PRESETS]

    searching: bool = False
    searched: bool = False
    error: str = ""
    problems: list[str] = []  # PACS that could not answer
    capped: bool = False
    studies: list[StudyCard] = []

    open_study: str = ""
    series_loading: bool = False
    series: list[SeriesCard] = []
    series_problems: list[str] = []

    _found_on: dict[str, list[str]] = {}  # study UID -> the PACS that have it
    _series: dict[str, dict] = {}  # series UID -> its Series fields, to queue it

    @rx.var
    def on_pacs(self) -> bool:
        return self.source != IDC

    @rx.var
    def source_label(self) -> str:
        if self.source == ALL:
            return "all PACS"
        return sources.label_of(self.source)

    @rx.event
    def load_sources(self):
        pacs = sources.pacs()
        choices = [Choice(IDC, "IDC (public)")] + [Choice(s.id, s.label) for s in pacs]
        if len(pacs) > 1:
            choices.append(Choice(ALL, "All PACS"))
        self.choices = choices
        if self.source not in {c.value for c in choices}:
            self.source = IDC

    @rx.event
    def set_source(self, value: str):
        if value != self.source:
            self.source = value
            self.studies, self.searched, self.error, self.problems = [], False, "", []
            self.open_study, self.series = "", []

    @rx.event
    def set_field(self, name: str, value: str):
        if name in FIELDS:
            setattr(self, name, value)

    @rx.event
    def clear_form(self):
        for name in ("patient_id", "patient_name", "accession", "modality", "description", "date_from", "date_to"):
            setattr(self, name, "")
        self.date_preset = "30d"

    def _query(self) -> StudyQuery | str:
        """The form as a query, or what is missing."""
        start = end = ""
        days = next((d for v, _, d in DATE_PRESETS if v == self.date_preset), None)
        if days is not None:
            today = date.today()
            start, end = (today - timedelta(days=days)).strftime("%Y%m%d"), today.strftime("%Y%m%d")
        elif self.date_preset == "custom":
            start, end = self.date_from.replace("-", ""), self.date_to.replace("-", "")
            if start and end and start > end:
                return "The range starts after it ends."
        query = StudyQuery(
            patient_id=self.patient_id.strip(),
            patient_name=self.patient_name.strip(),
            accession=self.accession.strip(),
            date_from=start,
            date_to=end,
            modality=self.modality.strip().upper(),
            description=self.description.strip(),
        )
        # A PACS serves a whole hospital: never ask it for everything.
        if not (query.patient_id or query.patient_name or query.accession or start or end):
            return "Enter a patient ID, a name, an accession number or a date range."
        return query

    @rx.event(background=True)
    async def search(self):
        async with self:
            query = self._query()
            if isinstance(query, str):
                self.error = query
                return
            ids = [s.id for s in sources.pacs()] if self.source == ALL else [self.source]
            self.searching, self.error, self.problems = True, "", []
            self.studies, self.searched = [], False  # no stale rows to click meanwhile
            self.open_study, self.series = "", []
        answers, problems = await asyncio.to_thread(_ask_all, ids, lambda src: src.search(query))
        merged: dict[str, Study] = {}
        found_on: dict[str, list[str]] = {}
        for sid in ids:
            for study in answers[sid].studies if sid in answers else []:
                merged[study.uid] = _merge(merged[study.uid], study) if study.uid in merged else study
                found_on.setdefault(study.uid, []).append(sid)
        capped = [sources.label_of(sid) for sid, result in answers.items() if result.capped]
        cards = [
            StudyCard(
                uid=s.uid,
                patient=_name(s.patient_name) or "(no name)",
                patient_id=s.patient_id,
                date=_date(s.date),
                description=s.description,
                modalities=", ".join(s.modalities),
                counts=" · ".join(
                    part for part in (
                        f"{formatting.count(s.series_count)} series" if s.series_count else "",
                        f"{formatting.count(s.image_count)} images" if s.image_count else "",
                    ) if part
                ),
                found_on=", ".join(sources.label_of(sid) for sid in found_on[s.uid]),
            )
            for s in sorted(merged.values(), key=lambda s: s.date, reverse=True)
        ]
        async with self:
            self.studies = cards
            self._found_on = found_on
            self.problems = problems + [f"{label}: only the first studies are shown. Narrow the search." for label in capped]
            self.capped = bool(capped)
            self.searching = False
            self.searched = True

    @rx.event(background=True)
    async def toggle_study(self, study_uid: str):
        async with self:
            if self.open_study == study_uid:
                self.open_study, self.series = "", []
                return
            self.open_study, self.series, self.series_problems = study_uid, [], []
            self.series_loading = True
            ids = list(self._found_on.get(study_uid, []))
        answers, problems = await asyncio.to_thread(_ask_all, ids, lambda src: src.series_of(study_uid))
        merged: dict[str, Series] = {}
        found_on: dict[str, list[str]] = {}
        for sid in ids:
            for s in answers.get(sid, []):  # the first PACS that has a series is the one it downloads from
                merged[s.uid] = _merge(merged[s.uid], s) if s.uid in merged else s
                found_on.setdefault(s.uid, []).append(sid)
        async with self:
            if self.open_study != study_uid:
                return  # another study was opened meanwhile
            self._series = {**self._series, **{uid: dataclasses.asdict(s) for uid, s in merged.items()}}
            self.series = [
                SeriesCard(
                    uid=s.uid,
                    modality=s.modality,
                    description=s.description or "(no description)",
                    date=_date(s.date),
                    images=formatting.count(s.images) if s.images else "—",
                    found_on=", ".join(sources.label_of(sid) for sid in found_on[s.uid]),
                )
                for s in merged.values()
            ]
            self.series_problems = problems
            self.series_loading = False

    @rx.event
    def download(self, series_uid: str):
        fields = self._series.get(series_uid)
        if fields is None:
            return rx.toast.error("Open the study again, then download.")
        outcome = downloader.get_manager().add(Series(**fields))
        if outcome == "downloaded":
            return [rx.toast.info("Already downloaded — see Downloads."), DownloadState.follow]
        return DownloadState.follow

    @rx.event
    def download_study(self):
        manager = downloader.get_manager()
        queued = sum(
            1 for card in self.series
            if card.uid in self._series and manager.add(Series(**self._series[card.uid])) == "queued"
        )
        message = f"Queued {queued} series." if queued else "Every series of this study is downloaded or in the queue."
        return [rx.toast.info(message), DownloadState.follow]
