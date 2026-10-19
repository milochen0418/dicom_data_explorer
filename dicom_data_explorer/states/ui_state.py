import reflex as rx


class UIState(rx.State):
    """State for the "DICOM basics" tutorial and the "How it works" strip."""

    is_onboarding_open: bool = False
    current_onboarding_step: int = 0
    # "1" once the user hid the strip; kept in the browser.
    guide_hidden: str = rx.LocalStorage("", name="dicom_explorer_guide_hidden")
    onboarding_steps: list[dict[str, str]] = [
        {
            "title": "Collections",
            "content": "A collection is a public dataset from one research project, cancer type or clinical trial, for example '4d_lung'.",
            "icon": "folder-open",
        },
        {
            "title": "Patients & Studies",
            "content": "Each collection holds patients. A patient can have several studies: one study is one visit to the scanner.",
            "icon": "users",
        },
        {
            "title": "Series & Images",
            "content": "A series is one scan in a study, such as a chest CT. It is made of many images (DICOM files). This app downloads whole series.",
            "icon": "layers",
        },
        {
            "title": "Modality & Body part",
            "content": "Modality is the kind of scanner: CT, MR (MRI), PT (PET), CR/DX (X-ray), SM (slide microscopy) and more. Body part is the area that was scanned.",
            "icon": "scan-line",
        },
        {
            "title": "Find, download, view",
            "content": "On Find series, narrow the list with the filters and press Download. Downloaded series land in the shared DICOM folder, where DICOM Viewer can open them.",
            "icon": "rocket",
        },
    ]

    @rx.event
    def open_onboarding(self):
        self.is_onboarding_open = True
        self.current_onboarding_step = 0

    @rx.event
    def close_onboarding(self):
        self.is_onboarding_open = False

    @rx.event
    def next_onboarding_step(self):
        if self.current_onboarding_step < len(self.onboarding_steps) - 1:
            self.current_onboarding_step += 1
        else:
            self.is_onboarding_open = False

    @rx.event
    def prev_onboarding_step(self):
        if self.current_onboarding_step > 0:
            self.current_onboarding_step -= 1

    @rx.event
    def hide_guide(self):
        self.guide_hidden = "1"

    @rx.event
    def show_guide(self):
        self.guide_hidden = ""
