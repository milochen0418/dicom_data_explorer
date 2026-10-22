"""Where series come from: IDC, and the PACS set up on the PACS page.

Each source can look series up by UID and fetch their files (sources/base.py).
IDC is always there; reload() adds one source per PACS card (pacs_config).
"""

from __future__ import annotations

import logging

from dicom_data_explorer.services.sources.base import Series, Source
from dicom_data_explorer.services.sources.idc import IDCSource

_SOURCES: dict[str, Source] = {}  # IDC first, then the PACS in the order of their cards


def register(source: Source) -> None:
    _SOURCES[source.id] = source


def get(source_id: str) -> Source | None:
    return _SOURCES.get(source_id)


def pacs() -> list[Source]:
    return [s for s in _SOURCES.values() if s.id != "idc"]


def label_of(source_id: str) -> str:
    """A source's name for the user (its id when it was removed meanwhile)."""
    source = _SOURCES.get(source_id)
    return source.label if source else source_id


def reload() -> None:
    """Read the PACS cards again (at startup and after the PACS page saves)."""
    from dicom_data_explorer.services import pacs_config
    from dicom_data_explorer.services.sources.dicomweb import DICOMwebSource
    from dicom_data_explorer.services.sources.dimse import DIMSESource

    fresh: dict[str, Source] = {"idc": _SOURCES.get("idc") or IDCSource()}
    for profile in pacs_config.load():
        fresh[profile.id] = DICOMwebSource(profile) if profile.kind == pacs_config.DICOMWEB else DIMSESource(profile)
    _SOURCES.clear()
    _SOURCES.update(fresh)


def find_series(uids: list[str], study_uid: str = "") -> dict[str, Series]:
    """The series any source has, by UID; the first source that has one wins.

    A PACS that only answers level by level needs `study_uid` to find a series.
    """
    found: dict[str, Series] = {}
    for source in _SOURCES.values():
        missing = [uid for uid in uids if uid not in found]
        if not missing:
            break
        try:
            found.update(source.find_series(missing, study_uid))
        except Exception:  # noqa: BLE001 - one unreachable PACS must not hide the others
            logging.exception("Looking up series in %s failed", source.label)
    return found


register(IDCSource())
