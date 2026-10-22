"""IDC (NCI Imaging Data Commons): series from the idc-index parquet, files from public S3."""

from __future__ import annotations

import contextlib
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlparse

import requests

from dicom_data_explorer.services.sources.base import DownloadError, RemoteFile, Series, Source

CHUNK_BYTES = 256 * 1024


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


class IDCSource(Source):
    id = "idc"
    label = "IDC"

    def find_series(self, uids, study_uid=""):
        # Imported here: the index loads DuckDB and the parquet file.
        from dicom_data_explorer.services import idc_service

        return {uid: Series.from_index(row) for uid, row in idc_service.series_by_uid(uids).items()}

    def list_files(self, series, session):
        bucket, prefix = parse_s3_url(series.aws_url)
        return [
            RemoteFile(
                rel=_relative(key, prefix),
                size=size,
                ref=f"https://{bucket}.s3.amazonaws.com/{quote(key, safe='/')}",
            )
            for key, size in list_objects(session, bucket, prefix)
        ]

    @contextlib.contextmanager
    def open_file(self, remote, session):
        with session.get(remote.ref, stream=True, timeout=(10, 60)) as resp:
            resp.raise_for_status()
            yield resp.iter_content(CHUNK_BYTES)

    def origin(self, series):
        return series.aws_url

    def is_transient(self, exc):
        if isinstance(exc, requests.HTTPError):
            code = exc.response.status_code if exc.response is not None else 0
            return code == 429 or code >= 500
        return isinstance(
            exc, (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError)
        )

    def describe_error(self, exc):
        if isinstance(exc, requests.Timeout):
            return "IDC stopped responding."
        if isinstance(exc, requests.ConnectionError):
            return "Can't reach IDC. Check the internet connection."
        if isinstance(exc, requests.HTTPError):
            code = exc.response.status_code if exc.response is not None else "?"
            return f"IDC answered with HTTP {code}."
        return None
