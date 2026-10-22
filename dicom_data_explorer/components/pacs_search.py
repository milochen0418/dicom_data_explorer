"""The PACS side of the Find series page: the source picker, the form, studies and their series."""

import reflex as rx

from dicom_data_explorer.components.download_button import download_button
from dicom_data_explorer.states.pacs_search_state import Choice, PacsSearchState, SeriesCard, StudyCard

_FIELD = "w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none"
_LABEL = "block text-xs font-semibold text-gray-600 mb-1"


def _source_pill(choice: Choice) -> rx.Component:
    return rx.el.button(
        rx.cond(choice.value == "idc", rx.icon("globe", size=15), rx.icon("server", size=15)),
        choice.label,
        on_click=PacsSearchState.set_source(choice.value),
        class_name=rx.cond(
            PacsSearchState.source == choice.value,
            "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-blue-600 text-white",
            "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-white text-gray-700 border border-gray-300 hover:bg-gray-50",
        ),
    )


def source_picker() -> rx.Component:
    """Where to search: IDC, one PACS, or all of them (shown once a PACS is set up)."""
    return rx.el.div(
        rx.el.span("Search in", class_name="text-sm font-semibold text-gray-600 mr-1"),
        rx.foreach(PacsSearchState.choices, _source_pill),
        rx.el.a(
            rx.icon("settings", size=14),
            "PACS",
            href="/pacs",
            class_name="flex items-center gap-1 text-sm text-gray-500 hover:text-blue-700 ml-auto",
        ),
        class_name="flex flex-wrap items-center gap-2 mb-4",
    )


def _input(label: str, field: str, value, placeholder: str = "", type_: str = "text") -> rx.Component:
    return rx.el.div(
        rx.el.label(label, class_name=_LABEL),
        rx.el.input(
            value=value,
            on_change=lambda v: PacsSearchState.set_field(field, v),
            placeholder=placeholder,
            type=type_,
            class_name=_FIELD,
        ),
    )


def pacs_form() -> rx.Component:
    s = PacsSearchState
    return rx.el.div(
        _input("Patient ID", "patient_id", s.patient_id, "e.g. 12345"),
        _input("Patient name", "patient_name", s.patient_name, "WANG^XIAO* (* = anything)"),
        _input("Accession number", "accession", s.accession),
        rx.el.div(
            rx.el.label("Study date", class_name=_LABEL),
            rx.el.select(
                rx.foreach(s.date_choices, lambda c: rx.el.option(c.label, value=c.value)),
                value=s.date_preset,
                on_change=lambda v: PacsSearchState.set_field("date_preset", v),
                class_name=_FIELD,
            ),
        ),
        rx.cond(
            s.date_preset == "custom",
            rx.fragment(
                _input("From", "date_from", s.date_from, type_="date"),
                _input("To", "date_to", s.date_to, type_="date"),
            ),
        ),
        _input("Modality", "modality", s.modality, "CT, MR, SEG…"),
        _input("Study description", "description", s.description, "*chest* (* = anything)"),
        rx.el.div(
            rx.el.button(
                rx.cond(s.searching, rx.icon("loader-circle", size=16, class_name="animate-spin"), rx.icon("search", size=16)),
                rx.cond(s.searching, "Searching…", "Search"),
                on_click=PacsSearchState.search,
                disabled=s.searching,
                class_name="flex items-center justify-center gap-1.5 w-full px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-60",
            ),
            rx.el.button(
                "Clear",
                on_click=PacsSearchState.clear_form,
                class_name="px-3 py-2 rounded-lg text-sm font-medium text-gray-600 hover:text-gray-900",
            ),
            class_name="flex items-end gap-2",
        ),
        class_name="grid grid-cols-2 lg:grid-cols-4 gap-3 p-4 bg-white rounded-xl border border-gray-200 shadow-sm",
    )


def _series_row(card: SeriesCard) -> rx.Component:
    return rx.el.tr(
        rx.el.td(
            rx.el.span(card.modality, class_name="px-2 py-0.5 rounded-md bg-gray-100 text-gray-700 text-xs font-semibold"),
            class_name="px-4 py-2",
        ),
        rx.el.td(card.description, class_name="px-4 py-2 text-sm text-gray-900"),
        rx.el.td(card.date, class_name="px-4 py-2 text-sm text-gray-600 whitespace-nowrap"),
        rx.el.td(card.images, class_name="px-4 py-2 text-sm text-gray-600 text-right"),
        rx.el.td(card.found_on, class_name="px-4 py-2 text-xs text-gray-500"),
        rx.el.td(
            download_button(card.uid, on_download=PacsSearchState.download(card.uid)),
            class_name="px-4 py-2 text-right",
        ),
        class_name="bg-white",
    )


