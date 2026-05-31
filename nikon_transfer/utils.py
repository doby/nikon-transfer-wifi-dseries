"""Low-level network and formatting helpers."""

import hashlib
import socket
import struct
from pathlib import Path


def recv_exactly(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("Connexion fermée prématurément")
        buf += chunk
    return buf


def read_ptp_string(data: bytes, offset: int) -> tuple[str, int]:
    """Parse a PTP string: 1-byte length + UTF-16LE chars."""
    length = data[offset]
    offset += 1
    if length == 0:
        return "", offset
    chars = data[offset: offset + length * 2]
    offset += length * 2
    return chars.decode("utf-16-le").rstrip("\x00"), offset


def _read_uint16_array(data: bytes, offset: int) -> tuple[list[int], int]:
    """PTP array: UINT32 count + count × UINT16 elements."""
    count = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    if count == 0:
        return [], offset
    values = list(struct.unpack_from(f"<{count}H", data, offset))
    offset += count * 2
    return values, offset


def md5_file(path: Path) -> str:
    # usedforsecurity=False : empreinte utilisée pour la déduplication de
    # fichiers, pas pour de la signature — MD5 reste adapté et plus rapide
    # que SHA-256. Le flag fait taire les SAST (CWE-327) à juste titre.
    h = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def format_size(n: int) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} To"


def format_storage_size(n: int) -> str:
    """Human-friendly storage size in SI (1000-based) units, matching how SD
    cards are labelled (a '32 Go' card holds 32 × 10⁹ bytes, not 32 × 2³⁰).
    Drops trailing decimals once the value reaches double digits ('14 Go'
    instead of '14,0 Go') and uses the French decimal comma."""
    value = float(n)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if value < 1000:
            if unit == "o":
                return f"{int(value)} {unit}"
            if value >= 10:
                return f"{value:.0f} {unit}"
            return f"{value:.1f} {unit}".replace(".", ",")
        value /= 1000
    return f"{value:.0f} Po"
