"""State of the search page: filters, one page of results, and the details panel."""

from __future__ import annotations

import asyncio
import dataclasses
import logging

import reflex as rx

from dicom_data_explorer import formatting
from dicom_data_explorer.services import idc_service

PAGE_SIZE = 25


@dataclasses.dataclass
class SeriesRow:
    uid: str = ""
    collection: str = ""
    modality: str = ""
    body_part: str = ""
    description: str = ""
    date: str = ""
    images: int = 0
    size: str = ""
    patient: str = ""
    study: str = ""
    manufacturer: str = ""
    license: str = ""
    preview_url: str = ""


@dataclasses.dataclass
class Choice:
    value: str = ""
    label: str = ""


SORT_CHOICES = [
    Choice("newest", "Newest first"),
    Choice("oldest", "Oldest first"),
    Choice("smallest", "Smallest first"),
    Choice("largest", "Largest first"),
    Choice("most_images", "Most images"),
    Choice("collection", "Collection A–Z"),
]


def _row(r: dict) -> SeriesRow:
    patient = " · ".join(
        str(v) for v in (r.get("PatientID"), r.get("PatientSex"), r.get("PatientAge")) if v
    )
    return SeriesRow(
        uid=r["SeriesInstanceUID"],
        collection=r.get("collection_id") or "",
        modality=r.get("Modality") or "",
        body_part=r.get("BodyPartExamined") or "",
        description=r.get("SeriesDescription") or "",
        date=r.get("SeriesDate") or "",
        images=int(r.get("instanceCount") or 0),
        size=formatting.size_mb(r.get("series_size_MB")),
        patient=patient,
        study=r.get("StudyDescription") or "",
        manufacturer=r.get("Manufacturer") or "",
        license=r.get("license_short_name") or "",
        preview_url=idc_service.viewer_url(
            r.get("StudyInstanceUID") or "", r["SeriesInstanceUID"], r.get("Modality") or ""
        ),
    )


def _choices(pairs: list[tuple[str, int]], selected: str) -> list[Choice]:
    choices = [Choice(value, f"{value} ({formatting.count(n)})") for value, n in pairs]
    if selected and all(c.value != selected for c in choices):
        choices.insert(0, Choice(selected, f"{selected} (0)"))
    return choices


def _fetch(f: idc_service.Filters, sort: str, page: int):
    summary = idc_service.summarize(f)
    pages = max(1, -(-summary.total // PAGE_SIZE))
    page = min(max(page, 1), pages)
    rows = idc_service.search(f, sort, PAGE_SIZE, (page - 1) * PAGE_SIZE) if summary.total else []
    return summary, page, rows


class IDCState(rx.State):
    text: str = ""
    collection: str = ""
    modality: str = ""
    body_part: str = ""
    sort: str = idc_service.DEFAULT_SORT
    page: int = 1

    rows: list[SeriesRow] = []
    total: int = 0
    total_size: str = ""
    collections: list[Choice] = []
    modalities: list[Choice] = []
    body_parts: list[Choice] = []
    sort_choices: list[Choice] = SORT_CHOICES
    loading: bool = False
    loaded: bool = False
    error: str = ""

    details: SeriesRow = SeriesRow()
    details_open: bool = False
    _raw: dict[str, dict] = {}  # the visible page's index rows, by series UID

    @rx.var
    def page_count(self) -> int:
        return max(1, -(-self.total // PAGE_SIZE))

    @rx.var
    def has_filters(self) -> bool:
        return bool(self.text.strip() or self.collection or self.modality or self.body_part)

    @rx.var
    def total_text(self) -> str:
        return f"{formatting.count(self.total)} series"

    @rx.var
    def range_text(self) -> str:
        if not self.total:
            return ""
        first = (self.page - 1) * PAGE_SIZE + 1
        last = min(self.page * PAGE_SIZE, self.total)
        return f"showing {formatting.count(first)}–{formatting.count(last)}, {self.total_size} in total"

    @rx.event
    async def run_search(self):
        self.loading = True
        yield
        f = idc_service.Filters(self.collection, self.modality, self.body_part, self.text)
        try:
            summary, page, rows = await asyncio.to_thread(_fetch, f, self.sort, self.page)
        except Exception:  # noqa: BLE001 - shown on the page
            logging.exception("IDC search failed")
            self.error = "The search failed. Try again, or clear the filters."
            self.loading = False
            return
        self.error = ""
        self.page = page
        self._raw = {r["SeriesInstanceUID"]: r for r in rows}
        self.rows = [_row(r) for r in rows]
        self.total = summary.total
        self.total_size = formatting.size_mb(summary.size_mb)
        self.collections = _choices(summary.collections, self.collection)
        self.modalities = _choices(summary.modalities, self.modality)
        self.body_parts = _choices(summary.body_parts, self.body_part)
        self.loading = False
        self.loaded = True

    @rx.event
    def set_text(self, value: str):
        if value.strip() == self.text.strip():
            self.text = value
            return
        self.text = value
        self.page = 1
        return IDCState.run_search

    @rx.event
    def set_collection(self, value: str):
        self.collection = value
        self.page = 1
        return IDCState.run_search

    @rx.event
    def set_modality(self, value: str):
        self.modality = value
        self.page = 1
        return IDCState.run_search

    @rx.event
    def set_body_part(self, value: str):
        self.body_part = value
        self.page = 1
        return IDCState.run_search

    @rx.event
    def set_sort(self, value: str):
        self.sort = value
        self.page = 1
        return IDCState.run_search

    @rx.event
    def clear_filters(self):
        self.text = ""
        self.collection = ""
        self.modality = ""
        self.body_part = ""
        self.page = 1
        return IDCState.run_search

    @rx.event
    def go_to_page(self, page: int):
        self.page = page
        return IDCState.run_search

    @rx.event
    def show_details(self, uid: str):
        if uid in self._raw:
            self.details = _row(dict(self._raw[uid]))
            self.details_open = True

    @rx.event
    def close_details(self):
        self.details_open = False
