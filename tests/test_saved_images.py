"""The Downloads list says whether a downloaded annotation has its images."""

import io

from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import ExplicitVRLittleEndian, generate_uid
from reflex_ddns_auth.collections import Collection

from dicom_data_explorer.services.downloader import DownloadManager
from dicom_data_explorer.states import download_state

CT_SERIES = "1.2.3.100"
RT_SERIES = "1.2.3.200"


def rtstruct_bytes() -> bytes:
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.481.3"
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.SOPClassUID = ds.file_meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.SeriesInstanceUID = RT_SERIES
    series = Dataset()
    series.SeriesInstanceUID = CT_SERIES
    study = Dataset()
    study.RTReferencedSeriesSequence = Sequence([series])
    frame = Dataset()
    frame.RTReferencedStudySequence = Sequence([study])
    ds.ReferencedFrameOfReferenceSequence = Sequence([frame])
    buffer = io.BytesIO()
    ds.save_as(buffer, enforce_file_format=True)
    return buffer.getvalue()


def publish(collection: Collection, uid: str, modality: str, name: str, data: bytes) -> None:
    with collection.publish(
        uid, kind="dicom.series", title=f"test · {modality}",
        meta={"Modality": modality, "SeriesDescription": name, "Collection": "test"},
    ) as item:
        item.file(f"{name}.dcm").write_bytes(data)


def test_downloads_list_knows_whether_an_annotation_has_its_images(tmp_path):
    collection = Collection("dicom", app_id="test-app", root=tmp_path)
    manager = DownloadManager(collection_factory=lambda: collection)
    publish(collection, RT_SERIES, "RTSTRUCT", "rt", rtstruct_bytes())
    download_state._references_cache.clear()

    (saved,) = download_state._load_saved(manager)
    assert saved.annotation and saved.images == "missing"

    publish(collection, CT_SERIES, "CT", "ct", b"not even dicom")
    rows = {s.uid: s for s in download_state._load_saved(manager)}
    assert rows[RT_SERIES].images == "present"
    assert rows[CT_SERIES].annotation is False and rows[CT_SERIES].images == ""


def test_the_live_state_of_an_annotations_images_follows_the_queue():
    from dicom_data_explorer.states.download_state import SavedSeries, _images_of

    rt = SavedSeries(uid="rt", annotation=True, images="missing", needs=["ct1", "ct2"])
    ct = SavedSeries(uid="ct1", annotation=False)
    blind = SavedSeries(uid="sr", annotation=True, images="unknown")
    rows = [rt, ct, blind]

    state, pct, text = _images_of(rows, {"ct1": "done"}, {})
    assert (state["rt"], text["rt"]) == ("missing", "") and state["sr"] == "unknown" and "ct1" not in state
    state, pct, text = _images_of(rows, {"ct1": "done", "ct2": "queued"}, {})
    assert (state["rt"], text["rt"]) == ("queued", "Images waiting in the queue")
    state, pct, text = _images_of(rows, {"ct1": "done", "ct2": "running"}, {"ct2": 42})
    assert (state["rt"], pct["rt"], text["rt"]) == ("running", 42, "Downloading images · 42%")
    state, pct, text = _images_of(rows, {"ct1": "done", "ct2": "failed"}, {})
    assert state["rt"] == "failed"
    state, pct, text = _images_of(rows, {"ct1": "done", "ct2": "done"}, {})
    assert (state["rt"], pct["rt"], text["rt"]) == ("present", 100, "with its images")
