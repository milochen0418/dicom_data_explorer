import reflex as rx
from dicom_data_explorer.states.idc_state import IDCState, SeriesRow
from dicom_data_explorer.states.pacs_search_state import PacsSearchState
from dicom_data_explorer.states.ui_state import UIState
from dicom_data_explorer.components.layout import layout
from dicom_data_explorer.components.download_button import download_button
from dicom_data_explorer.components.pacs_search import pacs_search_content, source_picker
from dicom_data_explorer.components.tooltip import helper_tooltip

_FIELD = "w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none"
_LABEL = "flex items-center text-xs font-semibold text-gray-600 mb-1"


def step(number: str, text: str) -> rx.Component:
    return rx.el.div(
        rx.el.span(
            number,
            class_name="w-6 h-6 shrink-0 rounded-full bg-blue-600 text-white text-xs font-bold flex items-center justify-center",
        ),
        rx.el.span(text, class_name="text-sm text-gray-700"),
        class_name="flex items-center gap-2",
    )


def guide() -> rx.Component:
    return rx.cond(
        UIState.guide_hidden == "",
        rx.el.div(
            rx.el.div(
                step("1", "Narrow the list with the filters."),
                rx.icon("chevron-right", size=16, class_name="text-blue-300 shrink-0 hidden lg:block"),
                step("2", "Press Download. (Click a row for its details.)"),
                rx.icon("chevron-right", size=16, class_name="text-blue-300 shrink-0 hidden lg:block"),
                step("3", "Open it from Downloads or in DICOM Viewer."),
                class_name="flex flex-col lg:flex-row lg:items-center gap-3 flex-1",
            ),
            rx.el.button(
                "Hide",
                on_click=UIState.hide_guide,
                class_name="text-sm text-blue-700 hover:underline shrink-0",
            ),
            class_name="flex items-start lg:items-center gap-4 px-4 py-3 mb-4 rounded-xl bg-blue-50 border border-blue-100",
        ),
    )


def select(label: str, value, choices, on_change, all_label: str, tip: str = "") -> rx.Component:
    return rx.el.div(
        rx.el.label(label, helper_tooltip(tip) if tip else None, class_name=_LABEL),
        rx.el.select(
            rx.el.option(all_label, value=""),
            rx.foreach(choices, lambda c: rx.el.option(c.label, value=c.value)),
            value=value,
            on_change=on_change,
            class_name=_FIELD,
        ),
    )


def filter_bar() -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.label("Search", class_name=_LABEL),
            rx.el.div(
                rx.icon("search", size=16, class_name="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"),
                rx.debounce_input(
                    rx.el.input(
                        value=IDCState.text,
                        on_change=IDCState.set_text,
                        placeholder="Description, collection, patient or UID",
                        class_name=_FIELD + " pl-9",
                    ),
                    debounce_timeout=400,
                ),
                class_name="relative",
            ),
            class_name="col-span-2 lg:col-span-2",
        ),
        select("Collection", IDCState.collection, IDCState.collections, IDCState.set_collection,
               "All collections", "A dataset from one research project or trial."),
        select("Modality", IDCState.modality, IDCState.modalities, IDCState.set_modality,
               "All modalities", "The kind of scanner: CT, MR (MRI), PT (PET), SM (slide microscopy)…"),
        select("Body part", IDCState.body_part, IDCState.body_parts, IDCState.set_body_part,
               "All body parts", "The part of the body that was scanned."),
        rx.el.div(
            rx.el.label("Sort", class_name=_LABEL),
            rx.el.select(
                rx.foreach(IDCState.sort_choices, lambda c: rx.el.option(c.label, value=c.value)),
                value=IDCState.sort,
                on_change=IDCState.set_sort,
                class_name=_FIELD,
            ),
        ),
        class_name="grid grid-cols-2 lg:grid-cols-6 gap-3 p-4 bg-white rounded-xl border border-gray-200 shadow-sm",
    )


def result_header() -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.span(IDCState.total_text, class_name="text-lg font-bold text-gray-900"),
            rx.el.span(IDCState.range_text, class_name="text-sm text-gray-500"),
            rx.cond(
                IDCState.loading,
                rx.icon("loader-circle", size=18, class_name="animate-spin text-blue-600"),
            ),
            class_name="flex items-baseline gap-3",
        ),
        rx.cond(
            IDCState.has_filters,
            rx.el.button(
                rx.icon("x", size=14),
                "Clear filters",
                on_click=IDCState.clear_filters,
                class_name="flex items-center gap-1 text-sm text-gray-600 hover:text-red-600",
            ),
        ),
        class_name="flex items-center justify-between mt-6 mb-3",
    )


