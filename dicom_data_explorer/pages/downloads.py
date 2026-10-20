import reflex as rx
from reflex_base.vars.function import FunctionStringVar

from dicom_data_explorer.states.download_state import DownloadState, JobCard, SavedSeries
from dicom_data_explorer.components.layout import layout

# Format an ISO timestamp in the browser's own time zone.
_local_time = FunctionStringVar.create(
    "((iso) => iso ? new Date(iso).toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'}) : '')"
)

_SMALL_BUTTON = "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium"


def job_actions(job: JobCard) -> rx.Component:
    cancel = rx.el.button(
        rx.icon("x", size=15),
        "Cancel",
        on_click=DownloadState.cancel(job.uid),
        class_name=f"{_SMALL_BUTTON} text-gray-700 border border-gray-300 hover:bg-gray-50",
    )
    remove = rx.el.button(
        rx.icon("trash-2", size=15),
        "Remove",
        on_click=DownloadState.cancel(job.uid),
        title="Forget this download and delete the files fetched so far",
        class_name=f"{_SMALL_BUTTON} text-gray-700 border border-gray-300 hover:bg-gray-50",
    )
    return rx.match(
        job.status,
        ("running", cancel),
        ("queued", cancel),
        ("canceling", rx.fragment()),
        (
            "failed",
            rx.fragment(
                rx.el.button(
                    rx.icon("rotate-ccw", size=15),
                    "Retry",
                    on_click=DownloadState.retry(job.uid),
                    class_name=f"{_SMALL_BUTTON} text-white bg-blue-600 hover:bg-blue-700",
                ),
                remove,
            ),
        ),
        (
            "interrupted",
            rx.fragment(
                rx.el.button(
                    rx.icon("play", size=15),
                    "Resume",
                    on_click=DownloadState.retry(job.uid),
                    class_name=f"{_SMALL_BUTTON} text-white bg-blue-600 hover:bg-blue-700",
                ),
                remove,
            ),
        ),
        rx.fragment(),
    )


def job_card(job: JobCard) -> rx.Component:
    running = job.status == "running"
    return rx.el.div(
        rx.el.div(
            rx.match(
                job.status,
                ("running", rx.icon("loader-circle", size=20, class_name="animate-spin text-blue-600 shrink-0")),
                ("queued", rx.icon("clock", size=20, class_name="text-gray-400 shrink-0")),
                ("failed", rx.icon("circle-alert", size=20, class_name="text-red-500 shrink-0")),
                ("interrupted", rx.icon("circle-alert", size=20, class_name="text-amber-500 shrink-0")),
                rx.icon("loader-circle", size=20, class_name="text-gray-400 shrink-0"),
            ),
            rx.el.div(
                rx.el.p(job.title, class_name="font-semibold text-gray-900 truncate"),
                rx.el.p(job.subtitle, class_name="text-sm text-gray-500 truncate"),
                class_name="flex-1 min-w-0",
            ),
            rx.el.div(job_actions(job), class_name="flex items-center gap-2 shrink-0"),
            class_name="flex items-center gap-3",
        ),
        rx.cond(
            running & (job.progress != ""),
            rx.el.div(
                rx.el.div(
                    rx.el.div(
                        class_name="h-full bg-blue-600 rounded-full transition-all duration-500",
                        style={"width": f"{job.percent}%"},
                    ),
                    class_name="w-full h-2 bg-blue-100 rounded-full overflow-hidden",
                ),
                rx.el.div(
                    rx.el.span(f"{job.percent}% · {job.progress}"),
                    rx.el.span(job.rate),
                    class_name="flex justify-between text-xs text-gray-600 mt-1.5",
                ),
                class_name="mt-3 pl-8",
            ),
        ),
        rx.cond(
            job.note != "",
            rx.el.p(
                job.note,
                class_name=rx.match(
                    job.status,
                    ("failed", "text-sm text-red-700 mt-2 pl-8"),
                    ("interrupted", "text-sm text-amber-800 mt-2 pl-8"),
                    "text-sm text-gray-500 mt-2 pl-8",
                ),
            ),
        ),
        class_name=rx.match(
            job.status,
            ("failed", "p-4 bg-white rounded-xl border border-red-200 shadow-sm"),
            ("interrupted", "p-4 bg-white rounded-xl border border-amber-200 shadow-sm"),
            "p-4 bg-white rounded-xl border border-gray-200 shadow-sm",
        ),
    )


