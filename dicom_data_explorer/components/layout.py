import reflex as rx
from dicom_data_explorer.components.sidebar import sidebar
from dicom_data_explorer.components.onboarding import onboarding_modal
from dicom_data_explorer.components.dialogs import big_download_dialog, delete_dialog


def layout(content: rx.Component) -> rx.Component:
    """Main layout wrapper with sidebar, tutorial and confirmations."""
    return rx.el.div(
        sidebar(),
        rx.el.main(content, class_name="flex-1 h-full overflow-hidden bg-gray-50"),
        onboarding_modal(),
        big_download_dialog(),
        delete_dialog(),
        class_name="flex h-screen w-screen overflow-hidden font-['Inter'] text-gray-900",
    )
