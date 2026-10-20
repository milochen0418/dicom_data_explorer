import reflex as rx
from reflex_ddns_auth.intent import intent

from dicom_data_explorer.pages.fetch_series import fetch_series_page
from dicom_data_explorer.pages.idc_search import idc_search_page
from dicom_data_explorer.pages.downloads import downloads_page
from dicom_data_explorer.states.fetch_state import FetchState
from dicom_data_explorer.states.idc_state import IDCState
from dicom_data_explorer.states.download_state import DownloadState


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
search_on_load = [IDCState.run_search, DownloadState.sync]
app.add_page(idc_search_page, route="/", title="Find series · DICOM Explorer", on_load=search_on_load)
# The search page's old address, for bookmarks.
app.add_page(idc_search_page, route="/idc-search", title="Find series · DICOM Explorer", on_load=search_on_load)
app.add_page(downloads_page, route="/downloads", title="Downloads · DICOM Explorer", on_load=DownloadState.sync)


# DDNS Intent: DICOM Viewer opens this page in a dialog when an annotation's
# images are not downloaded yet; it fetches them and hands back.
@intent(
    app,
    action="dicom.fetch-series",
    on_open=FetchState.open_intent,
    title="Fetch the images an annotation needs",
    params={"series": str, "title": str},
    required=("series",),
)
def fetch_series_intent() -> rx.Component:
    return fetch_series_page()
