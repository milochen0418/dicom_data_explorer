import reflex as rx
from dicom_data_explorer.pages.idc_search import idc_search_page
from dicom_data_explorer.pages.downloads import downloads_page
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
