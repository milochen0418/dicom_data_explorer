"""What a data source gives the download queue and the search page.

A source is a place series come from: IDC, or a PACS. The queue in
services/downloader.py is the same for all of them. It asks the source which
files a series has, then either reads each file through the source (IDC,
DICOMweb) or lets the source fetch the whole series and hand over each file
(C-GET, C-MOVE); it writes the files into the shared collection itself.
"""

from __future__ import annotations

import dataclasses
from contextlib import AbstractContextManager
from typing import Iterable, Protocol

import requests


class DownloadError(Exception):
    """A download cannot go on; the message is for the user."""


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
    size_mb: float = 0.0  # 0 when the source does not say
    study_uid: str = ""
    source: str = "idc"  # the id of its source in services/sources

    @classmethod
    def from_index(cls, row: dict) -> "Series":
        """From a row of the IDC index."""
        return cls(
            uid=str(row.get("SeriesInstanceUID") or ""),
            study_uid=str(row.get("StudyInstanceUID") or ""),
            aws_url=str(row.get("series_aws_url") or ""),
            collection=str(row.get("collection_id") or ""),
            modality=str(row.get("Modality") or ""),
            body_part=str(row.get("BodyPartExamined") or ""),
            description=str(row.get("SeriesDescription") or ""),
            date=str(row.get("SeriesDate") or ""),
            images=int(row.get("instanceCount") or 0),
            size_mb=float(row.get("series_size_MB") or 0),
        )


@dataclasses.dataclass(frozen=True)
class RemoteFile:
    rel: str  # where the file goes inside the series folder
    size: int  # bytes; 0 when the source does not say
    ref: str  # how the source finds it again (for IDC, its S3 URL)
    sop_class: str = ""  # what kind of object it is, when the source says


@dataclasses.dataclass
class StudyQuery:
    """The PACS search form. Dates are YYYYMMDD; text may use the * and ? wildcards."""

    patient_id: str = ""
    patient_name: str = ""
    accession: str = ""
    date_from: str = ""
    date_to: str = ""
    modality: str = ""
    description: str = ""

    def date_range(self) -> str:
        if not (self.date_from or self.date_to):
            return ""
        if self.date_from == self.date_to:
            return self.date_from
        return f"{self.date_from}-{self.date_to}"


@dataclasses.dataclass
class Study:
    uid: str
    source: str
    patient_id: str = ""
    patient_name: str = ""
    date: str = ""
    description: str = ""
    accession: str = ""
    modalities: list[str] = dataclasses.field(default_factory=list)
    series_count: int = 0
    image_count: int = 0


@dataclasses.dataclass
class SearchResult:
    studies: list[Study]
    capped: bool = False  # the PACS had more than it returned


class Sink(Protocol):
    """Where a source that fetches a whole series puts each file it receives."""

    def write(self, rel: str, data: bytes) -> None: ...

    def stopped(self) -> bool: ...


class Source:
    id = ""
    label = ""  # how the app names the source to the user
    # True: the queue reads each file through open_file, several at a time.
    # False: fetch() gets the whole series and hands each file to a Sink.
    per_file = True
    # Files read at the same time (per_file sources); 0 = as many as the queue runs.
    parallel = 0

    def find_series(self, uids: list[str], study_uid: str = "") -> dict[str, Series]:
        """The series this source has, by SeriesInstanceUID."""
        raise NotImplementedError

    def search(self, query: StudyQuery) -> SearchResult:
        """Studies matching the form (PACS sources)."""
        raise NotImplementedError

    def series_of(self, study_uid: str) -> list[Series]:
        """The series of one study (PACS sources)."""
        raise NotImplementedError

    def list_files(self, series: Series, session: requests.Session) -> list[RemoteFile]:
        """Every file of the series."""
        raise NotImplementedError

    def open_file(self, remote: RemoteFile, session: requests.Session) -> AbstractContextManager[Iterable[bytes]]:
        """The file's bytes in chunks (per_file sources). Raises on entry when it cannot be read."""
        raise NotImplementedError

    def fetch(self, series: Series, todo: list[RemoteFile], sink: Sink) -> None:
        """Fetch the files in `todo` and hand each to `sink` (sources that are not per_file)."""
        raise NotImplementedError

    def origin(self, series: Series) -> str:
        """What the collection records as the item's source; never a password."""
        return ""

    def is_transient(self, exc: BaseException) -> bool:
        """Whether trying again may help."""
        return False

    def describe_error(self, exc: BaseException) -> str | None:
        """A short message for the user, or None for the queue's generic one."""
        return None

    def test(self) -> list[tuple[str, str]]:
        """The "Test connection" checks: ("ok" | "fail" | "note", what it found) per check."""
        raise NotImplementedError
