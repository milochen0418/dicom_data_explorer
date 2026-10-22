"""What a downloaded annotation object (SEG, RTSTRUCT, SR, PR) says about the images it is for.

DICOM derived objects point back at their source images; the images never
point forward. So after an annotation is downloaded, its own header tells
which series to fetch so a viewer can show it on them.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from urllib.parse import quote

import requests

# Modalities whose objects annotate other series (as the IDC index names them).
ANNOTATION_MODALITIES = frozenset({"SEG", "RTSTRUCT", "SR", "PR", "ANN", "KO", "RTDOSE", "RTPLAN"})
# Series an annotation can belong to.
IMAGE_MODALITIES = frozenset({"CT", "MR", "PT", "NM", "US", "CR", "DX", "MG", "XA", "RF", "OT"})
# Annotation objects are small; anything bigger is not read ahead of its download.
PEEK_MAX_BYTES = 64 * 1024 * 1024
PEEK_MAX_FILES = 8


class ReferencesUnavailable(Exception):
    """The references cannot be read; the message says why, for the user."""


def _pydicom():
    try:
        import pydicom
    except ImportError as error:
        raise ReferencesUnavailable(
            "This app cannot read DICOM headers: pydicom is not installed. "
            "Reinstall the app to add it."
        ) from error
    return pydicom


def _text(ds, keyword: str) -> str:
    value = ds.get(keyword)
    return "" if value is None else str(value).strip()


def referenced_series_uids(ds) -> set[str]:
    """SeriesInstanceUIDs the object names as its source images.

    Looks where each kind of object keeps them: ReferencedSeriesSequence (SEG,
    presentation states), the RT referenced study and series tree (RTSTRUCT)
    and the evidence sequence (SR). Empty when it names none.
    """
    found = {
        _text(item, "SeriesInstanceUID")
        for item in ds.get("ReferencedSeriesSequence", [])
        if _text(item, "SeriesInstanceUID")
    }
    for referenced in ds.get("ReferencedFrameOfReferenceSequence", []):
        for study in referenced.get("RTReferencedStudySequence", []):
            for series in study.get("RTReferencedSeriesSequence", []):
                if _text(series, "SeriesInstanceUID"):
                    found.add(_text(series, "SeriesInstanceUID"))
    for study in ds.get("CurrentRequestedProcedureEvidenceSequence", []):
        for series in study.get("ReferencedSeriesSequence", []):
            if _text(series, "SeriesInstanceUID"):
                found.add(_text(series, "SeriesInstanceUID"))
    return found


def series_referenced_by(folder: str | Path, own_uid: str = "") -> set[str]:
    """Series the DICOM files in `folder` refer to, other than `own_uid` (the folder's own series).

    Raises ReferencesUnavailable when pydicom is missing (imported here, not at
    import time, so an app container installed before it became a dependency
    still runs).
    """
    pydicom = _pydicom()
    found: set[str] = set()
    folder = Path(folder)
    try:
        paths = sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))
    except OSError as error:
        logging.warning("Cannot read %s for references: %s", folder, error)
        return found
    for path in paths:
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True)
        except Exception:  # noqa: BLE001 - not a DICOM file, or a broken one
            continue
        found |= referenced_series_uids(ds)
    found.discard(own_uid)
    return found


def peek_series(aws_url: str, own_uid: str = "", session: requests.Session | None = None) -> set[str]:
    """Series the annotation at `aws_url` (an IDC series) refers to, read before downloading it.

    Fetches its files into memory (they are small) and parses their headers.
    Raises ReferencesUnavailable when they cannot be read.
    """
    from dicom_data_explorer.services.sources import idc

    pydicom = _pydicom()
    session = session or requests.Session()
    try:
        bucket, prefix = idc.parse_s3_url(aws_url)
        objects = idc.list_objects(session, bucket, prefix)
    except Exception as error:  # noqa: BLE001 - network or a bad URL
        raise ReferencesUnavailable(f"IDC could not be reached: {error}") from error
    found: set[str] = set()
    read = 0
    for key, size in objects[:PEEK_MAX_FILES]:
        if size > PEEK_MAX_BYTES:
            continue
        try:
            resp = session.get(f"https://{bucket}.s3.amazonaws.com/{quote(key)}", timeout=120)
            resp.raise_for_status()
            ds = pydicom.dcmread(io.BytesIO(resp.content), stop_before_pixels=True)
        except Exception as error:  # noqa: BLE001 - one unreadable file is not fatal
            logging.warning("Cannot read %s ahead of its download: %s", key, error)
            continue
        read += 1
        found |= referenced_series_uids(ds)
    if not read:
        raise ReferencesUnavailable("None of the annotation's files could be read.")
    found.discard(own_uid)
    return found
