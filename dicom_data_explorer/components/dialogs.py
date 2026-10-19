"""Confirmations: a big download, and deleting a downloaded series."""

import reflex as rx

from dicom_data_explorer.states.download_state import DownloadState


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
