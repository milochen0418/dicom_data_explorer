"""All PACS: the same study from several PACS becomes one row."""

from __future__ import annotations

from dicom_data_explorer.services.sources.base import Series, Study
from dicom_data_explorer.states.pacs_search_state import _merge


def test_a_study_from_two_pacs_keeps_the_first_and_fills_in_its_blanks():
    first = Study(uid="1", source="a", patient_id="P1", modalities=["SEG"], image_count=1)
    second = Study(uid="1", source="b", patient_id="P1", description="Chest", modalities=["CT", "SEG"], image_count=30)
    merged = _merge(first, second)
    assert (merged.source, merged.description, merged.modalities, merged.image_count) == ("a", "Chest", ["SEG", "CT"], 30)


def test_a_series_still_downloads_from_the_first_pacs_that_has_it():
    first = Series(uid="1.1", aws_url="", source="a", modality="CT", images=29)
    second = Series(uid="1.1", aws_url="", source="b", modality="CT", description="Abdomen", images=29)
    merged = _merge(first, second)
    assert (merged.source, merged.description) == ("a", "Abdomen")
    assert _merge(first, first) is first