def _th(text: str, extra: str = "") -> rx.Component:
    return rx.el.th(
        text,
        class_name=f"px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider whitespace-nowrap {extra}",
    )


def result_row(row: SeriesRow) -> rx.Component:
    selected = IDCState.details_open & (IDCState.details.uid == row.uid)
    return rx.el.tr(
        rx.el.td(
            rx.el.p(
                rx.cond(row.description != "", row.description, "(no description)"),
                class_name="font-medium text-gray-900 truncate max-w-[20rem]",
                title=row.description,
            ),
            rx.el.p(row.collection, class_name="text-xs text-gray-500 truncate max-w-[20rem]"),
            class_name="px-4 py-2.5",
        ),
        rx.el.td(
            rx.el.span(row.modality, class_name="px-2 py-0.5 rounded-md bg-gray-100 text-gray-700 text-xs font-semibold"),
            class_name="px-4 py-2.5",
        ),
        rx.el.td(row.body_part, class_name="px-4 py-2.5 text-sm text-gray-600"),
        rx.el.td(rx.cond(row.date != "", row.date, "—"), class_name="px-4 py-2.5 text-sm text-gray-600 whitespace-nowrap"),
        rx.el.td(row.images, class_name="px-4 py-2.5 text-sm text-gray-600 text-right"),
        rx.el.td(row.size, class_name="px-4 py-2.5 text-sm text-gray-600 text-right whitespace-nowrap"),
        rx.el.td(download_button(row.uid), class_name="px-4 py-2 text-right"),
        on_click=IDCState.show_details(row.uid),
        class_name=rx.cond(
            selected,
            "bg-blue-50 cursor-pointer",
            "bg-white hover:bg-gray-50 cursor-pointer",
        ),
    )


def results_table() -> rx.Component:
    return rx.el.div(
        rx.el.table(
            rx.el.thead(
                rx.el.tr(
                    _th("Series"),
                    _th("Modality"),
                    _th("Body part"),
                    _th("Date"),
                    _th("Images", "text-right"),
                    _th("Size", "text-right"),
                    _th(""),
                ),
                class_name="bg-gray-50 border-b border-gray-200",
            ),
            rx.el.tbody(
                rx.foreach(IDCState.rows, result_row),
                class_name="divide-y divide-gray-100",
            ),
            class_name="min-w-full",
        ),
        class_name=rx.cond(
            IDCState.loading,
            "overflow-x-auto rounded-xl border border-gray-200 bg-white shadow-sm opacity-60 transition-opacity",
            "overflow-x-auto rounded-xl border border-gray-200 bg-white shadow-sm transition-opacity",
        ),
    )


def pager() -> rx.Component:
    button = "flex items-center gap-1 px-3 py-1.5 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
    return rx.el.div(
        rx.el.button(
            rx.icon("chevron-left", size=16),
            "Previous",
            on_click=IDCState.go_to_page(IDCState.page - 1),
            disabled=IDCState.page <= 1,
            class_name=button,
        ),
        rx.el.span(
            f"Page {IDCState.page} of {IDCState.page_count}",
            class_name="text-sm text-gray-600",
        ),
        rx.el.button(
            "Next",
            rx.icon("chevron-right", size=16),
            on_click=IDCState.go_to_page(IDCState.page + 1),
            disabled=IDCState.page >= IDCState.page_count,
            class_name=button,
        ),
        class_name="flex items-center justify-end gap-3 mt-4",
    )


def empty_state() -> rx.Component:
    return rx.el.div(
        rx.icon("search", size=40, class_name="text-gray-300 mb-3"),
        rx.el.h3("No series match these filters", class_name="text-lg font-medium text-gray-900"),
        rx.el.p("Try fewer words or another collection.", class_name="text-gray-500 mt-1"),
        rx.el.button(
            "Clear filters",
            on_click=IDCState.clear_filters,
            class_name="mt-4 px-4 py-2 rounded-lg bg-gray-900 text-white text-sm font-medium hover:bg-gray-800",
        ),
        class_name="flex flex-col items-center justify-center py-16 bg-white rounded-xl border border-dashed border-gray-300",
    )


