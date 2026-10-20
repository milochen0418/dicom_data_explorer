"""The references an annotation object carries, read from synthetic files (pydicom only)."""

from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from dicom_data_explorer.services.references import referenced_series_uids, series_referenced_by

CT_SERIES = "1.2.3.100"


def dataset(sop_class: str) -> Dataset:
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = sop_class
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.SeriesInstanceUID = generate_uid()
    return ds


def seg_like() -> Dataset:  # SEG and presentation states: ReferencedSeriesSequence
    ds = dataset("1.2.840.10008.5.1.4.1.1.66.4")
    item = Dataset()
    item.SeriesInstanceUID = CT_SERIES
    ds.ReferencedSeriesSequence = Sequence([item])
    return ds


def rtstruct_like() -> Dataset:
    ds = dataset("1.2.840.10008.5.1.4.1.1.481.3")
    series = Dataset()
    series.SeriesInstanceUID = CT_SERIES
    study = Dataset()
    study.RTReferencedSeriesSequence = Sequence([series])
    frame = Dataset()
    frame.RTReferencedStudySequence = Sequence([study])
    ds.ReferencedFrameOfReferenceSequence = Sequence([frame])
    return ds


def sr_like() -> Dataset:
    ds = dataset("1.2.840.10008.5.1.4.1.1.88.33")
    series = Dataset()
    series.SeriesInstanceUID = CT_SERIES
    study = Dataset()
    study.ReferencedSeriesSequence = Sequence([series])
    ds.CurrentRequestedProcedureEvidenceSequence = Sequence([study])
    return ds


def test_each_kind_of_object_names_its_series():
    for build in (seg_like, rtstruct_like, sr_like):
        assert referenced_series_uids(build()) == {CT_SERIES}, build.__name__
    assert referenced_series_uids(dataset("1.2.840.10008.5.1.4.1.1.2")) == set()


def test_a_folder_of_annotations_names_the_series_except_its_own(tmp_path):
    seg = seg_like()
    seg.save_as(tmp_path / "seg.dcm", enforce_file_format=True)
    rt = rtstruct_like()
    rt.ReferencedFrameOfReferenceSequence[0].RTReferencedStudySequence[0].RTReferencedSeriesSequence[0].SeriesInstanceUID = "9.9.9"
    rt.save_as(tmp_path / "rt.dcm", enforce_file_format=True)
    (tmp_path / "notes.txt").write_text("not dicom")
    (tmp_path / ".hidden.dcm").write_text("skipped")
    assert series_referenced_by(tmp_path) == {CT_SERIES, "9.9.9"}
    assert series_referenced_by(tmp_path, own_uid="9.9.9") == {CT_SERIES}
    assert series_referenced_by(tmp_path / "missing") == set()


class FakeSession:
    """Just enough of requests.Session for list_objects and a file fetch."""

    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.gets: list[str] = []

    def get(self, url, params=None, timeout=None):
        self.gets.append(url)
        if params and params.get("list-type") == "2":
            prefix = params["prefix"]
            contents = "".join(
                f"<Contents><Key>{prefix}{name}</Key><Size>{len(data)}</Size></Contents>"
                for name, data in self.files.items()
            )
            body = f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">{contents}</ListBucketResult>'
            return _Response(body.encode())
        name = url.rsplit("/", 1)[1]
        return _Response(self.files[name])


class _Response:
    status_code = 200

    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


def _bytes(ds) -> bytes:
    import io

    buffer = io.BytesIO()
    ds.save_as(buffer, enforce_file_format=True)
    return buffer.getvalue()


def test_peek_reads_the_references_from_idc_before_downloading():
    from dicom_data_explorer.services.references import ReferencesUnavailable, peek_series

    seg = seg_like()
    session = FakeSession({"seg.dcm": _bytes(seg), "notes.txt": b"not dicom"})
    assert peek_series("s3://idc-open-data/abc/*", seg.SeriesInstanceUID, session) == {CT_SERIES}
    assert len(session.gets) == 3  # the listing and both files
    try:
        peek_series("s3://idc-open-data/abc/*", "", FakeSession({"notes.txt": b"nope"}))
    except ReferencesUnavailable as error:
        assert "could be read" in str(error)
    else:
        raise AssertionError("unreadable files should raise")
