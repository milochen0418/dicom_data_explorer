"""A PACS that speaks classic DICOM networking (DIMSE): C-FIND to search,
C-GET or C-MOVE to fetch.

C-GET brings the images back over the connection this app opens. C-MOVE has
the PACS open its own connection to this app's receiver (services/receiver.py),
so the PACS must know the receiver's AE title, this host's IP and the port.
Queries go level by level (study, series, image) with the parent's UID, the
one way every PACS supports.
"""

from __future__ import annotations

import logging
import socket

from pydicom.dataset import Dataset
from pydicom.multival import MultiValue
from pydicom.uid import (
    JPEG2000,
    DeflatedExplicitVRLittleEndian,
    ExplicitVRLittleEndian,
    HTJ2K,
    HTJ2KLossless,
    HTJ2KLosslessRPCL,
    ImplicitVRLittleEndian,
    JPEG2000Lossless,
    JPEGBaseline8Bit,
    JPEGExtended12Bit,
    JPEGLosslessSV1,
    JPEGLossless,
    JPEGLSLossless,
    JPEGLSNearLossless,
    RLELossless,
)
from pynetdicom import AE, StoragePresentationContexts, build_role, evt
from pynetdicom.sop_class import (
    CTImageStorage,
    StudyRootQueryRetrieveInformationModelFind as FIND,
    StudyRootQueryRetrieveInformationModelGet as GET,
    StudyRootQueryRetrieveInformationModelMove as MOVE,
    Verification,
)

from dicom_data_explorer.services import pacs_config, receiver
from dicom_data_explorer.services.sources.base import (
    DownloadError, RemoteFile, SearchResult, Series, Sink, Source, Study, StudyQuery,
)

logging.getLogger("pynetdicom").setLevel(logging.WARNING)

# Newer than the names pydicom 3.0.2 knows.
LABEL_MAP_SEGMENTATION = "1.2.840.10008.5.1.4.1.1.66.7"
# What C-GET offers to receive each kind of image in: the stored syntax wins,
# so the PACS never has to convert.
TRANSFER_SYNTAXES = [
    ExplicitVRLittleEndian, ImplicitVRLittleEndian, DeflatedExplicitVRLittleEndian,
    JPEGBaseline8Bit, JPEGExtended12Bit, JPEGLossless, JPEGLosslessSV1,
    JPEGLSLossless, JPEGLSNearLossless, JPEG2000Lossless, JPEG2000, RLELossless,
    HTJ2KLossless, HTJ2KLosslessRPCL, HTJ2K,
]
MAX_CONTEXTS = 128  # per connection, by the standard
OUT_OF_RESOURCES = 0xA700
NOTHING = "2.25.1"  # a valid UID that matches no study, for probing


class DimseError(DownloadError):
    """A DIMSE failure; the message is for the user."""

    def __init__(self, message: str, status: int | None = None, transient: bool = False):
        super().__init__(message)
        self.status = status
        self.transient = transient


def _text(ds: Dataset, keyword: str) -> str:
    value = ds.get(keyword)
    if value is None:
        return ""
    if isinstance(value, MultiValue):
        return "\\".join(str(v) for v in value)
    return str(value).strip()


def _list(ds: Dataset, keyword: str) -> list[str]:
    value = ds.get(keyword)
    if value is None or value == "":
        return []
    if isinstance(value, MultiValue):
        return [str(v) for v in value]
    return [v for v in str(value).split("\\") if v]


def _int(ds: Dataset, keyword: str) -> int:
    try:
        return int(_text(ds, keyword) or 0)
    except ValueError:
        return 0


