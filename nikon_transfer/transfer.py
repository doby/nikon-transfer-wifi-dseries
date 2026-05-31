"""Photo transfer orchestration."""

import logging
import socket
import time
from pathlib import Path

from .client import PtpIpClient
from .protocol import PTPIP_PORT
from .utils import format_size

log = logging.getLogger("nikon_transfer.transfer")


def discover_camera(host: str) -> bool:
    """Return True if the camera answers on the PTP/IP port."""
    log.debug("Sonde %s:%d …", host, PTPIP_PORT)
    try:
        s = socket.create_connection((host, PTPIP_PORT), timeout=3)
        s.close()
        log.debug("Caméra détectée sur %s", host)
        return True
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        log.debug("Caméra non joignable : %s", e)
        return False


def transfer_photos(
    host: str,
    dest: Path,
    new_only: bool,
    dry_run: bool,
    extensions: set[str],
    timeout: float = 10.0,
) -> dict:
    """
    Connect to the D5300, list photos and download them.
    Returns a summary dict: { transferred, skipped, errors, bytes }.
    """
    dest.mkdir(parents=True, exist_ok=True)
    stats = {"transferred": 0, "skipped": 0, "errors": 0, "bytes": 0}

    log.info("Connexion à %s (timeout=%.1fs)", host, timeout)
    with PtpIpClient(host, timeout=timeout) as client:
        storage_ids = client.get_storage_ids()
        log.info("Stockages trouvés : %s", storage_ids)
        print(f"  Stockages trouvés : {storage_ids}")

        all_handles: list[int] = []
        for sid in storage_ids:
            handles = client.get_object_handles(sid)
            all_handles.extend(handles)
        log.info("%d objets sur la carte", len(all_handles))
        print(f"  Objets sur la carte : {len(all_handles)}")

        infos: list[dict] = []
        log.debug("Récupération des métadonnées …")
        print("  Récupération des métadonnées …")
        for h in all_handles:
            try:
                info = client.get_object_info(h)
                if Path(info["filename"]).suffix.lower() in extensions:
                    infos.append(info)
            except Exception as e:
                log.warning("handle %#010x : %s", h, e)
                print(f"    ⚠ handle {h:#010x} : {e}")
                stats["errors"] += 1

        log.info("%d images compatibles", len(infos))
        print(f"  Images compatibles : {len(infos)}")

        for info in infos:
            filename  = info["filename"]
            dest_path = dest / filename

            if new_only and dest_path.exists():
                log.debug("Ignoré (existe) : %s", filename)
                stats["skipped"] += 1
                continue

            if dry_run:
                log.info("[DRY-RUN] %s (%s)", filename, format_size(info["size"]))
                print(f"  [DRY-RUN] {filename} ({format_size(info['size'])})")
                stats["transferred"] += 1
                continue

            log.info("Téléchargement : %s (%s)", filename, format_size(info["size"]))
            print(f"  ↓ {filename}  ({format_size(info['size'])}) …", end=" ", flush=True)
            t0 = time.monotonic()
            try:
                data = client.get_object(info["handle"])
                dest_path.write_bytes(data)
                elapsed = time.monotonic() - t0
                speed   = len(data) / elapsed / 1024 if elapsed > 0 else 0
                log.info("✓ %s — %.0f Ko/s", filename, speed)
                print(f"✓  {speed:.0f} Ko/s")
                stats["transferred"] += 1
                stats["bytes"]       += len(data)
            except Exception as e:
                log.error("✗ %s : %s", filename, e)
                print(f"✗  {e}")
                stats["errors"] += 1

    return stats
