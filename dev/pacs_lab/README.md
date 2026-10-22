# PACS lab: fake PACS on this Mac, no Docker

Fake DICOM archives (PACS) for developing and testing the explorer's PACS
support. Plain Python (pynetdicom): two `qrscp` instances, a C-MOVE receiver,
and `mini_pacs.py`, a stricter fake that behaves more like a hospital PACS.
The data is seven public IDC series copied out of the explorer's own
downloads.

The scripts live here. Everything they write (venv, copied data, what the
PACS store, logs, retrieved files) goes to `$PACS_LAB_HOME`, by default
`~/.cache/dicom_data_explorer/pacs_lab`. It stays outside the repo because
Reflex's dev reloader watches every top-level folder of the repo, so files
written inside it would restart a running dev backend.

## Start and stop

```bash
dev/pacs_lab/scripts/quickstart.sh       # venv, data, configs, start, load, C-ECHO
dev/pacs_lab/scripts/servers.sh status
dev/pacs_lab/scripts/servers.sh stop
```

`quickstart.sh` needs `uv` (or `python3`) and the seven series listed in
`scripts/copy_data.sh` in the explorer's downloads (`$PACS_LAB_SOURCE`,
default `/Users/Shared/re-ddns/collections/dicom/by-app/dicom-data-explorer`,
only read). A series that is missing is skipped.

## The fake PACS

| Server | AE title | Port | What it is |
|---|---|---|---|
| a | PACS_A | 11112 | qrscp: KiTS-00061 CT + SEG, PTEN_409 PT + RTSTRUCT |
| b | PACS_B | 11113 | qrscp: NLST CT + SEG, the KiTS SEG again, a Label Map SEG |
| rx | EXPLORER | 11114 | storescp: where C-MOVE delivers (`$PACS_LAB_HOME/received/explorer`) |
| mini | MINI_PACS | 11115 | `mini_pacs.py`: checks the called AE title, real wildcards, ModalitiesInStudy, counts |

All of them listen on 127.0.0.1 only. Start the mini PACS with
`servers.sh start mini` and fill it with `load_data.sh mini`. Each fake
PACS knows these move destinations: EXPLORER (127.0.0.1:11114), DDE_EXPLORER
(127.0.0.1:11120, the explorer app's own receiver) and, for qrscp, OFFLINE
(127.0.0.1:11119, never listening, for the "receiver is down" case).

To use them from the app, add them on its PACS page as DIMSE cards: host
127.0.0.1, port 11112 / 11113 / 11115, PACS AE title PACS_A / PACS_B /
MINI_PACS, our AE title DDE_EXPLORER, retrieve C-GET or C-MOVE.

## Try it

```bash
LAB=$PWD/dev/pacs_lab                    # from the repo root
WORK=${PACS_LAB_HOME:-$HOME/.cache/dicom_data_explorer/pacs_lab}
cd "$WORK"
py() { "$WORK/.venv/bin/python" -I -B "$@"; }
KITS_STUDY=1.3.6.1.4.1.14519.5.2.1.6919.4624.198361641186777317394471940754
KITS_CT=1.3.6.1.4.1.14519.5.2.1.6919.4624.322088738018313179675994057197

py $LAB/scripts/pacs_client.py --port 11112 --aec PACS_A echo
py $LAB/scripts/pacs_client.py --port 11112 --aec PACS_A find --level STUDY --patient-id KiTS-00061
py $LAB/scripts/pacs_client.py --port 11112 --aec PACS_A find --level SERIES --study-uid $KITS_STUDY
py $LAB/scripts/pacs_client.py --port 11112 --aec PACS_A get --study-uid $KITS_STUDY --series-uid $KITS_CT --out received/get_kits_ct
py $LAB/scripts/verify.py --orig data/kits_ct --recv received/get_kits_ct
py $LAB/scripts/pacs_client.py --port 11112 --aec PACS_A move --study-uid $KITS_STUDY --series-uid $KITS_CT --dest EXPLORER
py $LAB/scripts/verify.py --orig data/kits_ct --recv received/explorer --series-uid $KITS_CT
py $LAB/scripts/negative_tests.py --port 11112 --aec PACS_A --study-uid $KITS_STUDY --series-uid $KITS_CT
py $LAB/scripts/multi_pacs.py --pacs PACS_A@127.0.0.1:11112 --pacs PACS_B@127.0.0.1:11113
py $LAB/scripts/dicomweb_demo.py         # read-only GETs against the public Orthanc demo
```

## What the lab showed (2026-10-22)

- C-GET: 29 files (15.3 MB) in 0.21 s, 162 files (85.3 MB) in 0.98 s.
  C-MOVE: 0.29 s and 1.33 s for the same series. SOPInstanceUIDs and pixel
  data match the originals; whole files differ because qrscp re-stores them
  in Implicit VR (private tags become UN).
- Status codes from `negative_tests.py`:
  - unknown move destination, and known but unreachable: both 0xA801
  - C-GET without storage contexts or without SCP role selection: 0xA702
  - C-GET offering only CT for a CT + SEG study: 0xB000 (1 failed)
  - SERIES query without StudyInstanceUID: 0xA900
  - C-GET of a series that does not exist: 0x0000 with 0 completed
- qrscp is lenient where hospital PACS are not: it accepts any called AE
  title, ignores ModalitiesInStudy and other optional keys, answers once per
  stored file, and matches wildcards through SQL LIKE (`_` matches anything).
  `mini_pacs.py` behaves correctly on all four.

## Gotchas

- qrscp needs sqlalchemy and absolute storage paths; `make_configs.sh`
  writes them.
- storescu's default contexts lack newer SOP classes such as the Label Map
  SEG; `load_data.sh` uses `-cx` for it.
- 0xA801 means "unknown destination" or "known but unreachable": read the
  PACS log to tell them apart.
- macOS bash 3.2: background servers need `exec` so that `$!` is the server
  itself; `servers.sh` does this.

Tested with macOS 15.3.1 (arm64), Python 3.11.14, pynetdicom 3.0.4,
pydicom 3.0.2, SQLAlchemy 2.1.5 and dicomweb-client 0.61.2.
