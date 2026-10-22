"""The one Download control: it shows what is happening to that series."""

import reflex as rx

from dicom_data_explorer.states.download_state import DownloadState

_BASE = "inline-flex items-center justify-center gap-1.5 rounded-lg text-sm font-medium transition-colors whitespace-nowrap"


def _cancel_icon(uid) -> rx.Component:
    return rx.el.button(
        rx.icon("x", size=14),
        on_click=DownloadState.cancel(uid).stop_propagation,
        title="Cancel this download",
        class_name="p-1 rounded-md text-gray-400 hover:text-red-600 hover:bg-red-50",
    )


def download_button(uid, wide: bool = False, on_download=None) -> rx.Component:
    """``uid`` is a series UID var; ``wide`` fills the width (details panel).

    ``on_download`` replaces the IDC download (the PACS search queues its own series).
    """
    width = "w-full py-2" if wide else "w-36 py-1.5"
    status = DownloadState.status_of[uid]
    return rx.match(
        status,
        (
            "done",
            rx.el.a(
                rx.icon("circle-check", size=16),
                "Downloaded",
                href="/downloads",
                on_click=rx.stop_propagation,
                title="Saved. See it under Downloads.",
                class_name=f"{_BASE} {width} text-green-700 bg-green-50 hover:bg-green-100",
            ),
        ),
        (
            "queued",
            rx.el.div(
                rx.icon("clock", size=15, class_name="text-gray-500"),
                rx.el.span("Waiting", class_name="text-gray-600"),
                _cancel_icon(uid),
                class_name=f"{_BASE} {width} bg-gray-100",
            ),
        ),
        (
            "running",
            rx.el.div(
                rx.el.div(
                    rx.el.div(
                        class_name="h-full bg-blue-600 rounded-full transition-all duration-500",
                        style={"width": f"{DownloadState.percent_of[uid]}%"},
                    ),
                    class_name="flex-1 h-2 bg-blue-100 rounded-full overflow-hidden",
                ),
                rx.el.span(
                    f"{DownloadState.percent_of[uid]}%",
                    class_name="text-xs font-semibold text-blue-700 w-9 text-right",
                ),
                _cancel_icon(uid),
                class_name=f"{_BASE} {width} px-2 bg-blue-50",
            ),
        ),
        (
            "canceling",
            rx.el.div("Canceling…", class_name=f"{_BASE} {width} text-gray-500 bg-gray-100"),
        ),
        (
            "failed",
            rx.el.button(
                rx.icon("rotate-ccw", size=15),
                "Retry",
                on_click=DownloadState.retry(uid).stop_propagation,
                title="The download failed. Retry keeps the files already fetched.",
                class_name=f"{_BASE} {width} text-red-700 bg-red-50 hover:bg-red-100 border border-red-200",
            ),
        ),
        (
            "interrupted",
            rx.el.button(
                rx.icon("play", size=15),
                "Resume",
                on_click=DownloadState.retry(uid).stop_propagation,
                title="Stopped by an app restart. Resume fetches the missing files.",
                class_name=f"{_BASE} {width} text-amber-800 bg-amber-50 hover:bg-amber-100 border border-amber-200",
            ),
        ),
        rx.el.button(
            rx.icon("download", size=15),
            "Download",
            on_click=(on_download if on_download is not None else DownloadState.download(uid, False)).stop_propagation,
            class_name=f"{_BASE} {width} text-white bg-blue-600 hover:bg-blue-700 shadow-sm",
        ),
    )