class DIMSESource(Source):
    per_file = False

    def __init__(self, profile: pacs_config.PacsProfile):
        self.profile = profile
        self.id = profile.id
        self.label = profile.label

    # -- connections --------------------------------------------------------------

    def _associate(self, contexts: list[tuple[str, list | None]], **kw):
        p = self.profile
        ae = AE(ae_title=p.calling_aet.strip())
        ae.acse_timeout = 15
        ae.network_timeout = 60
        ae.dimse_timeout = 300  # a PACS may stay silent for minutes while it retrieves
        for abstract, syntaxes in contexts:
            if syntaxes:
                ae.add_requested_context(abstract, syntaxes)
            else:
                ae.add_requested_context(abstract)
        assoc = ae.associate(p.host.strip(), int(p.port), ae_title=p.called_aet.strip(), **kw)
        if assoc.is_established:
            return assoc
        if assoc.is_rejected:
            primitive = getattr(assoc.acceptor, "primitive", None)
            reason = getattr(primitive, "reason_str", "") or "rejected"
            raise DimseError(
                f"{self.label} refused the connection ({reason}). Check its AE title ({p.called_aet}) "
                f"and that it knows ours ({p.calling_aet})."
            )
        # pynetdicom reports a refused TCP connection as an abort too: tell them apart.
        try:
            socket.create_connection((p.host.strip(), int(p.port)), timeout=5).close()
        except OSError:
            raise DimseError(f"Can't reach {self.label} at {p.host}:{p.port}.", transient=True) from None
        raise DimseError(f"{self.label} dropped the connection.", transient=True)

    def _find(self, identifier: Dataset, cap: int = 0, key: str = "") -> tuple[list[Dataset], bool]:
        """The matches, and whether there were more than `cap` distinct `key` values.

        A PACS may answer once per stored image even at study level, so the cap
        counts distinct keys, not answers.
        """
        assoc = self._associate([(FIND, None)])
        rows: list[Dataset] = []
        seen: set[str] = set()
        capped = False
        try:
            for status, ident in assoc.send_c_find(identifier, FIND):
                if not status:
                    raise DimseError(f"{self.label} stopped answering.", transient=True)
                code = status.Status
                if code in (0xFF00, 0xFF01):
                    if ident is None or capped:
                        continue
                    rows.append(ident)
                    if key:
                        seen.add(_text(ident, key))
                    if cap and len(seen) > cap:
                        capped = True
                        assoc.send_c_cancel(1, query_model=FIND)
                elif code in (0x0000, 0xFE00):
                    break
                else:
                    raise DimseError(self._status(code, "C-FIND"), code)
        finally:
            assoc.release()
        return rows, capped

    def _status(self, code: int, name: str) -> str:
        if code == 0xA801:
            where = f"{pacs_config.host_ip() or 'this computer'}:{pacs_config.RECEIVER_PORT}"
            return (
                f"{self.label} could not deliver to {pacs_config.RECEIVER_AET} (0xA801): it does not know "
                f"that AE title, or cannot connect to {where}. Ask its administrator to register it, "
                "and check the firewall."
            )
        if 0xA700 <= code <= 0xA7FF:
            hint = (
                " With C-GET it can also mean the PACS cannot send the kinds of images the series holds."
                if name == "C-GET" else ""
            )
            return f"{self.label} ran out of resources (0x{code:04X}). Try again later.{hint}"
        if 0xA900 <= code <= 0xA9FF:
            return f"{self.label} did not accept the {name} request (0x{code:04X})."
        if 0xC000 <= code <= 0xCFFF:
            return f"{self.label} could not process the {name} request (0x{code:04X})."
        return f"{self.label} answered the {name} request with 0x{code:04X}."

    # -- search -----------------------------------------------------------------

    def search(self, query: StudyQuery) -> SearchResult:
        ds = Dataset()
        ds.QueryRetrieveLevel = "STUDY"
        texts = (query.patient_id, query.patient_name, query.accession, query.description)
        if any(not t.isascii() for t in texts if t):
            ds.SpecificCharacterSet = "ISO_IR 192"
        ds.StudyInstanceUID = ""
        ds.PatientID = query.patient_id
        ds.PatientName = query.patient_name
        ds.AccessionNumber = query.accession
        ds.StudyDate = query.date_range()
        ds.StudyDescription = query.description
        ds.ModalitiesInStudy = query.modality
        ds.NumberOfStudyRelatedSeries = ""
        ds.NumberOfStudyRelatedInstances = ""
        cap = self.profile.max_results
        rows, capped = self._find(ds, cap, "StudyInstanceUID")
        studies: dict[str, Study] = {}
        for r in rows:
            uid = _text(r, "StudyInstanceUID")
            if uid and uid not in studies:
                studies[uid] = Study(
                    uid=uid,
                    source=self.id,
                    patient_id=_text(r, "PatientID"),
                    patient_name=_text(r, "PatientName"),
                    date=_text(r, "StudyDate"),
                    description=_text(r, "StudyDescription"),
                    accession=_text(r, "AccessionNumber"),
                    modalities=_list(r, "ModalitiesInStudy"),
                    series_count=_int(r, "NumberOfStudyRelatedSeries"),
                    image_count=_int(r, "NumberOfStudyRelatedInstances"),
                )
        found = list(studies.values())[:cap]
        if query.modality:
            # An optional key the PACS may have ignored: check it here when it answered one.
            found = [s for s in found if not s.modalities or query.modality.upper() in s.modalities]
        return SearchResult(found, capped)

    def series_of(self, study_uid: str) -> list[Series]:
        ds = Dataset()
        ds.QueryRetrieveLevel = "SERIES"
        ds.StudyInstanceUID = study_uid
        for keyword in (
            "SeriesInstanceUID", "Modality", "SeriesNumber", "SeriesDescription",
            "SeriesDate", "BodyPartExamined", "NumberOfSeriesRelatedInstances",
        ):
            setattr(ds, keyword, "")
        rows, _ = self._find(ds)
        series: dict[str, Series] = {}
        answers: dict[str, int] = {}
        for r in rows:
            uid = _text(r, "SeriesInstanceUID")
            if not uid:
                continue
            answers[uid] = answers.get(uid, 0) + 1
            series.setdefault(
                uid,
                Series(
                    uid=uid,
                    aws_url="",
                    study_uid=study_uid,
                    modality=_text(r, "Modality"),
                    body_part=_text(r, "BodyPartExamined"),
                    description=_text(r, "SeriesDescription"),
                    date=_text(r, "SeriesDate"),
                    images=_int(r, "NumberOfSeriesRelatedInstances"),
                    source=self.id,
                ),
            )
        for uid, s in series.items():
            if not s.images and answers[uid] > 1:
                s.images = answers[uid]  # a PACS that answers once per image tells the count that way
        return list(series.values())

    def find_series(self, uids: list[str], study_uid: str = "") -> dict[str, Series]:
        if not study_uid:
            return {}  # without its study, a level-by-level PACS cannot find a series
        return {s.uid: s for s in self.series_of(study_uid) if s.uid in uids}

    # -- download ---------------------------------------------------------------

    def list_files(self, series: Series, session) -> list[RemoteFile]:
        if not series.study_uid:
            raise DimseError("This series has no study UID, so the PACS cannot list its images.")
        ds = Dataset()
        ds.QueryRetrieveLevel = "IMAGE"
        ds.StudyInstanceUID = series.study_uid
        ds.SeriesInstanceUID = series.uid
        ds.SOPInstanceUID = ""
        ds.SOPClassUID = ""
        rows, _ = self._find(ds)
        files: dict[str, RemoteFile] = {}
        for r in rows:
            sop = _text(r, "SOPInstanceUID")
            if sop and sop not in files:
                files[sop] = RemoteFile(rel=f"{sop}.dcm", size=0, ref=sop, sop_class=_text(r, "SOPClassUID"))
        return list(files.values())

    def fetch(self, series: Series, todo: list[RemoteFile], sink: Sink) -> None:
        if self.profile.retrieve == "C-MOVE":
            self._move(series, sink)
        else:
            self._get(series, todo, sink)

    @staticmethod
    def _identifier(series: Series) -> Dataset:
        ds = Dataset()
        ds.QueryRetrieveLevel = "SERIES"
        ds.StudyInstanceUID = series.study_uid
        ds.SeriesInstanceUID = series.uid
        return ds

    def _get(self, series: Series, todo: list[RemoteFile], sink: Sink) -> None:
        classes = sorted({r.sop_class for r in todo if r.sop_class})
        if not classes:  # the PACS did not say what the series holds: offer the common kinds
            classes = [cx.abstract_syntax for cx in StoragePresentationContexts] + [LABEL_MAP_SEGMENTATION]
        classes = classes[: MAX_CONTEXTS - 1]

        def on_store(event) -> int:
            if sink.stopped():
                return OUT_OF_RESOURCES
            try:
                sink.write(f"{event.request.AffectedSOPInstanceUID}.dcm", event.encoded_dataset(include_meta=True))
            except Exception:  # noqa: BLE001 - the PACS counts it as failed; Retry fetches it
                logging.exception("Could not save an image of %s", series.uid)
                return OUT_OF_RESOURCES
            return 0x0000

        assoc = self._associate(
            [(GET, None)] + [(uid, TRANSFER_SYNTAXES) for uid in classes],
            ext_neg=[build_role(uid, scp_role=True) for uid in classes],
            evt_handlers=[(evt.EVT_C_STORE, on_store)],
        )
        try:
            self._retrieve(assoc, lambda: assoc.send_c_get(self._identifier(series), GET), GET, sink, "C-GET")
        finally:
            assoc.release()

    def _move(self, series: Series, sink: Sink) -> None:
        rx = receiver.get()
        rx.expect(series.uid, sink)
        try:
            assoc = self._associate([(MOVE, None)])
            try:
                self._retrieve(
                    assoc, lambda: assoc.send_c_move(self._identifier(series), rx.aet, MOVE), MOVE, sink, "C-MOVE"
                )
            finally:
                assoc.release()
        finally:
            rx.done(series.uid)

    def _retrieve(self, assoc, send, model, sink: Sink, name: str) -> None:
        cancelled = answered = False
        try:
            for status, _ in send():  # send() itself raises ValueError when the context was refused
                if not status and not answered:
                    # Dropped before any answer: the usual way a PACS refuses a command
                    # it does not allow for our AE title (Orthanc's AllowGet, for one).
                    other = "C-MOVE" if name == "C-GET" else "C-GET"
                    raise DimseError(
                        f"{self.label} refused the {name} request. It may not allow {name} for our "
                        f"AE title ({self.profile.calling_aet}): choose {other} on its card."
                    )
                if not status:
                    raise DimseError(f"{self.label} stopped answering during {name}.", transient=True)
                answered = True
                code = status.Status
                if code in (0xFF00, 0xFF01):
                    if sink.stopped() and not cancelled:
                        assoc.send_c_cancel(1, query_model=model)
                        cancelled = True
                    continue
                if code in (0x0000, 0xB000, 0xFE00):
                    return  # what did not arrive shows up as missing files
                raise DimseError(self._status(code, name), code, transient=0xA700 <= code <= 0xA7FF)
        except ValueError as exc:  # the PACS did not accept the request's presentation context
            raise DimseError(
                f"{self.label} does not accept {name} from this app. "
                + ("Choose C-MOVE on its card." if name == "C-GET" else "Choose C-GET on its card.")
            ) from exc

    def origin(self, series: Series) -> str:
        return f"dimse:{self.id}"

    # -- errors and the connection test ---------------------------------------------

    def is_transient(self, exc: BaseException) -> bool:
        return isinstance(exc, DimseError) and exc.transient

    def describe_error(self, exc: BaseException) -> str | None:
        return str(exc) if isinstance(exc, DimseError) else None

    def test(self) -> list[tuple[str, str]]:
        p = self.profile
        try:
            assoc = self._associate([(Verification, None)])
            status = assoc.send_c_echo()
            assoc.release()
        except DimseError as exc:
            return [("fail", str(exc))]
        if not status or status.Status != 0x0000:
            return [("fail", f"{p.called_aet} accepted the connection but not C-ECHO.")]
        report = [("ok", f"C-ECHO works: {p.called_aet} answers and accepts our AE title {p.calling_aet}.")]

        probe = Dataset()
        probe.QueryRetrieveLevel = "STUDY"
        probe.PatientID = "DDE_TEST_NONE"
        probe.StudyInstanceUID = ""
        try:
            self._find(probe)
            report.append(("ok", "Search (C-FIND) works."))
        except DimseError as exc:
            report.append(("fail", f"Search (C-FIND) failed: {exc}"))

        nothing = Dataset()
        nothing.QueryRetrieveLevel = "STUDY"
        nothing.StudyInstanceUID = NOTHING
        # C-GET of a study nobody has: any answer means the command is allowed;
        # a PACS that does not allow it rejects the context or drops the request.
        try:
            assoc = self._associate(
                [(GET, None), (CTImageStorage, None)], ext_neg=[build_role(CTImageStorage, scp_role=True)]
            )
            try:
                answers = [status for status, _ in assoc.send_c_get(nothing, GET)]
            finally:
                assoc.release()
            get_ok = bool(answers) and all(answers)
        except (DimseError, ValueError):
            get_ok = False
        report.append(("ok", "C-GET is allowed.") if get_ok else ("fail", "C-GET is not allowed: choose C-MOVE."))

        where = f"{pacs_config.host_ip() or 'this computer'}:{pacs_config.RECEIVER_PORT}"
        rx_aet = pacs_config.RECEIVER_AET
        try:
            assoc = self._associate([(MOVE, None)])
            codes = []
            try:
                for status, _ in assoc.send_c_move(nothing, rx_aet, MOVE):
                    if status:
                        codes.append(status.Status)
            finally:
                assoc.release()
            final = codes[-1] if codes else None
            if final == 0xA801:
                report.append(("fail", f"C-MOVE: {p.called_aet} does not know {rx_aet}. Ask its administrator to register {rx_aet} at {where}."))
            elif final in (0x0000, 0xB000):
                report.append(("ok", f"C-MOVE: {p.called_aet} knows {rx_aet}; it delivers to {where}."))
            else:
                # Some PACS (Orthanc) refuse to move a study they do not have before
                # looking at the destination, so the probe cannot tell.
                answer = f"0x{final:04X}" if final is not None else "no answer"
                report.append(("note", f"C-MOVE: inconclusive ({answer}). Make sure {p.called_aet} knows {rx_aet} at {where}; the first download will tell."))
        except ValueError:
            report.append(("fail", "C-MOVE is not supported."))
        except DimseError as exc:
            report.append(("fail", f"C-MOVE probe failed: {exc}"))
        return report
