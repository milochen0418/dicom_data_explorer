"""Confirmations: a big download, deleting a downloaded series, fetching an annotation's images."""

import reflex as rx

from dicom_data_explorer.states.download_state import DownloadState
from dicom_data_explorer.states.fetch_state import Wanted


def _dialog(open_, title: str, text, confirm_label: str, on_confirm, on_cancel, danger: bool) -> rx.Component:
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title(title),
            rx.alert_dialog.description(text, class_name="text-gray-600"),
            rx.el.div(
                rx.alert_dialog.cancel(
                    rx.el.button(
                        "Cancel",
                        on_click=on_cancel,
                        class_name="px-4 py-2 rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-50 font-medium",
                    ),
                ),
                rx.alert_dialog.action(
                    rx.el.button(
                        confirm_label,
                        on_click=on_confirm,
                        class_name=(
                            "px-4 py-2 rounded-lg text-white font-medium "
                            + ("bg-red-600 hover:bg-red-700" if danger else "bg-blue-600 hover:bg-blue-700")
                        ),
                    ),
                ),
                class_name="flex justify-end gap-3 mt-5",
            ),
            max_width="460px",
        ),
        open=open_,
    )


def big_download_dialog() -> rx.Component:
    return _dialog(
        DownloadState.big_uid != "",
        "This is a big series",
        DownloadState.big_text,
        "Download anyway",
        DownloadState.confirm_big,
        DownloadState.cancel_big,
        danger=False,
    )


def _companion_row(w: Wanted) -> rx.Component:
    status = rx.match(
        w.status,
        ("done", rx.el.span("Downloaded", class_name="text-sm font-medium text-green-700")),
        ("queued", rx.el.span("Waiting", class_name="text-sm text-gray-600")),
        ("running", rx.el.span(f"Downloading {w.percent}%", class_name="text-sm text-blue-700")),
        rx.cond(
            w.in_index,
            rx.el.span("Not downloaded", class_name="text-sm text-gray-500"),
            rx.el.span("Not in the IDC index", class_name="text-sm text-red-600"),
        ),
    )
    return rx.el.div(
        rx.el.div(
            rx.el.p(w.title, title=w.uid, class_name="text-sm font-medium text-gray-900 truncate"),
            rx.el.p(w.subtitle, class_name="text-xs text-gray-500 truncate"),
            class_name="flex-1 min-w-0",
        ),
        rx.el.div(status, class_name="shrink-0"),
        class_name="flex items-center gap-4 py-2.5",
    )


def _bundle_row(w: Wanted) -> rx.Component:
    status = rx.match(
        w.status,
        ("done", rx.el.span("Downloaded", class_name="text-sm font-medium text-green-700")),
        ("queued", rx.el.span("Waiting", class_name="text-sm text-gray-600")),
        ("running", rx.el.span(f"Downloading {w.percent}%", class_name="text-sm text-blue-700")),
        rx.fragment(),
    )
    return rx.el.label(
        rx.el.input(
            type="checkbox",
            checked=DownloadState.bundle_selected[w.uid],
            disabled=(w.status == "done") | (w.status == "queued") | (w.status == "running") | (w.status == "canceling"),
            on_change=lambda checked: DownloadState.toggle_bundle(w.uid, checked),
            class_name="mt-1 accent-blue-600 shrink-0",
        ),
        rx.el.div(
            rx.el.p(w.title, title=w.uid, class_name="text-sm font-medium text-gray-900 truncate"),
            rx.el.p(w.subtitle, class_name="text-xs text-gray-500 truncate"),
            class_name="flex-1 min-w-0",
        ),
        rx.el.div(status, class_name="shrink-0"),
        class_name="flex items-start gap-3 py-2.5 cursor-pointer",
    )