def results() -> rx.Component:
    return rx.cond(
        IDCState.error != "",
        rx.el.div(
            rx.icon("circle-alert", size=18),
            IDCState.error,
            class_name="flex items-center gap-2 p-4 rounded-xl bg-red-50 text-red-700 border border-red-200",
        ),
        rx.cond(
            IDCState.loaded,
            rx.cond(
                IDCState.total > 0,
                rx.fragment(results_table(), pager()),
                empty_state(),
            ),
            rx.el.div(
                rx.icon("loader-circle", size=28, class_name="animate-spin text-blue-600"),
                rx.el.p("Loading the IDC index…", class_name="mt-3 text-gray-500"),
                class_name="flex flex-col items-center justify-center py-16",
            ),
        ),
    )


def detail(label: str, value, mono: bool = False) -> rx.Component:
    return rx.cond(
        value != "",
        rx.el.div(
            rx.el.dt(label, class_name="text-xs font-semibold text-gray-500 uppercase tracking-wider"),
            rx.el.dd(
                value,
                class_name="text-xs text-gray-800 font-mono break-all" if mono else "text-sm text-gray-900 break-words",
            ),
            class_name="mb-3",
        ),
    )


def details_panel() -> rx.Component:
    d = IDCState.details
    return rx.cond(
        IDCState.details_open,
        rx.el.aside(
            rx.el.div(
                rx.el.h3("Series details", class_name="font-bold text-gray-900"),
                rx.el.button(
                    rx.icon("x", size=18),
                    on_click=IDCState.close_details,
                    title="Close",
                    class_name="text-gray-400 hover:text-gray-700 p-1 rounded-md hover:bg-gray-100",
                ),
                class_name="flex justify-between items-center px-5 h-14 border-b border-gray-100 shrink-0",
            ),
            rx.el.div(
                rx.el.h4(
                    rx.cond(d.description != "", d.description, "(no description)"),
                    class_name="text-lg font-semibold text-gray-900 mb-1",
                ),
                rx.el.p(f"{d.collection} · {d.modality}", class_name="text-sm text-gray-500 mb-4"),
                download_button(d.uid, wide=True),
                rx.cond(
                    d.preview_url != "",
                    rx.el.a(
                        rx.icon("external-link", size=15),
                        "Preview on the IDC website",
                        href=d.preview_url,
                        target="_blank",
                        rel="noopener noreferrer",
                        class_name="mt-2 w-full flex items-center justify-center gap-1.5 py-2 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50",
                    ),
                ),
                rx.el.p(
                    "Look at the images online first; download only what you need.",
                    class_name="text-xs text-gray-500 mt-1 mb-5 text-center",
                ),
                rx.el.dl(
                    detail("Body part", d.body_part),
                    detail("Series date", d.date),
                    detail("Images", d.images.to_string()),
                    detail("Size", d.size),
                    detail("Patient", d.patient),
                    detail("Study", d.study),
                    detail("Scanner", d.manufacturer),
                    detail("License", d.license),
                    detail("Series UID", d.uid, mono=True),
                ),
                class_name="p-5 overflow-y-auto flex-1",
            ),
            # Floats over the results, so opening it never squeezes the table.
            class_name="absolute right-0 top-0 z-20 w-96 max-w-full h-full bg-white border-l border-gray-200 flex flex-col shadow-2xl",
        ),
    )


def idc_content() -> rx.Component:
    return rx.fragment(
        rx.el.div(
            rx.el.div(
                rx.el.h1("Find DICOM series", class_name="text-2xl font-bold text-gray-900"),
                rx.el.p(
                    "Public imaging data from the NCI Imaging Data Commons (IDC).",
                    class_name="text-gray-500 mt-0.5",
                ),
            ),
            rx.cond(
                UIState.guide_hidden != "",
                rx.el.button(
                    rx.icon("circle-help", size=16),
                    "How it works",
                    on_click=UIState.show_guide,
                    class_name="flex items-center gap-1.5 text-sm text-gray-600 hover:text-blue-700",
                ),
            ),
            class_name="flex items-end justify-between mb-4",
        ),
        guide(),
        filter_bar(),
        result_header(),
        results(),
    )


def idc_search_content() -> rx.Component:
    return rx.el.div(
        rx.el.div(
            # The picker appears once a PACS is set up on the PACS page.
            rx.cond(PacsSearchState.choices.length() > 1, source_picker()),
            rx.cond(PacsSearchState.on_pacs, pacs_search_content(), idc_content()),
            class_name="flex-1 min-w-0 h-full overflow-y-auto p-6 lg:p-8",
        ),
        rx.cond(PacsSearchState.on_pacs, rx.fragment(), details_panel()),
        class_name="relative flex h-full w-full",
    )


def idc_search_page() -> rx.Component:
    return layout(idc_search_content())