def in_progress() -> rx.Component:
    return rx.el.section(
        rx.el.h2("In progress", class_name="text-lg font-bold text-gray-900 mb-1"),
        rx.el.p(
            "One series at a time, 8 files in parallel. You can keep browsing or close the tab: "
            "downloads continue in the background.",
            class_name="text-sm text-gray-500 mb-4",
        ),
        rx.cond(
            DownloadState.jobs.length() > 0,
            rx.el.div(rx.foreach(DownloadState.jobs, job_card), class_name="flex flex-col gap-3"),
            rx.el.div(
                rx.el.p("Nothing is downloading.", class_name="text-gray-500"),
                rx.el.a(
                    rx.icon("search", size=15),
                    "Find series",
                    href="/",
                    class_name="flex items-center gap-1.5 text-blue-600 font-medium hover:underline",
                ),
                class_name="flex items-center justify-between px-5 py-4 bg-white rounded-xl border border-dashed border-gray-300",
            ),
        ),
        class_name="mb-10",
    )


def saved_row(item: SavedSeries) -> rx.Component:
    return rx.el.tr(
        rx.el.td(
            rx.el.p(item.title, class_name="font-medium text-gray-900 truncate max-w-[24rem]"),
            rx.el.p(item.subtitle, class_name="text-xs text-gray-500 truncate max-w-[24rem]"),
            class_name="px-4 py-3",
        ),
        rx.el.td(item.size, class_name="px-4 py-3 text-sm text-gray-600 text-right whitespace-nowrap"),
        rx.el.td(
            _local_time.call(item.saved_at).to(str),
            class_name="px-4 py-3 text-sm text-gray-600 whitespace-nowrap",
        ),
        rx.el.td(
            rx.el.button(
                rx.icon("folder", size=14, class_name="shrink-0"),
                rx.el.span(item.folder, class_name="truncate"),
                rx.icon("copy", size=13, class_name="shrink-0 text-gray-400"),
                on_click=DownloadState.copy_path(item.path),
                title=item.path,
                class_name="flex items-center gap-1.5 max-w-[16rem] text-xs font-mono text-gray-600 hover:text-blue-700",
            ),
            class_name="px-4 py-3",
        ),
        rx.el.td(
            rx.el.div(
                rx.cond(
                    item.annotation,
                    rx.el.button(
                        rx.icon("images", size=15),
                        "Get images",
                        on_click=DownloadState.check_companions(item.uid, True),
                        title="Download the images this annotation belongs to",
                        class_name="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 whitespace-nowrap",
                    ),
                ),
                rx.el.button(
                    rx.icon("trash-2", size=16),
                    on_click=DownloadState.ask_delete(item.uid, item.title),
                    title="Delete this series",
                    class_name="p-2 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-lg",
                ),
                class_name="flex items-center justify-end gap-2",
            ),
            class_name="px-4 py-3 text-right",
        ),
        class_name="bg-white hover:bg-gray-50",
    )


def downloaded() -> rx.Component:
    return rx.el.section(
        rx.el.div(
            rx.el.h2("Downloaded", class_name="text-lg font-bold text-gray-900"),
            rx.el.span(DownloadState.saved_total, class_name="text-sm text-gray-500"),
            class_name="flex items-baseline gap-3 mb-1",
        ),
        rx.el.p(
            "Saved in the shared DICOM folder, under by-app › dicom-data-explorer. "
            "In DICOM Viewer, open that folder to look at the images.",
            class_name="text-sm text-gray-500 mb-4",
        ),
        rx.cond(
            DownloadState.saved.length() > 0,
            rx.el.div(
                rx.el.table(
                    rx.el.thead(
                        rx.el.tr(
                            *[
                                rx.el.th(
                                    t,
                                    class_name=f"px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider {extra}",
                                )
                                for t, extra in (
                                    ("Series", ""), ("Size", "text-right"), ("Downloaded", ""),
                                    ("Folder", ""), ("", ""),
                                )
                            ]
                        ),
                        class_name="bg-gray-50 border-b border-gray-200",
                    ),
                    rx.el.tbody(rx.foreach(DownloadState.saved, saved_row), class_name="divide-y divide-gray-100"),
                    class_name="min-w-full",
                ),
                class_name="overflow-x-auto rounded-xl border border-gray-200 bg-white shadow-sm",
            ),
            rx.el.div(
                rx.icon("hard-drive", size=36, class_name="text-gray-300 mb-3"),
                rx.el.p("No downloads yet.", class_name="text-gray-500"),
                rx.el.a(
                    "Find series to download",
                    href="/",
                    class_name="mt-3 text-blue-600 font-medium hover:underline",
                ),
                class_name="flex flex-col items-center justify-center py-12 bg-white rounded-xl border border-dashed border-gray-300",
            ),
        ),
    )


def downloads_content() -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.h1("Downloads", class_name="text-2xl font-bold text-gray-900 mb-6"),
            in_progress(),
            downloaded(),
            class_name="p-6 lg:p-8 max-w-6xl mx-auto",
        ),
        class_name="h-full overflow-y-auto",
    )


def downloads_page() -> rx.Component:
    return layout(downloads_content())