def _series_panel() -> rx.Component:
    s = PacsSearchState
    return rx.el.div(
        rx.cond(
            s.series_loading,
            rx.el.p(
                rx.icon("loader-circle", size=15, class_name="animate-spin"),
                "Asking for the series…",
                class_name="flex items-center gap-2 text-sm text-gray-500 px-4 py-3",
            ),
            rx.fragment(
                rx.foreach(s.series_problems, lambda p: rx.el.p(p, class_name="text-sm text-red-600 px-4 pt-3")),
                rx.cond(
                    s.series.length() > 0,
                    rx.el.div(
                        rx.el.table(
                            rx.el.tbody(rx.foreach(s.series, _series_row), class_name="divide-y divide-gray-100"),
                            class_name="min-w-full",
                        ),
                        rx.el.div(
                            rx.el.button(
                                rx.icon("download", size=15),
                                "Download the whole study",
                                on_click=PacsSearchState.download_study,
                                class_name="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium text-blue-700 border border-blue-200 hover:bg-blue-50",
                            ),
                            class_name="flex justify-end px-4 py-2",
                        ),
                    ),
                    rx.el.p("The PACS lists no series for this study.", class_name="text-sm text-gray-500 px-4 py-3"),
                ),
            ),
        ),
        class_name="bg-gray-50 border-t border-gray-100",
    )


def _study_row(card: StudyCard) -> rx.Component:
    open_ = PacsSearchState.open_study == card.uid
    return rx.el.div(
        rx.el.div(
            rx.cond(open_, rx.icon("chevron-down", size=18, class_name="text-gray-500 shrink-0"), rx.icon("chevron-right", size=18, class_name="text-gray-400 shrink-0")),
            rx.el.div(
                rx.el.p(
                    rx.el.span(card.patient, class_name="font-medium text-gray-900"),
                    rx.el.span(card.patient_id, class_name="ml-2 text-xs font-mono text-gray-500"),
                    class_name="truncate",
                ),
                rx.el.p(
                    rx.cond(card.description != "", card.description, "(no description)"),
                    class_name="text-sm text-gray-600 truncate",
                ),
                class_name="flex-1 min-w-0",
            ),
            rx.el.div(
                rx.el.p(card.date, class_name="text-sm text-gray-700 whitespace-nowrap"),
                rx.el.p(card.modalities, class_name="text-xs text-gray-500"),
                class_name="text-right shrink-0 w-32",
            ),
            rx.el.div(
                rx.el.p(card.counts, class_name="text-xs text-gray-500 whitespace-nowrap"),
                rx.el.p(card.found_on, title=card.found_on, class_name="text-xs text-blue-700 truncate max-w-[12rem]"),
                class_name="text-right shrink-0 w-48",
            ),
            on_click=PacsSearchState.toggle_study(card.uid),
            class_name="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-gray-50",
        ),
        rx.cond(open_, _series_panel()),
        class_name="border-b border-gray-100 last:border-b-0",
    )


def pacs_results() -> rx.Component:
    s = PacsSearchState
    return rx.el.div(
        rx.cond(s.error != "", rx.el.p(s.error, class_name="text-sm text-red-600 mt-3")),
        rx.foreach(
            s.problems,
            lambda p: rx.el.p(
                rx.icon("circle-alert", size=15, class_name="shrink-0"), p,
                class_name="flex items-center gap-2 text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mt-3",
            ),
        ),
        rx.cond(
            s.searched,
            rx.cond(
                s.studies.length() > 0,
                rx.el.div(
                    rx.el.p(
                        rx.cond(s.studies.length() == 1, "1 study", f"{s.studies.length()} studies"),
                        class_name="text-lg font-bold text-gray-900 mt-6 mb-3",
                    ),
                    rx.el.div(rx.foreach(s.studies, _study_row), class_name="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden"),
                ),
                rx.el.div(
                    rx.icon("search-x", size=36, class_name="text-gray-300 mb-3"),
                    rx.el.h3("No studies match", class_name="text-lg font-medium text-gray-900"),
                    rx.el.p("Check the spelling, or widen the dates.", class_name="text-gray-500 mt-1"),
                    class_name="flex flex-col items-center justify-center py-14 mt-6 bg-white rounded-xl border border-dashed border-gray-300",
                ),
            ),
        ),
    )


def pacs_search_content() -> rx.Component:
    return rx.el.div(
        rx.el.h1(f"Search {PacsSearchState.source_label}", class_name="text-2xl font-bold text-gray-900"),
        rx.el.p(
            "A PACS only answers what you ask: give a patient, an accession number or a date range.",
            class_name="text-gray-500 mt-0.5 mb-4",
        ),
        pacs_form(),
        pacs_results(),
    )