def bundle_dialog() -> rx.Component:
    """Download an annotation together with the images it belongs to."""
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Download this annotation with its images?"),
            rx.alert_dialog.description(
                rx.el.span(
                    "“", DownloadState.bundle_title, "” (", DownloadState.bundle_subtitle, ") is an "
                    "annotation of other images. With them, DICOM Viewer can show it in place.",
                ),
                class_name="text-gray-600",
            ),
            rx.cond(
                DownloadState.bundle_loading,
                rx.el.p(
                    rx.icon("loader-circle", size=16, class_name="animate-spin"),
                    "Reading the annotation to see which images it belongs to…",
                    class_name="flex items-center gap-2 text-sm text-gray-500 mt-4",
                ),
                rx.el.div(
                    rx.cond(
                        DownloadState.bundle_note != "",
                        rx.el.p(DownloadState.bundle_note, class_name="text-xs text-amber-700 mt-3"),
                    ),
                    rx.cond(
                        DownloadState.bundle_rows.length() > 0,
                        rx.el.div(
                            rx.foreach(DownloadState.bundle_rows, _bundle_row),
                            class_name="mt-3 divide-y divide-gray-100 border-y border-gray-100",
                        ),
                    ),
                ),
            ),
            rx.el.div(
                rx.alert_dialog.cancel(
                    rx.el.button(
                        "Cancel",
                        on_click=DownloadState.cancel_bundle,
                        class_name="px-4 py-2 rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-50 font-medium",
                    ),
                ),
                rx.alert_dialog.action(
                    rx.el.button(
                        "Only the annotation",
                        on_click=DownloadState.confirm_bundle(True),
                        disabled=DownloadState.bundle_loading,
                        class_name="px-4 py-2 rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-50 font-medium disabled:opacity-50",
                    ),
                ),
                rx.alert_dialog.action(
                    rx.el.button(
                        rx.icon("download", size=16),
                        DownloadState.bundle_label,
                        on_click=DownloadState.confirm_bundle(False),
                        disabled=DownloadState.bundle_loading,
                        class_name="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-white font-medium bg-blue-600 hover:bg-blue-700 disabled:opacity-50",
                    ),
                ),
                class_name="flex justify-end gap-3 mt-5",
            ),
            max_width="580px",
        ),
        open=DownloadState.bundle_uid != "",
    )


def companion_dialog() -> rx.Component:
    """After an annotation is downloaded: the series it belongs to, offered for download."""
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Download the images this annotation belongs to?"),
            rx.alert_dialog.description(
                rx.el.span(
                    "“", DownloadState.companion_title, "” refers to the series below, which are "
                    "not downloaded. With them, DICOM Viewer can show the annotation on its images.",
                ),
                class_name="text-gray-600",
            ),
            rx.el.div(
                rx.foreach(DownloadState.companions, _companion_row),
                class_name="mt-4 divide-y divide-gray-100 border-y border-gray-100",
            ),
            rx.el.div(
                rx.alert_dialog.cancel(
                    rx.el.button(
                        "Not now",
                        on_click=DownloadState.dismiss_companions,
                        class_name="px-4 py-2 rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-50 font-medium",
                    ),
                ),
                rx.alert_dialog.action(
                    rx.el.button(
                        rx.icon("download", size=16),
                        DownloadState.companions_label,
                        on_click=DownloadState.download_companions,
                        disabled=~DownloadState.companions_pending,
                        class_name="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-white font-medium bg-blue-600 hover:bg-blue-700 disabled:opacity-50",
                    ),
                ),
                class_name="flex justify-end gap-3 mt-5",
            ),
            max_width="560px",
        ),
        open=DownloadState.companion_uid != "",
    )


def delete_dialog() -> rx.Component:
    return _dialog(
        DownloadState.delete_uid != "",
        "Delete this series?",
        rx.el.span(
            "“", DownloadState.delete_title, "” will be removed from the shared DICOM folder, "
            "so DICOM Viewer can no longer open it. You can download it again later.",
        ),
        "Delete",
        DownloadState.confirm_delete,
        DownloadState.cancel_delete,
        danger=True,
    )
