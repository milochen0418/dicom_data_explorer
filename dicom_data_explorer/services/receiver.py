"""This app's DICOM receiver (a Storage SCP): where a PACS delivers a C-MOVE.

C-MOVE makes the PACS open its own connection to an AE title it already
knows, so the receiver listens on a fixed port (pacs_config.RECEIVER_PORT)
under a fixed AE title, which the PACS administrator registers together with
this host's IP. One receiver per process, started on first use. It accepts
images only for series a download is waiting for, and hands each one to it.
"""

from __future__ import annotations

import logging
import threading

from pynetdicom import AE, _config, evt
from pynetdicom.sop_class import Verification

from dicom_data_explorer.services import pacs_config
from dicom_data_explorer.services.sources.base import DownloadError, Sink

# Accept every storage SOP class, new and private ones included: what a PACS
# sends is decided by what the series holds, not by us.
_config.UNRESTRICTED_STORAGE_SERVICE = True

REFUSED = 0x0124  # "not authorized": nobody here is waiting for that series
OUT_OF_RESOURCES = 0xA700


class Receiver:
    def __init__(self, aet: str, port: int, bind: str):
        self.aet, self.port, self.bind = aet, port, bind
        self._lock = threading.Lock()
        self._waiting: dict[str, Sink] = {}  # SeriesInstanceUID -> the download
        self._server = None

    def start(self) -> None:
        with self._lock:
            if self._server is not None:
                return
            ae = AE(ae_title=self.aet)
            ae.require_called_aet = True  # a PACS must address us by our AE title
            ae.add_supported_context(Verification)  # so the PACS administrator can C-ECHO us
            try:
                self._server = ae.start_server(
                    (self.bind, self.port), block=False, evt_handlers=[(evt.EVT_C_STORE, self._on_store)]
                )
            except OSError as exc:
                raise DownloadError(
                    f"The receiver cannot listen on port {self.port} ({exc.strerror or exc}). "
                    "Is another program using it?"
                ) from exc

    def stop(self) -> None:
        with self._lock:
            if self._server is not None:
                self._server.shutdown()
                self._server = None

    def expect(self, series_uid: str, sink: Sink) -> None:
        with self._lock:
            self._waiting[series_uid] = sink

    def done(self, series_uid: str) -> None:
        with self._lock:
            self._waiting.pop(series_uid, None)

    def _on_store(self, event) -> int:
        """One image of a C-MOVE (each incoming connection runs in its own thread).

        Images are routed by their own SeriesInstanceUID: the Move Originator
        fields that would name the request are optional in the standard.
        """
        try:
            series_uid = str(event.dataset.SeriesInstanceUID)
        except Exception:  # noqa: BLE001 - not a dataset we can route
            return REFUSED
        with self._lock:
            sink = self._waiting.get(series_uid)
        if sink is None or sink.stopped():
            return REFUSED
        try:
            sink.write(f"{event.request.AffectedSOPInstanceUID}.dcm", event.encoded_dataset(include_meta=True))
        except Exception:  # noqa: BLE001 - the PACS counts it as failed; Retry fetches it
            logging.exception("Could not save an image received for %s", series_uid)
            return OUT_OF_RESOURCES
        return 0x0000


_receiver: Receiver | None = None
_receiver_lock = threading.Lock()


def get() -> Receiver:
    """The process's receiver, listening."""
    global _receiver
    with _receiver_lock:
        if _receiver is None:
            _receiver = Receiver(pacs_config.RECEIVER_AET, pacs_config.RECEIVER_PORT, pacs_config.RECEIVER_BIND)
        receiver = _receiver
    receiver.start()
    return receiver
