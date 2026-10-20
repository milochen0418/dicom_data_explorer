"""The dialog DICOM Viewer opens to fetch the images an annotation needs (see FetchState)."""

import reflex as rx

from dicom_data_explorer.states.fetch_state import FetchState, Wanted

_BUTTON = "inline-flex items-center justify-center gap-1.5 rounded-lg text-sm font-medium transition-colors whitespace-nowrap px-4 py-2"


def _status_cell(w: Wanted) -> rx.Component:
    return rx.match(
        w.status,
        (
            "done",
            rx.el.span(
                rx.icon("circle-check", size=16),
                "Downloaded",
                class_name="inline-flex items-center gap-1.5 text-sm font-medium text-green-700",
            ),
        ),
        (
            "running",
            rx.el.div(
                rx.el.div(
                    rx.el.div(
                        class_name="h-full bg-blue-600 rounded-full transition-all duration-500",
                        style={"width": f"{w.percent}%"},
                    ),
                    class_name="w-28 h-2 bg-blue-100 rounded-full overflow-hidden",
                ),
                rx.el.span(f"{w.percent}%", class_name="text-xs font-semibold text-blue-700 w-9 text-right"),
                class_name="inline-flex items-center gap-2",
            ),
        ),
        (
            "queued",
            rx.el.span(
                rx.icon("clock", size=15),
                "Waiting",
                class_name="inline-flex items-center gap-1.5 text-sm text-gray-600",
            ),
        ),
        ("canceling", rx.el.span("Canceling…", class_name="text-sm text-gray-500")),
        (
            "failed",
            rx.el.button(
                rx.icon("rotate-ccw", size=15),
                "Retry",
                on_click=FetchState.retry(w.uid),
                class_name="inline-flex items-center gap-1.5 text-sm font-medium text-red-700 bg-red-50 hover:bg-red-100 border border-red-200 rounded-lg px-3 py-1",
            ),
        ),
        (
            "interrupted",
            rx.el.button(
                rx.icon("play", size=15),
                "Resume",
                on_click=FetchState.retry(w.uid),
                class_name="inline-flex items-center gap-1.5 text-sm font-medium text-amber-800 bg-amber-50 hover:bg-amber-100 border border-amber-200 rounded-lg px-3 py-1",
            ),
        ),
        rx.cond(
            w.in_index,
            rx.el.span("Not downloaded", class_name="text-sm text-gray-500"),
            rx.el.span("Cannot be fetched", class_name="text-sm text-red-600"),
        ),
    )


def _wanted_row(w: Wanted) -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.p(w.title, title=w.uid, class_name="text-sm font-medium text-gray-900 truncate"),
            rx.el.p(w.subtitle, class_name="text-xs text-gray-500 truncate"),
            rx.cond(w.note != "", rx.el.p(w.note, class_name="text-xs text-amber-700 mt-0.5")),
            class_name="flex-1 min-w-0",
        ),
        rx.el.div(_status_cell(w), class_name="shrink-0"),
        class_name="flex items-center gap-4 py-3 border-b border-gray-100 last:border-b-0",
    )


def fetch_series_page() -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.div(
                rx.icon("cloud-download", size=20, class_name="text-blue-600"),
                class_name="p-2 rounded-lg bg-blue-50 shrink-0",
            ),
            rx.el.div(
                rx.el.h2("Images for this annotation", class_name="text-base font-semibold text-gray-900"),
                rx.el.p(
                    rx.cond(FetchState.title != "", FetchState.title, "The annotation refers to these series"),
                    class_name="text-sm text-gray-500 truncate",
                ),
                class_name="flex-1 min-w-0",
            ),
            class_name="flex items-center gap-3 pr-10",
        ),
        rx.cond(
            FetchState.error != "",
            rx.el.p(FetchState.error, class_name="text-sm text-red-600 mt-4"),
        ),
        rx.cond(
            ~FetchState.loaded,
            rx.el.p(
                rx.icon("loader-circle", size=16, class_name="animate-spin"),
                "Looking up the series in the IDC index…",
                class_name="flex items-center gap-2 text-sm text-gray-500 mt-4",
            ),
            rx.el.div(rx.foreach(FetchState.wanted, _wanted_row), class_name="mt-3"),
        ),
        rx.cond(
            FetchState.unknown_count > 0,
            rx.el.p(
                "Series that are not in the IDC index cannot be fetched from here.",
                class_name="text-xs text-gray-500 mt-2",
            ),
        ),
        rx.el.div(
            rx.el.button(
                "Cancel",
                on_click=FetchState.cancel,
                class_name=f"{_BUTTON} border border-gray-300 text-gray-700 hover:bg-gray-50",
            ),
            rx.cond(
                FetchState.all_done,
                rx.el.button(
                    rx.icon("eye", size=16),
                    "Open in DICOM Viewer",
                    on_click=FetchState.finish_now,
                    class_name=f"{_BUTTON} text-white bg-green-600 hover:bg-green-700 shadow-sm",
                ),
                rx.el.button(
                    rx.cond(
                        FetchState.active,
                        rx.icon("loader-circle", size=16, class_name="animate-spin"),
                        rx.icon("download", size=16),
                    ),
                    rx.cond(FetchState.active, "Downloading…", FetchState.download_label),
                    on_click=FetchState.download_all,
                    disabled=FetchState.active | ~FetchState.loaded | (FetchState.fetchable.length() == 0),
                    class_name=f"{_BUTTON} text-white bg-blue-600 hover:bg-blue-700 shadow-sm disabled:opacity-50",
                ),
            ),
            class_name="flex justify-end gap-3 mt-5",
        ),
        rx.el.p(
            "Downloads go to the shared DICOM folder and also appear under Downloads. "
            "When every series is in, this dialog closes and the viewer opens them.",
            class_name="text-xs text-gray-400 mt-4",
        ),
        class_name="w-[560px] max-w-[92vw] p-5 bg-white font-['Inter'] text-gray-800",
    )
