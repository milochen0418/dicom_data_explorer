"""A PACS (or archive, or cloud store) that speaks DICOMweb: QIDO-RS to search,
WADO-RS to fetch.

Plain HTTP, so it needs no open port and works through NAT and VPNs. Images
are fetched one at a time in the transfer syntax the PACS stores them in,
which lets a download resume where it stopped.
"""

from __future__ import annotations

import contextlib
from email.message import Message

import requests

from dicom_data_explorer.services import pacs_config
from dicom_data_explorer.services.sources.base import (
    DownloadError, RemoteFile, SearchResult, Series, Source, Study, StudyQuery,
)

QIDO_JSON = "application/dicom+json"
WADO_STORED = 'multipart/related; type="application/dicom"; transfer-syntax=*'
WADO_DEFAULT = 'multipart/related; type="application/dicom"'
PAGE = 1000  # instances asked for per QIDO page
CHUNK_BYTES = 256 * 1024

STUDY_FIELDS = [
    "StudyDescription", "ModalitiesInStudy", "AccessionNumber",
    "NumberOfStudyRelatedSeries", "NumberOfStudyRelatedInstances",
]
SERIES_FIELDS = [
    "SeriesDescription", "SeriesDate", "BodyPartExamined", "SeriesNumber", "NumberOfSeriesRelatedInstances",
]


def _first(row: dict, tag: str) -> str:
    """The first value of a DICOM JSON attribute as text (a person name's alphabetic form)."""
    values = (row.get(tag) or {}).get("Value") or []
    if not values:
        return ""
    value = values[0]
    if isinstance(value, dict):
        return str(value.get("Alphabetic") or "")
    return str(value)


def _all(row: dict, tag: str) -> list[str]:
    return [str(v) for v in (row.get(tag) or {}).get("Value") or [] if v is not None]


def _int(row: dict, tag: str) -> int:
    try:
        return int(float(_first(row, tag) or 0))
    except ValueError:
        return 0


def multipart_parts(body: bytes, content_type: str) -> list[bytes]:
    """The bodies of a multipart/related answer; the whole body when it is not multipart."""
    header = Message()
    header["content-type"] = content_type
    boundary = header.get_param("boundary")
    if not header.get_content_type().startswith("multipart/") or not boundary:
        return [body] if body else []
    parts = []
    for chunk in body.split(b"--" + str(boundary).encode())[1:]:
        if chunk.startswith(b"--"):
            break  # the closing delimiter
        _, _, data = chunk.partition(b"\r\n\r\n")
        parts.append(data[:-2] if data.endswith(b"\r\n") else data)
    return parts


