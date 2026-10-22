"""The PACS this app can search and download from, kept in one JSON file.

Each entry is a "PACS card": where the PACS is and how to talk to it. The file
lives in the app's own data folder ($DICOM_EXPLORER_DATA_DIR, by default
~/.local/share/dicom_data_explorer), never in the shared collection: it holds
passwords and tokens, so it is written readable by its owner only.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import tempfile
from pathlib import Path

DICOMWEB = "dicomweb"
DIMSE = "dimse"
KINDS = (DICOMWEB, DIMSE)
AUTHS = ("none", "basic", "bearer")
RETRIEVES = ("C-GET", "C-MOVE")

# Where C-MOVE delivers: this app's receiver. The PACS administrator registers
# its AE title, this host's IP and the port on the PACS.
RECEIVER_AET = os.environ.get("DICOM_RECEIVER_AET", "DDE_EXPLORER")
RECEIVER_PORT = int(os.environ.get("DICOM_RECEIVER_PORT", "11120"))
RECEIVER_BIND = os.environ.get("DICOM_RECEIVER_BIND", "0.0.0.0")


def data_dir() -> Path:
    return Path(
        os.environ.get("DICOM_EXPLORER_DATA_DIR")
        or Path.home() / ".local" / "share" / "dicom_data_explorer"
    )


def host_ip() -> str:
    """The IP to give a PACS administrator: the Docker host's LAN IP when re_ddns runs the app."""
    return os.environ.get("EXTERNAL_IP", "")


@dataclasses.dataclass
class PacsProfile:
    id: str
    label: str
    kind: str = DICOMWEB
    # DICOMweb
    url: str = ""  # QIDO-RS root, e.g. http://host:8042/dicom-web
    wado_url: str = ""  # WADO-RS root, when it differs from url
    auth: str = "none"
    username: str = ""
    password: str = ""
    token: str = ""
    verify_tls: bool = True
    # DIMSE
    host: str = ""
    port: int = 104
    called_aet: str = ""  # the PACS's AE title
    calling_aet: str = RECEIVER_AET  # ours, as the PACS knows it
    retrieve: str = "C-GET"
    # Both
    max_results: int = 200
    # What the last "Test connection" found
    tested_at: str = ""
    test_ok: bool = False
    test_report: list[str] = dataclasses.field(default_factory=list)

    @property
    def address(self) -> str:
        if self.kind == DICOMWEB:
            return self.url
        return f"{self.called_aet}@{self.host}:{self.port}"


def _file() -> Path:
    return data_dir() / "pacs.json"


def load() -> list[PacsProfile]:
    try:
        raw = json.loads(_file().read_text())
    except FileNotFoundError:
        return []
    fields = {f.name for f in dataclasses.fields(PacsProfile)}
    return [PacsProfile(**{k: v for k, v in entry.items() if k in fields}) for entry in raw.get("pacs", [])]


def save(profiles: list[PacsProfile]) -> None:
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"pacs": [dataclasses.asdict(p) for p in profiles]}, indent=2)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".pacs.", suffix=".json")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(body)
        os.replace(tmp, _file())
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def new_id(label: str, taken: set[str]) -> str:
    """A stable id from the label: lower case, digits and dashes, never "idc"."""
    base = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "pacs"
    candidate, n = base, 2
    while candidate in taken or candidate == "idc":
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def problem(p: PacsProfile) -> str:
    """What is missing or wrong in a card, for the form; "" when it can be saved."""
    if not p.label.strip():
        return "Give the PACS a name."
    if p.kind not in KINDS:
        return "Choose DICOMweb or DIMSE."
    if p.kind == DICOMWEB:
        if not re.match(r"^https?://", p.url.strip()):
            return "The DICOMweb address must start with http:// or https://."
        if p.wado_url and not re.match(r"^https?://", p.wado_url.strip()):
            return "The WADO-RS address must start with http:// or https://."
        if p.auth not in AUTHS:
            return "Choose how to log in."
        return ""
    if not p.host.strip():
        return "Enter the PACS's host name or IP address."
    if not 0 < int(p.port) < 65536:
        return "The port must be between 1 and 65535."
    for name, aet in (("The PACS's", p.called_aet), ("Our", p.calling_aet)):
        if not aet.strip():
            return f"{name} AE title is missing."
        if len(aet.strip()) > 16 or "\\" in aet:
            return f"{name} AE title must be at most 16 characters, without a backslash."
    if p.retrieve not in RETRIEVES:
        return "Choose C-GET or C-MOVE."
    return ""
