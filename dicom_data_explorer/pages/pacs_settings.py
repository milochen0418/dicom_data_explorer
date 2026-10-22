"""The PACS page: the hospital archives this app can search and download from."""

import reflex as rx
from reflex_base.vars.function import FunctionStringVar

from dicom_data_explorer.components.layout import layout
from dicom_data_explorer.states.pacs_settings_state import CheckLine, PacsCard, PacsSettingsState

_FIELD = "w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none"
_LABEL = "block text-xs font-semibold text-gray-600 mb-1"
_BUTTON = "inline-flex items-center justify-center gap-1.5 rounded-lg text-sm font-medium transition-colors whitespace-nowrap px-3 py-1.5"

# Format an ISO timestamp in the browser's own time zone.
_local_time = FunctionStringVar.create(
    "((iso) => iso ? new Date(iso).toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'}) : '')"
)


def receiver_box() -> rx.Component:
    s = PacsSettingsState
    return rx.el.div(
        rx.icon("radio-receiver", size=20, class_name="text-blue-600 shrink-0 mt-0.5"),
        rx.el.div(
            rx.el.p("For C-MOVE, the PACS sends the images to this app.", class_name="text-sm font-semibold text-gray-900"),
            rx.el.p(
                "Ask its administrator to register this app as a destination: AE title ",
                rx.el.code(s.receiver_aet, class_name="font-mono font-semibold"),
                ", IP ",
                rx.el.code(rx.cond(s.receiver_ip != "", s.receiver_ip, "this computer's IP"), class_name="font-mono font-semibold"),
                ", port ",
                rx.el.code(s.receiver_port, class_name="font-mono font-semibold"),
                ". C-GET and DICOMweb need none of this.",
                class_name="text-sm text-gray-700 mt-0.5",
            ),
        ),
        class_name="flex gap-3 p-4 mb-6 rounded-xl bg-blue-50 border border-blue-100",
    )


def check_line(line: CheckLine) -> rx.Component:
    return rx.el.li(
        rx.match(
            line.state,
            ("ok", rx.icon("circle-check", size=15, class_name="text-green-600 shrink-0 mt-0.5")),
            ("fail", rx.icon("circle-x", size=15, class_name="text-red-600 shrink-0 mt-0.5")),
            rx.icon("info", size=15, class_name="text-amber-600 shrink-0 mt-0.5"),
        ),
        rx.el.span(line.text),
        class_name="flex gap-2 text-sm text-gray-700",
    )


def pacs_card(card: PacsCard) -> rx.Component:
    testing = PacsSettingsState.testing == card.id
    return rx.el.div(
        rx.el.div(
            rx.icon("server", size=20, class_name="text-gray-500 shrink-0"),
            rx.el.div(
                rx.el.div(
                    rx.el.p(card.label, class_name="font-semibold text-gray-900 truncate"),
                    rx.el.span(card.kind, class_name="px-2 py-0.5 rounded-md bg-gray-100 text-gray-700 text-xs font-semibold"),
                    rx.el.span(card.retrieve, class_name="px-2 py-0.5 rounded-md bg-blue-50 text-blue-700 text-xs font-semibold"),
                    class_name="flex items-center gap-2",
                ),
                rx.el.p(card.address, class_name="text-xs font-mono text-gray-500 truncate mt-0.5"),
                class_name="flex-1 min-w-0",
            ),
            rx.el.div(
                rx.el.button(
                    rx.cond(testing, rx.icon("loader-circle", size=15, class_name="animate-spin"), rx.icon("plug-zap", size=15)),
                    rx.cond(testing, "Testing…", "Test connection"),
                    on_click=PacsSettingsState.test(card.id),
                    disabled=PacsSettingsState.testing != "",
                    class_name=f"{_BUTTON} text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-50",
                ),
                rx.el.button(
                    rx.icon("pencil", size=15),
                    "Edit",
                    on_click=PacsSettingsState.open_edit(card.id),
                    class_name=f"{_BUTTON} text-gray-700 border border-gray-300 hover:bg-gray-50",
                ),
                rx.el.button(
                    rx.icon("trash-2", size=15),
                    on_click=PacsSettingsState.ask_delete(card.id, card.label),
                    title="Remove this PACS",
                    class_name=f"{_BUTTON} text-gray-500 border border-gray-300 hover:text-red-600 hover:bg-red-50",
                ),
                class_name="flex items-center gap-2 shrink-0",
            ),
            class_name="flex items-center gap-3",
        ),
        rx.cond(
            card.tested_at != "",
            rx.el.div(
                rx.el.p(
                    rx.cond(card.test_ok, "Last test passed", "Last test found problems"),
                    " · ",
                    _local_time.call(card.tested_at).to(str),
                    class_name=rx.cond(card.test_ok, "text-xs font-semibold text-green-700", "text-xs font-semibold text-red-700"),
                ),
                rx.el.ul(rx.foreach(card.report, check_line), class_name="mt-1.5 flex flex-col gap-1"),
                class_name="mt-3 pl-8",
            ),
            rx.el.p("Not tested yet.", class_name="mt-3 pl-8 text-xs text-gray-500"),
        ),
        class_name="p-4 bg-white rounded-xl border border-gray-200 shadow-sm",
    )


