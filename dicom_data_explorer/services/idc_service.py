"""Queries over the IDC series index: one row per series, about a million rows.

Everything runs in DuckDB directly on the parquet file shipped with
``idc-index-data``.  The dropdown counts and the match total come from the
(collection, modality, body part) combinations — about a thousand rows —
grouped once per search text and cached, so changing a dropdown only costs
the query for the visible page.
"""

from __future__ import annotations

import dataclasses
import functools
import threading
from collections import Counter

import duckdb
import idc_index_data

PARQUET_PATH = str(idc_index_data.IDC_INDEX_PARQUET_FILEPATH)

SORTS = {
    "newest": "SeriesDate DESC NULLS LAST",
    "oldest": "SeriesDate ASC NULLS LAST",
    "smallest": "series_size_MB ASC NULLS LAST",
    "largest": "series_size_MB DESC NULLS LAST",
    "most_images": "instanceCount DESC NULLS LAST",
    "collection": "collection_id ASC, SeriesDate DESC NULLS LAST",
}
DEFAULT_SORT = "newest"

# Free text matches any of these columns, case-insensitively.
_TEXT_MATCH = (
    "contains(lower(concat_ws(' ', SeriesDescription, collection_id, "
    "StudyDescription, PatientID, SeriesInstanceUID)), ?)"
)

_ROW_COLUMNS = (
    "SeriesInstanceUID, StudyInstanceUID, collection_id, PatientID, PatientSex, "
    "PatientAge, Modality, BodyPartExamined, SeriesDate, SeriesDescription, "
    "StudyDescription, Manufacturer, instanceCount, series_size_MB, "
    "series_aws_url, license_short_name, source_DOI"
)

_conn: duckdb.DuckDBPyConnection | None = None
_lock = threading.Lock()


def _query(sql: str, params: list | tuple = ()) -> list[dict]:
    global _conn
    with _lock:
        if _conn is None:
            _conn = duckdb.connect()
            _conn.execute(
                f"CREATE VIEW series AS SELECT * FROM read_parquet('{PARQUET_PATH}')"
            )
        cursor = _conn.execute(sql, list(params))
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]


@dataclasses.dataclass(frozen=True)
class Filters:
    collection: str = ""
    modality: str = ""
    body_part: str = ""
    text: str = ""

    @property
    def needle(self) -> str:
        return " ".join(self.text.lower().split())

    def where(self) -> tuple[str, list]:
        conditions, params = ["TRUE"], []
        for column, value in (
            ("collection_id", self.collection),
            ("Modality", self.modality),
            ("BodyPartExamined", self.body_part),
        ):
            if value:
                conditions.append(f"{column} = ?")
                params.append(value)
        if self.needle:
            conditions.append(_TEXT_MATCH)
            params.append(self.needle)
        return " AND ".join(conditions), params


@dataclasses.dataclass(frozen=True)
class Combo:
    collection: str
    modality: str
    body_part: str
    count: int
    size_mb: float


@functools.lru_cache(maxsize=64)
def _combos(needle: str) -> tuple[Combo, ...]:
    where, params = Filters(text=needle).where()
    rows = _query(
        "SELECT collection_id, Modality, BodyPartExamined, count(*) AS n, "
        f"sum(series_size_MB) AS mb FROM series WHERE {where} GROUP BY ALL",
        params,
    )
    return tuple(
        Combo(
            collection=r["collection_id"] or "",
            modality=r["Modality"] or "",
            body_part=r["BodyPartExamined"] or "",
            count=int(r["n"]),
            size_mb=float(r["mb"] or 0),
        )
        for r in rows
    )


@dataclasses.dataclass
class Summary:
    total: int
    size_mb: float
    # Each dropdown's choices with how many series they would give, counted
    # with the *other* filters applied (so a choice never leads to nothing).
    collections: list[tuple[str, int]]
    modalities: list[tuple[str, int]]
    body_parts: list[tuple[str, int]]


def summarize(f: Filters) -> Summary:
    combos = _combos(f.needle)
    wanted = {"collection": f.collection, "modality": f.modality, "body_part": f.body_part}

    def keep(c: Combo, ignore: str | None) -> bool:
        return all(
            not value or dim == ignore or getattr(c, dim) == value
            for dim, value in wanted.items()
        )

    facets: dict[str, Counter] = {dim: Counter() for dim in wanted}
    total, size_mb = 0, 0.0
    for c in combos:
        for dim in wanted:
            if getattr(c, dim) and keep(c, dim):
                facets[dim][getattr(c, dim)] += c.count
        if keep(c, None):
            total += c.count
            size_mb += c.size_mb
    return Summary(
        total=total,
        size_mb=size_mb,
        collections=sorted(facets["collection"].items()),
        modalities=sorted(facets["modality"].items()),
        body_parts=sorted(facets["body_part"].items()),
    )


def search(f: Filters, sort: str = DEFAULT_SORT, limit: int = 25, offset: int = 0) -> list[dict]:
    where, params = f.where()
    order = SORTS.get(sort, SORTS[DEFAULT_SORT])
    return _query(
        f"SELECT {_ROW_COLUMNS} FROM series WHERE {where} "
        f"ORDER BY {order}, SeriesInstanceUID LIMIT ? OFFSET ?",
        [*params, int(limit), int(offset)],
    )


def series_by_uid(uids: list[str]) -> dict[str, dict]:
    if not uids:
        return {}
    marks = ", ".join("?" for _ in uids)
    rows = _query(
        f"SELECT {_ROW_COLUMNS} FROM series WHERE SeriesInstanceUID IN ({marks})",
        list(uids),
    )
    return {r["SeriesInstanceUID"]: r for r in rows}


def viewer_url(study_uid: str, series_uid: str, modality: str) -> str:
    """IDC's own web viewer for one series (Slim for slides, OHIF otherwise)."""
    if not study_uid or not series_uid:
        return ""
    base = "https://viewer.imaging.datacommons.cancer.gov"
    if modality == "SM":
        return f"{base}/slim/studies/{study_uid}/series/{series_uid}"
    return (
        f"{base}/v3/viewer/?StudyInstanceUIDs={study_uid}"
        f"&initialSeriesInstanceUID={series_uid}"
    )
