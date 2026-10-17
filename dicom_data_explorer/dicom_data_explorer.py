import reflex as rx
from dicom_data_explorer.pages.welcome import welcome_page
from dicom_data_explorer.pages.idc_search import idc_search_page
from dicom_data_explorer.pages.downloads import downloads_page
from dicom_data_explorer.states.idc_state import IDCState
from dicom_data_explorer.components.layout import layout


def index() -> rx.Component:
    return layout(welcome_page())


app = rx.App(
    head_components=[
        rx.el.link(rel="preconnect", href="https://fonts.googleapis.com"),
        rx.el.link(rel="preconnect", href="https://fonts.gstatic.com", cross_origin=""),
        rx.el.link(
            href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
            rel="stylesheet",
        ),
    ],
)
app.add_page(index, route="/")
app.add_page(idc_search_page, route="/idc-search", on_load=IDCState.load_initial_data)
app.add_page(downloads_page, route="/downloads")