def _input(label: str, field: str, value, placeholder: str = "", type_: str = "text", hint: str = "") -> rx.Component:
    return rx.el.div(
        rx.el.label(label, class_name=_LABEL),
        rx.el.input(
            value=value,
            on_change=lambda v: PacsSettingsState.set_field(field, v),
            placeholder=placeholder,
            type=type_,
            class_name=_FIELD,
        ),
        rx.el.p(hint, class_name="text-xs text-gray-500 mt-1") if hint else rx.fragment(),
    )


def _select(label: str, field: str, value, options: list[tuple[str, str]]) -> rx.Component:
    return rx.el.div(
        rx.el.label(label, class_name=_LABEL),
        rx.el.select(
            *[rx.el.option(text, value=v) for v, text in options],
            value=value,
            on_change=lambda v: PacsSettingsState.set_field(field, v),
            class_name=_FIELD,
        ),
    )


def dicomweb_fields() -> rx.Component:
    s = PacsSettingsState
    secret_hint = rx.cond(s.has_secret, "Leave blank to keep the stored one.", "")
    return rx.el.div(
        rx.el.div(
            _input("DICOMweb address (QIDO-RS)", "f_url", s.f_url, "http://pacs.example:8042/dicom-web"),
            class_name="col-span-2",
        ),
        rx.el.div(
            _input("WADO-RS address, if different", "f_wado_url", s.f_wado_url, "Usually blank"),
            class_name="col-span-2",
        ),
        _select("Login", "f_auth", s.f_auth, [("none", "None"), ("basic", "User name and password"), ("bearer", "Token (Bearer)")]),
        rx.el.div(
            rx.el.label(
                rx.el.input(
                    type="checkbox",
                    checked=s.f_verify,
                    on_change=lambda _checked: PacsSettingsState.toggle_verify(),
                    class_name="mr-2",
                ),
                "Check the server's certificate (https)",
                class_name="flex items-center text-sm text-gray-700 mt-6",
            ),
        ),
        rx.cond(
            s.f_auth == "basic",
            rx.fragment(
                _input("User name", "f_username", s.f_username),
                rx.el.div(
                    _input("Password", "f_password", s.f_password, type_="password"),
                    rx.el.p(secret_hint, class_name="text-xs text-gray-500 mt-1"),
                ),
            ),
        ),
        rx.cond(
            s.f_auth == "bearer",
            rx.el.div(
                _input("Token", "f_token", s.f_token, type_="password"),
                rx.el.p(secret_hint, class_name="text-xs text-gray-500 mt-1"),
                class_name="col-span-2",
            ),
        ),
        class_name="grid grid-cols-2 gap-3",
    )


def dimse_fields() -> rx.Component:
    s = PacsSettingsState
    return rx.el.div(
        _input("Host or IP", "f_host", s.f_host, "10.0.0.5"),
        _input("Port", "f_port", s.f_port, "104", type_="number"),
        _input("The PACS's AE title", "f_called", s.f_called, "PACS_MAIN"),
        _input("Our AE title", "f_calling", s.f_calling, "DDE_EXPLORER"),
        _select("Retrieve with", "f_retrieve", s.f_retrieve, [
            ("C-GET", "C-GET (images come back on our connection)"),
            ("C-MOVE", "C-MOVE (the PACS sends them to our receiver)"),
        ]),
        class_name="grid grid-cols-2 gap-3",
    )


