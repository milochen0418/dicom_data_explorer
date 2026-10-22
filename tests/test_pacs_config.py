"""The PACS cards: kept in the app's own data folder, readable by its owner only."""

from __future__ import annotations

import stat

import pytest

from dicom_data_explorer.services import pacs_config
from dicom_data_explorer.services.pacs_config import PacsProfile


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DICOM_EXPLORER_DATA_DIR", str(tmp_path))
    return tmp_path


def test_cards_round_trip_and_stay_private(data_dir):
    assert pacs_config.load() == []
    cards = [
        PacsProfile(id="a", label="A", url="http://a/dicom-web", auth="basic", username="u", password="secret"),
        PacsProfile(id="b", label="B", kind="dimse", host="10.0.0.5", port=11112, called_aet="PACS_B"),
    ]
    pacs_config.save(cards)
    assert pacs_config.load() == cards
    mode = (data_dir / "pacs.json").stat().st_mode
    assert stat.S_IMODE(mode) == 0o600


def test_new_ids_are_unique_and_never_idc():
    assert pacs_config.new_id("Hospital PACS (test)", set()) == "hospital-pacs-test"
    assert pacs_config.new_id("Hospital PACS", {"hospital-pacs"}) == "hospital-pacs-2"
    assert pacs_config.new_id("IDC", set()) == "idc-2"
    assert pacs_config.new_id("醫院", set()) == "pacs"


@pytest.mark.parametrize(
    "card, problem",
    [
        (PacsProfile(id="", label=""), "name"),
        (PacsProfile(id="", label="A", url="pacs.local"), "http"),
        (PacsProfile(id="", label="A", kind="dimse", host="", called_aet="X"), "host"),
        (PacsProfile(id="", label="A", kind="dimse", host="h", port=0, called_aet="X"), "port"),
        (PacsProfile(id="", label="A", kind="dimse", host="h", called_aet="A_TITLE_LONGER_THAN_16"), "16"),
        (PacsProfile(id="", label="A", kind="dimse", host="h", called_aet="X", retrieve="C-PUT"), "C-GET"),
    ],
)
def test_the_form_says_what_is_wrong(card, problem):
    assert problem in pacs_config.problem(card)


def test_a_complete_card_has_no_problem():
    assert pacs_config.problem(PacsProfile(id="", label="A", url="https://a/dicom-web")) == ""
    assert pacs_config.problem(PacsProfile(id="", label="B", kind="dimse", host="h", called_aet="X")) == ""
