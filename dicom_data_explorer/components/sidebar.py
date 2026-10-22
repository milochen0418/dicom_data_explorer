import reflex as rx
from dicom_data_explorer.states.ui_state import UIState
from dicom_data_explorer.states.download_state import DownloadState


def sidebar_item(text: str, icon: str, href: str, active, badge: rx.Component | None = None) -> rx.Component:
    return rx.el.a(
        rx.icon(icon, size=20),
        rx.el.span(text, class_name="font-medium flex-1"),
        badge,
        href=href,
        class_name=rx.cond(
            active,
            "flex items-center gap-3 px-4 py-2.5 rounded-xl bg-blue-50 text-blue-700 mb-1",
            "flex items-center gap-3 px-4 py-2.5 rounded-xl text-gray-700 hover:bg-gray-100 mb-1",
        ),
    )


def downloads_badge() -> rx.Component:
    return rx.fragment(
        rx.cond(
            DownloadState.active_count > 0,
            rx.el.span(
                DownloadState.active_count,
                title="Downloading or waiting",
                class_name="bg-blue-600 text-white text-xs font-bold min-w-5 h-5 px-1.5 rounded-full flex items-center justify-center",
            ),
        ),
        rx.cond(
            DownloadState.problem_count > 0,
            rx.el.span(
                DownloadState.problem_count,
                title="Failed or interrupted: open Downloads to retry",
                class_name="bg-red-500 text-white text-xs font-bold min-w-5 h-5 px-1.5 rounded-full flex items-center justify-center ml-1",
            ),
        ),
    )


def current_download() -> rx.Component:
    """What is downloading right now, visible from every page."""
    job = DownloadState.current
    return rx.cond(
        job.uid != "",
        rx.el.a(
            rx.el.div(
                rx.icon("loader-circle", size=14, class_name="animate-spin text-blue-600"),
                rx.el.span("Downloading", class_name="text-xs font-semibold text-blue-700"),
                rx.el.span(
                    rx.cond(DownloadState.active_count > 1, f"+{DownloadState.active_count - 1} waiting", ""),
                    class_name="text-xs text-gray-500 ml-auto",
                ),
                class_name="flex items-center gap-1.5 mb-1",
            ),
            rx.el.p(job.title, class_name="text-sm text-gray-800 truncate"),
            rx.el.div(
                rx.el.div(
                    class_name="h-full bg-blue-600 rounded-full transition-all duration-500",
                    style={"width": f"{job.percent}%"},
                ),
                class_name="w-full h-1.5 bg-blue-100 rounded-full overflow-hidden mt-2",
            ),
            rx.el.p(f"{job.percent}%", class_name="text-xs text-gray-500 mt-1"),
            href="/downloads",
            class_name="block mx-4 mb-4 p-3 rounded-xl border border-blue-100 bg-white hover:border-blue-300",
        ),
    )


def sidebar() -> rx.Component:
    path = rx.State.router.page.path
    return rx.el.aside(
        rx.el.div(
            rx.icon("activity", class_name="text-blue-600", size=26),
            rx.el.span("DICOM Explorer", class_name="text-lg font-bold text-gray-900"),
            class_name="h-16 flex items-center gap-3 px-6 border-b border-gray-100",
        ),
        rx.el.nav(
            sidebar_item("Find series", "search", "/", (path != "/downloads") & (path != "/pacs")),
            sidebar_item("Downloads", "download", "/downloads", path == "/downloads", downloads_badge()),
            sidebar_item("PACS", "server", "/pacs", path == "/pacs"),
            class_name="px-3 py-5",
        ),
        rx.el.div(class_name="flex-1"),
        current_download(),
        rx.el.div(
            rx.el.button(
                rx.icon("graduation-cap", size=20),
                rx.el.span("DICOM basics", class_name="font-medium"),
                on_click=UIState.open_onboarding,
                class_name="w-full flex items-center gap-3 px-4 py-2.5 rounded-xl text-gray-700 hover:bg-gray-100",
            ),
            class_name="px-3 py-3 border-t border-gray-100",
        ),
        class_name="w-60 h-full bg-white border-r border-gray-200 flex flex-col shrink-0",
    )