def form_dialog() -> rx.Component:
    s = PacsSettingsState
    kind_button = "flex-1 px-3 py-2 text-sm font-medium rounded-lg border"
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title(rx.cond(s.form_id == "", "Add a PACS", "Edit PACS")),
            rx.el.div(
                _input("Name", "f_label", s.f_label, "Hospital PACS (test)"),
                rx.el.div(
                    rx.el.label("How the app talks to it", class_name=_LABEL),
                    rx.el.div(
                        rx.el.button(
                            "DICOMweb (HTTP)",
                            on_click=PacsSettingsState.set_field("f_kind", "dicomweb"),
                            class_name=rx.cond(s.f_kind == "dicomweb", f"{kind_button} bg-blue-600 text-white border-blue-600", f"{kind_button} bg-white text-gray-700 border-gray-300"),
                        ),
                        rx.el.button(
                            "DIMSE (C-FIND, C-GET, C-MOVE)",
                            on_click=PacsSettingsState.set_field("f_kind", "dimse"),
                            class_name=rx.cond(s.f_kind == "dimse", f"{kind_button} bg-blue-600 text-white border-blue-600", f"{kind_button} bg-white text-gray-700 border-gray-300"),
                        ),
                        class_name="flex gap-2",
                    ),
                    class_name="mt-3",
                ),
                rx.el.div(rx.cond(s.f_kind == "dicomweb", dicomweb_fields(), dimse_fields()), class_name="mt-3"),
                rx.el.div(
                    _input("Most studies per search", "f_max", s.f_max, "200", type_="number"),
                    class_name="mt-3 w-1/2 pr-1.5",
                ),
                rx.cond(
                    s.form_error != "",
                    rx.el.p(s.form_error, class_name="text-sm text-red-600 mt-3"),
                ),
                class_name="mt-2",
            ),
            rx.el.div(
                rx.dialog.close(
                    rx.el.button(
                        "Cancel",
                        on_click=PacsSettingsState.close_form,
                        class_name=f"{_BUTTON} px-4 py-2 border border-gray-300 text-gray-700 hover:bg-gray-50",
                    ),
                ),
                rx.el.button(
                    "Save",
                    on_click=PacsSettingsState.save,
                    class_name=f"{_BUTTON} px-4 py-2 text-white bg-blue-600 hover:bg-blue-700",
                ),
                class_name="flex justify-end gap-3 mt-5",
            ),
            class_name="max-w-xl",
        ),
        open=s.form_open,
        on_open_change=PacsSettingsState.set_form_open,
    )


def delete_dialog() -> rx.Component:
    s = PacsSettingsState
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Remove this PACS?"),
            rx.alert_dialog.description(
                f"“{s.delete_label}” disappears from the search page. Series already downloaded from it stay.",
                class_name="text-gray-600",
            ),
            rx.el.div(
                rx.alert_dialog.cancel(
                    rx.el.button("Cancel", on_click=PacsSettingsState.cancel_delete, class_name=f"{_BUTTON} px-4 py-2 border border-gray-300 text-gray-700 hover:bg-gray-50"),
                ),
                rx.alert_dialog.action(
                    rx.el.button("Remove", on_click=PacsSettingsState.confirm_delete, class_name=f"{_BUTTON} px-4 py-2 text-white bg-red-600 hover:bg-red-700"),
                ),
                class_name="flex justify-end gap-3 mt-4",
            ),
        ),
        open=s.delete_id != "",
    )


def pacs_settings_content() -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.div(
                rx.el.h1("PACS", class_name="text-2xl font-bold text-gray-900"),
                rx.el.p(
                    "Hospital archives this app can search and download from, next to IDC.",
                    class_name="text-gray-500 mt-0.5",
                ),
            ),
            rx.el.button(
                rx.icon("plus", size=16),
                "Add a PACS",
                on_click=PacsSettingsState.open_add,
                class_name=f"{_BUTTON} px-4 py-2 text-white bg-blue-600 hover:bg-blue-700 shadow-sm",
            ),
            class_name="flex items-end justify-between mb-4",
        ),
        receiver_box(),
        rx.cond(
            PacsSettingsState.cards.length() > 0,
            rx.el.div(rx.foreach(PacsSettingsState.cards, pacs_card), class_name="flex flex-col gap-3"),
            rx.el.div(
                rx.icon("server", size=36, class_name="text-gray-300 mb-3"),
                rx.el.h3("No PACS yet", class_name="text-lg font-medium text-gray-900"),
                rx.el.p(
                    "Add one with the address, AE titles and port its administrator gives you. "
                    "To practise, start the fake PACS in dev/pacs_lab of this project.",
                    class_name="text-gray-500 mt-1 max-w-lg text-center",
                ),
                class_name="flex flex-col items-center justify-center py-14 bg-white rounded-xl border border-dashed border-gray-300",
            ),
        ),
        rx.el.p(
            "Settings are kept in this app's own data folder, readable by its owner only; "
            "passwords never go into the shared DICOM folder.",
            class_name="text-xs text-gray-400 mt-6",
        ),
        form_dialog(),
        delete_dialog(),
        class_name="h-full overflow-y-auto p-6 lg:p-8 max-w-4xl",
    )


def pacs_settings_page() -> rx.Component:
    return layout(pacs_settings_content())