class DICOMwebSource(Source):
    parallel = 2  # images at a time: a hospital PACS is shared with its doctors

    def __init__(self, profile: pacs_config.PacsProfile):
        self.profile = profile
        self.id = profile.id
        self.label = profile.label

    @property
    def _qido(self) -> str:
        return self.profile.url.strip().rstrip("/")

    @property
    def _wado(self) -> str:
        return (self.profile.wado_url or self.profile.url).strip().rstrip("/")

    def _kw(self, accept: str) -> dict:
        """Headers, login and certificate checking for one request."""
        p = self.profile
        headers = {"Accept": accept}
        auth = None
        if p.auth == "basic":
            auth = (p.username, p.password)
        elif p.auth == "bearer":
            headers["Authorization"] = f"Bearer {p.token}"
        return {"headers": headers, "auth": auth, "verify": p.verify_tls, "timeout": (10, 60)}

    def _qido_get(self, session: requests.Session, path: str, params: dict) -> requests.Response:
        resp = session.get(f"{self._qido}{path}", params=params, **self._kw(QIDO_JSON))
        resp.raise_for_status()
        return resp

    @staticmethod
    def _rows(resp: requests.Response) -> list[dict]:
        return resp.json() if resp.status_code == 200 and resp.content.strip() else []

    def _series(self, row: dict, study_uid: str = "") -> Series:
        return Series(
            uid=_first(row, "0020000E"),
            aws_url="",
            study_uid=_first(row, "0020000D") or study_uid,
            modality=_first(row, "00080060"),
            body_part=_first(row, "00180015"),
            description=_first(row, "0008103E"),
            date=_first(row, "00080021"),
            images=_int(row, "00201209"),
            source=self.id,
        )

    # -- search -----------------------------------------------------------------

    def search(self, query: StudyQuery) -> SearchResult:
        cap = self.profile.max_results
        params: dict = {"limit": str(cap + 1), "includefield": STUDY_FIELDS}
        for key, value in (
            ("PatientID", query.patient_id), ("PatientName", query.patient_name),
            ("AccessionNumber", query.accession), ("StudyDate", query.date_range()),
            ("ModalitiesInStudy", query.modality), ("StudyDescription", query.description),
        ):
            if value:
                params[key] = value
        with requests.Session() as session:
            resp = self._qido_get(session, "/studies", params)
        rows = self._rows(resp)
        studies = [
            Study(
                uid=_first(r, "0020000D"),
                source=self.id,
                patient_id=_first(r, "00100020"),
                patient_name=_first(r, "00100010"),
                date=_first(r, "00080020"),
                description=_first(r, "00081030"),
                accession=_first(r, "00080050"),
                modalities=_all(r, "00080061"),
                series_count=_int(r, "00201206"),
                image_count=_int(r, "00201208"),
            )
            for r in rows
        ]
        capped = len(studies) > cap or resp.headers.get("Warning", "").startswith("299")
        return SearchResult([s for s in studies[:cap] if s.uid], capped)

    def series_of(self, study_uid: str) -> list[Series]:
        with requests.Session() as session:
            resp = self._qido_get(session, f"/studies/{study_uid}/series", {"includefield": SERIES_FIELDS})
        return [s for s in (self._series(r, study_uid) for r in self._rows(resp)) if s.uid]

    def find_series(self, uids: list[str], study_uid: str = "") -> dict[str, Series]:
        path = f"/studies/{study_uid}/series" if study_uid else "/series"
        params = {"SeriesInstanceUID": ",".join(uids), "includefield": SERIES_FIELDS}
        with requests.Session() as session:
            rows = self._rows(self._qido_get(session, path, params))
        found = {s.uid: s for s in (self._series(r, study_uid) for r in rows)}
        return {uid: s for uid, s in found.items() if uid in uids and s.study_uid}

    # -- download ---------------------------------------------------------------

    def list_files(self, series: Series, session: requests.Session) -> list[RemoteFile]:
        if not series.study_uid:
            raise DownloadError("This series has no study UID, so the PACS cannot list its images.")
        path = f"/studies/{series.study_uid}/series/{series.uid}/instances"
        files: dict[str, RemoteFile] = {}
        offset = 0
        while True:
            params = {"limit": str(PAGE), "offset": str(offset), "includefield": "SOPClassUID"}
            rows = self._rows(self._qido_get(session, path, params))
            fresh = 0
            for row in rows:
                sop = _first(row, "00080018")
                if sop and sop not in files:
                    files[sop] = RemoteFile(
                        rel=f"{sop}.dcm",
                        size=0,
                        ref=f"{self._wado}/studies/{series.study_uid}/series/{series.uid}/instances/{sop}",
                        sop_class=_first(row, "00080016"),
                    )
                    fresh += 1
            # A short page is the last; a server that ignores offset sends nothing new.
            if len(rows) < PAGE or not fresh:
                return list(files.values())
            offset += PAGE

    @contextlib.contextmanager
    def open_file(self, remote: RemoteFile, session: requests.Session):
        resp = session.get(remote.ref, **self._kw(WADO_STORED))
        if resp.status_code == 406:  # cannot send the stored syntax: take its default
            resp = session.get(remote.ref, **self._kw(WADO_DEFAULT))
        resp.raise_for_status()
        parts = multipart_parts(resp.content, resp.headers.get("Content-Type", ""))
        if not parts:
            raise DownloadError(f"{self.label} sent an empty answer for an image.")
        data = parts[0]
        yield (data[i:i + CHUNK_BYTES] for i in range(0, len(data), CHUNK_BYTES))

    def origin(self, series: Series) -> str:
        return f"dicomweb:{self.id}"

    # -- errors and the connection test ---------------------------------------------

    def is_transient(self, exc: BaseException) -> bool:
        if isinstance(exc, requests.HTTPError):
            code = exc.response.status_code if exc.response is not None else 0
            return code == 429 or code >= 500
        if isinstance(exc, requests.exceptions.SSLError):
            return False
        return isinstance(
            exc, (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError)
        )

    def describe_error(self, exc: BaseException) -> str | None:
        if isinstance(exc, requests.exceptions.SSLError):
            return f"{self.label}'s certificate is not trusted. Turn off the certificate check only on a network you trust."
        if isinstance(exc, requests.Timeout):
            return f"{self.label} stopped responding."
        if isinstance(exc, requests.ConnectionError):
            return f"Can't reach {self.label} at {self._qido}."
        if isinstance(exc, requests.HTTPError):
            code = exc.response.status_code if exc.response is not None else "?"
            reasons = {
                401: "refused the login (HTTP 401). Check the user name, password or token.",
                403: "says this login may not do that (HTTP 403).",
                404: "does not have it (HTTP 404).",
                413: "wants more search conditions (HTTP 413).",
            }
            return f"{self.label} {reasons.get(code, f'answered with HTTP {code}.')}"
        return None

    def test(self) -> list[tuple[str, str]]:
        params = {"PatientID": "DDE_TEST_NONE", "limit": "1"}
        try:
            with requests.Session() as session:
                resp = session.get(f"{self._qido}/studies", params=params, **self._kw(QIDO_JSON))
        except Exception as exc:  # noqa: BLE001 - reported to the user
            return [("fail", self.describe_error(exc) or str(exc))]
        code = resp.status_code
        if code in (200, 204):
            return [("ok", f"Search (QIDO-RS) works: HTTP {code}."), ("note", "Downloads use WADO-RS at the same address.")]
        if code == 413:
            return [("note", "The PACS answers, but wants more search conditions (HTTP 413).")]
        message = self.describe_error(requests.HTTPError(response=resp)) or f"HTTP {code}"
        return [("fail", f"Search (QIDO-RS) failed: {message}")]
