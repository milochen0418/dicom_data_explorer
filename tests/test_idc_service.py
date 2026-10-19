"""Search and dropdown counts against the real IDC index that ships with idc-index-data."""

from dicom_data_explorer.services import idc_service
from dicom_data_explorer.services.idc_service import Filters


def count(where: str, params: list) -> int:
    return idc_service._query(f"SELECT count(*) AS n FROM series WHERE {where}", params)[0]["n"]


def test_totals_match_sql():
    f = Filters(modality="CT", body_part="CHEST")
    assert idc_service.summarize(f).total == count("Modality = ? AND BodyPartExamined = ?", ["CT", "CHEST"])


def test_each_dropdown_is_counted_without_its_own_filter():
    s = idc_service.summarize(Filters(modality="CT"))
    modalities = dict(s.modalities)
    assert modalities["CT"] == s.total
    assert modalities["MR"] == count("Modality = ?", ["MR"])  # still offered, with its own count
    assert all(n > 0 for _, n in s.collections)


def test_text_search_and_paging():
    f = Filters(text="  ChEsT ")
    total = idc_service.summarize(f).total
    assert 0 < total < count("TRUE", [])
    first = idc_service.search(f, "largest", limit=5)
    second = idc_service.search(f, "largest", limit=5, offset=5)
    assert len(first) == 5 and len(second) == 5
    assert not {r["SeriesInstanceUID"] for r in first} & {r["SeriesInstanceUID"] for r in second}
    assert first[0]["series_size_MB"] >= second[-1]["series_size_MB"]


def test_quotes_in_values_are_safe():
    assert idc_service.summarize(Filters(collection="x' OR '1'='1")).total == 0


def test_series_by_uid():
    row = idc_service.search(Filters(modality="MR"), limit=1)[0]
    found = idc_service.series_by_uid([row["SeriesInstanceUID"], "nope"])
    assert list(found) == [row["SeriesInstanceUID"]]
