# nikon-transfer

> 🇫🇷 [Lire en français](README.fr.md)

Wi-Fi photo transfer from a **Nikon D5300** to your Mac (or any computer) via the
PTP/IP protocol. No USB cable, no proprietary app (Nikon WMU is abandoned and broken
on recent macOS), no `libgphoto2`. The core is **pure Python stdlib**; the optional
GUI adds PySide6 + Pillow.

[![tests](https://github.com/doby/nikon-transfer-wifi-dseries/actions/workflows/tests.yml/badge.svg)](https://github.com/doby/nikon-transfer-wifi-dseries/actions/workflows/tests.yml)
[![sast](https://github.com/doby/nikon-transfer-wifi-dseries/actions/workflows/sast.yml/badge.svg)](https://github.com/doby/nikon-transfer-wifi-dseries/actions/workflows/sast.yml)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)]()
[![license](https://img.shields.io/badge/license-MIT-lightgrey)]()

## ⚠️ Disclaimer

This software is provided "as is", without warranty of any kind. It talks to
the camera through a reverse-engineered PTP/IP protocol that is not covered
by Nikon's warranty. The author accepts no responsibility for malfunctions,
data loss, or hardware damage resulting from its use. See [LICENSE](LICENSE).

## Screenshot

> _GUI screenshot to be added — see `docs/screenshot.png`._

## Features

- **CLI** for scripted transfers (`--new-only`, `--dry-run`, extension filter…).
- **GUI** with thumbnail grid, sortable columns (name / capture date / size),
  on-demand EXIF panel, full-resolution preview on double-click.
- **Auto-sync**: new photos shot while the GUI is running appear in the grid
  within ~3 s — no manual reload.
- **No third-party runtime deps** for the CLI/core (stdlib only).

## Requirements

| Step   | Action                                                                 |
|--------|------------------------------------------------------------------------|
| Camera | Menu → Setup ⚙ → Wi-Fi → **Enable**                                    |
| Mac    | Connect to the camera's Wi-Fi network (`Nikon_XXXXXXXX`)               |
| Python | 3.11 or later                                                          |

Default camera IP is `192.168.1.1`, PTP/IP port `15740`.

Tested on **Apple Silicon MacBook (M1)** running macOS. Should also work on
Intel Mac, Linux and Windows (pure Python core), but not verified.

## Installation

```bash
pip install .             # CLI only
pip install ".[gui]"      # CLI + GUI
pip install ".[progress]" # adds tqdm progress bar to the CLI
```

## Usage

### CLI

```
nikon-transfer [options]

Options:
  --host HOST       Camera IP address (default: 192.168.1.1)
  --dest PATH       Download folder (default: ~/Pictures/Nikon_D5300)
  --new-only        Skip files already present in the destination
  --dry-run         List files without downloading
  --ext EXT [EXT …] File extensions to transfer (default: .jpg .nef .tif .png …)
  --debug           Log every PTP/IP packet as hex (verbose)
```

Examples:

```bash
nikon-transfer                                            # transfer everything
nikon-transfer --dest ~/Desktop/Shoot --new-only --ext .nef
nikon-transfer --dry-run                                  # list, don't download
nikon-transfer --host 192.168.0.10                        # custom camera IP
```

### GUI

```bash
nikon-transfer-gui
```

Click **Connecter**, wait for the thumbnails to load, tick the photos you want,
then **Télécharger la sélection**. Shoot a new picture while the GUI is connected —
it shows up in the grid automatically.

## How it works

The tool speaks **PTP/IP** (CIPA DC-X 005-2005) directly over TCP `15740`. Two
TCP connections are established (command + event) and a small subset of PTP
operations is used: `OpenSession`, `GetStorageIDs`, `GetObjectHandles`,
`GetObjectInfo`, `GetObject`, `GetThumb`, `GetPartialObject`.

## PTP/IP gotchas (learned the hard way on the D5300)

These are not obvious from the spec — see `CLAUDE.md` for the full context.

1. **Two TCP channels are mandatory before any PTP operation.** The camera stays
   silent on `OpenSession` until the event channel handshake completes.
2. **DATA / DATA_END packets are prefixed with a 4-byte TransactionID** before
   the actual PTP payload. Forgetting to strip it makes `GetStorageIDs` return a
   spurious leading ID.
3. **`parent=0`** in `GetObjectHandles` means "all objects regardless of parent"
   (not `0xFFFFFFFF`).
4. **Fresh GUID per instance** (`uuid.uuid4().bytes`) — reusing one makes the
   camera mistake the connection for an abandoned session and refuse it.
5. **The D5300 doesn't push PTP/IP events.** The event channel is required for
   the handshake but the camera closes it immediately after. New-photo
   detection therefore polls `GetObjectHandles` every ~3 s rather than waiting
   on the event channel.

## Development

```bash
pip install -e ".[gui,dev]"
pytest             # 25 tests, sockets fully mocked — no camera required
pytest --cov
```

## Limitations

- Throughput is **~1–2 MB/s**, capped by the D5300's 802.11g Wi-Fi (not Python).
- **No native RAW (NEF) preview** in the GUI's full-resolution view (Qt can't
  decode NEF — would need `rawpy` or similar).
- **No recovery on mid-transfer network errors** — reconnect manually.
- Tested on **D5300 only**. PRs welcome for other Nikon bodies — see
  [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

[MIT](LICENSE)
