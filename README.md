# DICOM data explorer

## App Overview

This app helps you explore public DICOM datasets, inspect series metadata, and download image files to your local machine.

### Landing Page
The welcome screen introduces the workflow and guides you to the search pages.

![Landing Page](docs/images/landing-page.png)

### IDC Search
Search IDC collections by filters, browse results, and add series to the download cart.

![IDC Search](docs/images/idc-search.png)

### Series Metadata
Inspect detailed series metadata before downloading.

![Series Metadata](docs/images/series-metadata.png)

### Download Flow
Review your cart and start downloads with progress feedback.

![Start Downloading](docs/images/start-downloading.png)

### Fetching the images an annotation needs

Other apps can open this app's `dicom.fetch-series` page as a dialog (a DDNS
Intent). DICOM Viewer does so when a downloaded annotation object (SEG,
RTSTRUCT, SR, PR) refers to series that are not downloaded yet: the dialog
looks those series up in the IDC index, then on the PACS set up on the PACS
page, downloads them with the usual queue (they appear under Downloads too)
and, once every one of them is in, hands back to the viewer, which opens
them with the annotation on top.

| Intent | Params |
|--------|--------|
| `dicom.fetch-series` | `series`: comma-separated SeriesInstanceUIDs (required); `title`: how the caller names the annotation; `study`: their StudyInstanceUID (optional, but a DIMSE PACS cannot find a series without it) |

The result is `{"series": [...], "downloaded": [...]}`.

The same works from this side. Pressing **Download** on an annotation
series (SEG, RTSTRUCT, SR, PR, ...) opens a dialog instead of just queueing
it: the app reads the annotation's files from IDC to see which image series
they refer to, lists them ticked, and one button downloads the annotation
together with its images. When the references cannot be read, the image
series of the same study are offered instead, unticked. On the Downloads
page an annotation's row shows whether its images are there, follows their
download live (waiting, percentage, done), and offers **Get images** when
they are missing.

### Hospital PACS

Besides IDC, the app can search and download from hospital archives (PACS).
Add them on the **PACS** page:

- **DICOMweb** (HTTP): the address, e.g. `http://pacs.example:8042/dicom-web`
  (QIDO-RS to search, WADO-RS to download), and a login: none, user name and
  password, or a token.
- **DIMSE** (the classic DICOM network protocol): host, port, the PACS's AE
  title, our AE title (`DDE_EXPLORER` by default) and how to retrieve:
  - **C-GET**: the images come back on our own connection. Nothing to
    register on the PACS, but not every PACS allows it.
  - **C-MOVE**: the PACS sends the images to this app's receiver. Its
    administrator registers our AE title, IP and port (`DDE_EXPLORER`, the
    host's IP, `11120`) as a destination.

**Test connection** tries each step (C-ECHO, C-FIND, C-GET and C-MOVE, or the
DICOMweb search) and says what to change when one fails.

On **Find series**, choose where to search: IDC, one PACS, or **All PACS**. A
PACS only answers what it is asked, so give a patient ID, a name, an
accession number or a date range. All PACS asks them in parallel and merges
the studies and series by UID; a series found on several PACS downloads from
the first of them in the PACS page's list.

Downloads share one queue with a lane per source, so a slow PACS never holds
up IDC or another PACS. DICOMweb fetches at most 2 files at a time; C-GET and
C-MOVE fetch a whole series in one request. Each job is journaled in the data
folder: after a restart it comes back as interrupted, and Resume fetches only
the missing files.

The PACS cards (with their passwords) stay in the app's data folder, readable
by its owner only; they never reach the shared DICOM folder or the browser.
A downloaded series' record in the shared folder holds what an IDC series'
record holds (modality, description, image count) and the PACS it came
from, never patient names or IDs.

| Setting | Environment variable | Default |
|---------|----------------------|---------|
| Data folder (PACS cards, job journal) | `DICOM_EXPLORER_DATA_DIR` | `~/.local/share/dicom_data_explorer` |
| Receiver AE title | `DICOM_RECEIVER_AET` | `DDE_EXPLORER` |
| Receiver port | `DICOM_RECEIVER_PORT` | `11120` |
| Receiver bind address | `DICOM_RECEIVER_BIND` | `0.0.0.0` |
| IP shown for C-MOVE registration | `EXTERNAL_IP` | set by re_ddns |

In the re_ddns App Store, the catalog entry maps port 11120 and keeps the
data folder in a volume.

To practise without a hospital, [dev/pacs_lab](dev/pacs_lab/README.md) runs
fake PACS on this computer and shows how to add them as cards.

## Design Document

We’ve added a software design document to share the architecture and roadmap. Please reference it and join the open-source development efforts.

[docs/DICOM Data Explorer — Software Design Document (Intro).pdf](docs/DICOM%20Data%20Explorer%20(Intro)%20%E2%80%94%20Software%20Design%20Document.pdf)

## Getting Started

This project is managed with [Poetry](https://python-poetry.org/).

### Prerequisites

- Python 3.11.x
- Poetry

### Installation

1. Ensure Poetry uses Python 3.11:

```bash
poetry env use python3.11
poetry env info
```

2. Install dependencies:

```bash
poetry install
```

### Running the App

Start the development server:

```bash
poetry run ./reflex_rerun.sh
```

The application will be available at `http://localhost:3000`.
