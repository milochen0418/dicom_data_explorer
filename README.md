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
lists those series from the IDC index, downloads them with the usual queue
(they appear under Downloads too) and, once every one of them is in, hands
back to the viewer, which opens them with the annotation on top.

| Intent | Params |
|--------|--------|
| `dicom.fetch-series` | `series`: comma-separated SeriesInstanceUIDs (required); `title`: how the caller names the annotation |

The result is `{"series": [...], "downloaded": [...]}`.

The same works from this side. Pressing **Download** on an annotation
series (SEG, RTSTRUCT, SR, PR, ...) opens a dialog instead of just queueing
it: the app reads the annotation's files from IDC to see which image series
they refer to, lists them ticked, and one button downloads the annotation
together with its images. When the references cannot be read, the image
series of the same study are offered instead, unticked. The Downloads page
keeps a **Get images** button on every annotation downloaded earlier.

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
