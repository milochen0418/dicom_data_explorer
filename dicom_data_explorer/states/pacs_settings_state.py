"""State of the PACS page: the PACS cards, the form that edits one, "Test connection".

Passwords and tokens never reach the browser: the form shows them blank, and
a blank field keeps what is stored.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timezone

import reflex as rx

from dicom_data_explorer.services import pacs_config, sources
from dicom_data_explorer.services.pacs_config import PacsProfile

FORM_FIELDS = (
    "f_label", "f_kind", "f_url", "f_wado_url", "f_auth", "f_username", "f_password", "f_token",
    "f_host", "f_port", "f_called", "f_calling", "f_retrieve", "f_max",
)


@dataclasses.dataclass
class CheckLine:
    state: str = ""  # ok | fail | note
    text: str = ""


@dataclasses.dataclass
class PacsCard:
    id: str = ""
    label: str = ""
    kind: str = ""  # DICOMweb | DIMSE
    address: str = ""
    retrieve: str = ""  # WADO-RS | C-GET | C-MOVE
    tested_at: str = ""  # ISO time of the last test, "" if never
    test_ok: bool = False
    report: list[CheckLine] = dataclasses.field(default_factory=list)


def _card(p: PacsProfile) -> PacsCard:
    report = []
    for line in p.test_report:
        state, _, text = line.partition("|")
        report.append(CheckLine(state, text))
    return PacsCard(
        id=p.id,
        label=p.label,
        kind="DICOMweb" if p.kind == pacs_config.DICOMWEB else "DIMSE",
        address=p.address,
        retrieve="WADO-RS" if p.kind == pacs_config.DICOMWEB else p.retrieve,
        tested_at=p.tested_at,
        test_ok=p.test_ok,
        report=report,
    )


class PacsSettingsState(rx.State):
    cards: list[PacsCard] = []
    testing: str = ""  # the card being tested

    form_open: bool = False
    form_id: str = ""  # "" while adding a card
    f_label: str = ""
    f_kind: str = pacs_config.DICOMWEB
    f_url: str = ""
    f_wado_url: str = ""
    f_auth: str = "none"
    f_username: str = ""
    f_password: str = ""
    f_token: str = ""
    f_verify: bool = True
    f_host: str = ""
    f_port: str = "104"
    f_called: str = ""
    f_calling: str = pacs_config.RECEIVER_AET
    f_retrieve: str = "C-GET"
    f_max: str = "200"
    has_secret: bool = False  # the card being edited keeps a password or token
    form_error: str = ""

    delete_id: str = ""
    delete_label: str = ""

    receiver_aet: str = pacs_config.RECEIVER_AET
    receiver_port: str = str(pacs_config.RECEIVER_PORT)
    receiver_ip: str = pacs_config.host_ip()

    def _reload_cards(self) -> None:
        self.cards = [_card(p) for p in pacs_config.load()]

    @rx.event
    def load(self):
        self._reload_cards()

    @rx.event
    def set_field(self, name: str, value: str):
        if name in FORM_FIELDS:
            setattr(self, name, value)

    @rx.event
    def toggle_verify(self):
        self.f_verify = not self.f_verify

    @rx.event
    def set_form_open(self, value: bool):
        self.form_open = bool(value)

    def _fill(self, p: PacsProfile) -> None:
        self.f_label, self.f_kind = p.label, p.kind
        self.f_url, self.f_wado_url, self.f_auth = p.url, p.wado_url, p.auth
        self.f_username, self.f_password, self.f_token = p.username, "", ""
        self.f_verify = p.verify_tls
        self.f_host, self.f_port = p.host, str(p.port)
        self.f_called, self.f_calling, self.f_retrieve = p.called_aet, p.calling_aet, p.retrieve
        self.f_max = str(p.max_results)
        self.has_secret = bool(p.password or p.token)
        self.form_error = ""

    @rx.event
    def open_add(self):
        self.form_id = ""
        self._fill(PacsProfile(id="", label=""))
        self.form_open = True

    @rx.event
    def open_edit(self, card_id: str):
        profile = next((p for p in pacs_config.load() if p.id == card_id), None)
        if profile is None:
            return rx.toast.error("That PACS is gone; the list was reloaded.")
        self.form_id = card_id
        self._fill(profile)
        self.form_open = True

    @rx.event
    def close_form(self):
        self.form_open = False

    @rx.event
    def save(self):
        profiles = pacs_config.load()
        existing = next((p for p in profiles if p.id == self.form_id), None)
        try:
            port, max_results = int(self.f_port or 0), int(self.f_max or 0)
        except ValueError:
            self.form_error = "The port and the maximum must be whole numbers."
            return
        profile = PacsProfile(
            id=existing.id if existing else "",
            label=self.f_label.strip(),
            kind=self.f_kind,
            url=self.f_url.strip(),
            wado_url=self.f_wado_url.strip(),
            auth=self.f_auth,
            username=self.f_username.strip(),
            # A blank secret keeps the stored one.
            password=self.f_password or (existing.password if existing else ""),
            token=self.f_token.strip() or (existing.token if existing else ""),
            verify_tls=self.f_verify,
            host=self.f_host.strip(),
            port=port,
            called_aet=self.f_called.strip(),
            calling_aet=self.f_calling.strip(),
            retrieve=self.f_retrieve,
            max_results=max(1, max_results or 200),
        )
        problem = pacs_config.problem(profile)
        if problem:
            self.form_error = problem
            return
        if existing:
            profiles[profiles.index(existing)] = profile  # its old test results no longer apply
        else:
            profile.id = pacs_config.new_id(profile.label, {p.id for p in profiles})
            profiles.append(profile)
        pacs_config.save(profiles)
        sources.reload()
        self.form_open = False
        self._reload_cards()
        return rx.toast.success(f"Saved {profile.label}. Press Test connection to check it.")

    @rx.event
    def ask_delete(self, card_id: str, label: str):
        self.delete_id, self.delete_label = card_id, label

    @rx.event
    def cancel_delete(self):
        self.delete_id = ""

    @rx.event
    def confirm_delete(self):
        card_id, self.delete_id = self.delete_id, ""
        pacs_config.save([p for p in pacs_config.load() if p.id != card_id])
        sources.reload()
        self._reload_cards()
        return rx.toast.success(f"Removed {self.delete_label}.")

    @rx.event(background=True)
    async def test(self, card_id: str):
        """Run the source's checks and keep the result on its card."""
        async with self:
            if self.testing:
                return
            self.testing = card_id
        source = sources.get(card_id)
        try:
            report = await asyncio.to_thread(source.test) if source else [("fail", "Save the PACS again.")]
        except Exception as exc:  # noqa: BLE001 - shown on the card
            report = [("fail", f"The test itself failed: {exc}")]
        profiles = pacs_config.load()
        for p in profiles:
            if p.id == card_id:
                p.tested_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                p.test_ok = all(state != "fail" for state, _ in report)
                p.test_report = [f"{state}|{text}" for state, text in report]
        await asyncio.to_thread(pacs_config.save, profiles)
        async with self:
            self.testing = ""
            self._reload_cards()
