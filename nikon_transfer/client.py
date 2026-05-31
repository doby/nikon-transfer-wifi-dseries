"""Minimal PTP/IP client for Nikon D5300 (no external dependencies)."""

import logging
import select
import socket
import struct
import uuid
from datetime import datetime

from .protocol import (
    CLIENT_NAME, PTPIP_PORT,
    PKT_INIT_CMD_REQUEST, PKT_INIT_CMD_ACK,
    PKT_INIT_EVENT_REQUEST, PKT_INIT_EVENT_ACK,
    PKT_CMD_REQUEST, PKT_CMD_RESPONSE,
    PKT_EVENT, PKT_DATA_START, PKT_DATA_END,
    OP_OPEN_SESSION, OP_CLOSE_SESSION,
    OP_GET_STORAGE_IDS, OP_GET_STORAGE_INFO, OP_GET_OBJECT_HANDLES,
    OP_GET_OBJECT_INFO, OP_GET_OBJECT, OP_GET_THUMB, OP_GET_PARTIAL_OBJECT,
    OP_GET_DEVICE_PROP_VALUE, OP_GET_DEVICE_INFO,
    DPC_BATTERY_LEVEL, DPC_DATETIME,
    RSP_OK, rsp_name,
)
from .utils import recv_exactly, read_ptp_string, _read_uint16_array

log = logging.getLogger("nikon_transfer.client")

_PKT_NAMES = {
    0x0001: "INIT_CMD_REQUEST",
    0x0002: "INIT_CMD_ACK",
    0x0003: "INIT_EVENT_REQUEST",
    0x0004: "INIT_EVENT_ACK",
    0x0006: "CMD_REQUEST",
    0x0007: "CMD_RESPONSE",
    0x0008: "EVENT",
    0x0009: "DATA_START",
    0x000C: "DATA_END",
}


class PtpIpClient:
    """PTP/IP client exposing the operations needed for photo transfer."""

    def __init__(
        self,
        host: str,
        port: int = PTPIP_PORT,
        timeout: float = 10.0,
    ):
        self.host    = host
        self.port    = port
        self.timeout = timeout
        self.cmd_sock: socket.socket | None = None
        self.evt_sock: socket.socket | None = None
        self._session_id     = 1
        self._transaction_id = 1
        # Fresh GUID each instance so the camera doesn't mistake us for a stale session.
        self._guid = uuid.uuid4().bytes

    # ── Connection lifecycle ─────────────────────────────────────────────────

    def connect(self) -> None:
        log.info("Connexion PTP/IP à %s:%s …", self.host, self.port)

        self.cmd_sock = socket.create_connection((self.host, self.port), self.timeout)
        self.cmd_sock.settimeout(self.timeout)
        log.debug("TCP connecté, guid=%s", self._guid.hex())

        name_enc = (CLIENT_NAME + "\x00").encode("utf-16-le")
        # INIT_CMD_REQUEST: GUID(16) + FriendlyName(UTF-16LE) + ProtocolVersion(uint16=1)
        payload = self._guid + name_enc + struct.pack("<H", 1)
        self._send_packet(self.cmd_sock, PKT_INIT_CMD_REQUEST, payload)

        try:
            ptype, data = self._recv_packet(self.cmd_sock)
        except TimeoutError:
            raise ConnectionError(
                "La caméra n'a pas répondu à INIT_CMD_REQUEST.\n"
                "  → Éteignez complètement la caméra, rallumez-la,\n"
                "    puis réactivez le Wi-Fi (Menu → Setup → Wi-Fi → Enable)."
            ) from None

        if ptype != PKT_INIT_CMD_ACK:
            raise ConnectionError(f"INIT_CMD_ACK attendu, reçu 0x{ptype:04X}")
        conn_num = struct.unpack_from("<I", data, 0)[0]
        log.debug("INIT_CMD_ACK reçu, conn_num=%#010x", conn_num)

        # PTP/IP requires the event channel before any PTP operation:
        # the camera stays silent on OpenSession until INIT_EVENT_ACK is received.
        self.evt_sock = socket.create_connection((self.host, self.port), self.timeout)
        self.evt_sock.settimeout(self.timeout)
        self._send_packet(self.evt_sock, PKT_INIT_EVENT_REQUEST, struct.pack("<I", conn_num))
        try:
            ptype, _ = self._recv_packet(self.evt_sock)
        except TimeoutError:
            raise ConnectionError(
                "La caméra n'a pas répondu à INIT_EVENT_REQUEST."
            ) from None
        if ptype != PKT_INIT_EVENT_ACK:
            raise ConnectionError(f"INIT_EVENT_ACK attendu, reçu 0x{ptype:04X}")

        log.info("Canaux CMD + EVENT établis ✓")
        self._open_session()

    def disconnect(self) -> None:
        try:
            self._close_session()
        except Exception as e:
            log.debug("close_session ignoré : %s", e)
        for name in ("evt_sock", "cmd_sock"):
            sock = getattr(self, name)
            if sock is None:
                continue
            try:
                sock.close()
                log.debug("%s fermé", name)
            except Exception:
                pass
            setattr(self, name, None)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.disconnect()

    # ── Session ──────────────────────────────────────────────────────────────

    def _open_session(self) -> None:
        log.debug("OpenSession (session_id=%d)", self._session_id)
        self._send_operation(OP_OPEN_SESSION, [self._session_id])
        rsp = self._recv_response()
        log.debug("OpenSession rsp=%s", rsp_name(rsp))

    def _close_session(self) -> None:
        log.debug("CloseSession")
        self._send_operation(OP_CLOSE_SESSION)
        self._recv_response()

    # ── PTP operations ───────────────────────────────────────────────────────

    def get_storage_ids(self) -> list[int]:
        log.debug("GetStorageIDs")
        self._send_operation(OP_GET_STORAGE_IDS)
        data = self._recv_data()
        if not data:
            log.debug("GetStorageIDs → []")
            return []
        count = struct.unpack_from("<I", data, 0)[0]
        ids = list(struct.unpack_from(f"<{count}I", data, 4))
        log.debug("GetStorageIDs → %s", [f"{i:#010x}" for i in ids])
        return ids

    def get_storage_info(self, storage_id: int) -> dict:
        """PTP StorageInfo: returns free bytes / images and volume label."""
        log.debug("GetStorageInfo storage=%#010x", storage_id)
        self._send_operation(OP_GET_STORAGE_INFO, [storage_id])
        data = self._recv_data()
        if len(data) < 26:
            return {"free_bytes": 0, "free_objects": 0, "volume_label": ""}
        # Layout: StorageType(2) + FilesystemType(2) + AccessCapability(2)
        #       + MaxCapacity(8) + FreeSpaceInBytes(8) + FreeSpaceInImages(4)
        #       + StorageDescription(string) + VolumeLabel(string)
        free_bytes   = struct.unpack_from("<Q", data, 14)[0]
        free_objects = struct.unpack_from("<I", data, 22)[0]
        offset = 26
        _desc,        offset = read_ptp_string(data, offset)
        volume_label, offset = read_ptp_string(data, offset)
        info = {
            "free_bytes":   free_bytes,
            "free_objects": free_objects,
            "volume_label": volume_label,
        }
        log.debug("GetStorageInfo → %s", info)
        return info

    def get_object_handles(self, storage_id: int) -> list[int]:
        log.debug("GetObjectHandles storage=%#010x", storage_id)
        # Parent handle 0 = all objects regardless of parent (PTP spec §10.4.6).
        self._send_operation(OP_GET_OBJECT_HANDLES, [storage_id, 0, 0])
        data = self._recv_data()
        if not data:
            log.debug("GetObjectHandles → 0 handles")
            return []
        count = struct.unpack_from("<I", data, 0)[0]
        log.debug("GetObjectHandles → %d handles", count)
        return list(struct.unpack_from(f"<{count}I", data, 4))

    def get_object_info(self, handle: int) -> dict:
        log.debug("GetObjectInfo handle=%#010x", handle)
        self._send_operation(OP_GET_OBJECT_INFO, [handle])
        data = self._recv_data()
        file_size = struct.unpack_from("<I", data, 8)[0]
        offset = 52  # fixed PTP ObjectInfo header size
        filename,     offset = read_ptp_string(data, offset)
        capture_str,  offset = read_ptp_string(data, offset)
        capture_dt = None
        if capture_str:
            # PTP CaptureDate is "YYYYMMDDTHHMMSS" (optionally ".s").
            try:
                capture_dt = datetime.strptime(capture_str[:15], "%Y%m%dT%H%M%S")
            except ValueError:
                pass
        info = {
            "handle":       handle,
            "filename":     filename,
            "size":         file_size,
            "format":       struct.unpack_from("<H", data, 4)[0],
            "capture_date": capture_dt,
        }
        log.debug("GetObjectInfo → %s (%d octets) date=%s", filename, file_size, capture_dt)
        return info

    def get_object(self, handle: int) -> bytes:
        log.debug("GetObject handle=%#010x", handle)
        self._send_operation(OP_GET_OBJECT, [handle])
        data = self._recv_data()
        log.debug("GetObject → %d octets reçus", len(data))
        return data

    def get_thumb(self, handle: int) -> bytes:
        log.debug("GetThumb handle=%#010x", handle)
        self._send_operation(OP_GET_THUMB, [handle])
        data = self._recv_data()
        log.debug("GetThumb → %d octets reçus", len(data))
        return data

    def get_partial_object(self, handle: int, offset: int, max_bytes: int) -> bytes:
        log.debug("GetPartialObject handle=%#010x offset=%d max=%d", handle, offset, max_bytes)
        self._send_operation(OP_GET_PARTIAL_OBJECT, [handle, offset, max_bytes])
        data = self._recv_data()
        log.debug("GetPartialObject → %d octets reçus", len(data))
        return data

    def get_device_info(self) -> dict:
        """PTP DeviceInfo (§13.2): manufacturer, model, firmware, serial, and
        the lists of supported operations / events / properties / formats."""
        log.debug("GetDeviceInfo")
        self._send_operation(OP_GET_DEVICE_INFO)
        data = self._recv_data()
        # Fixed header: StandardVersion(2) + VendorExtID(4) + VendorExtVer(2)
        std_version  = struct.unpack_from("<H", data, 0)[0]
        vendor_ext   = struct.unpack_from("<I", data, 2)[0]
        vendor_ver   = struct.unpack_from("<H", data, 6)[0]
        offset = 8
        vendor_desc, offset = read_ptp_string(data, offset)
        # FunctionalMode (2) then five UINT16 arrays.
        functional_mode = struct.unpack_from("<H", data, offset)[0]
        offset += 2
        operations,  offset = _read_uint16_array(data, offset)
        events,      offset = _read_uint16_array(data, offset)
        properties,  offset = _read_uint16_array(data, offset)
        capture_fmt, offset = _read_uint16_array(data, offset)
        image_fmt,   offset = _read_uint16_array(data, offset)
        manufacturer,   offset = read_ptp_string(data, offset)
        model,          offset = read_ptp_string(data, offset)
        device_version, offset = read_ptp_string(data, offset)
        serial,         offset = read_ptp_string(data, offset)
        info = {
            "standard_version":  std_version,
            "vendor_extension":  vendor_ext,
            "vendor_version":    vendor_ver,
            "vendor_description": vendor_desc,
            "functional_mode":   functional_mode,
            "operations":        operations,
            "events":            events,
            "properties":        properties,
            "capture_formats":   capture_fmt,
            "image_formats":     image_fmt,
            "manufacturer":      manufacturer,
            "model":             model,
            "device_version":    device_version,
            "serial_number":     serial,
        }
        log.info("DeviceInfo : %s %s firmware=%s série=%s",
                 manufacturer, model, device_version, serial)
        return info

    def get_device_datetime(self) -> datetime | None:
        """Read the camera's internal clock (PTP DPC DateTime, STR format)."""
        log.debug("GetDevicePropValue DateTime")
        self._send_operation(OP_GET_DEVICE_PROP_VALUE, [DPC_DATETIME])
        try:
            data = self._recv_data()
        except IOError as e:
            log.debug("DateTime non supporté : %s", e)
            return None
        if not data:
            return None
        text, _ = read_ptp_string(data, 0)
        # PTP §13.4.10 — "YYYYMMDDThhmmss" (optional ".s" / "Z" / "±hhmm").
        try:
            dt = datetime.strptime(text[:15], "%Y%m%dT%H%M%S")
        except ValueError:
            log.debug("DateTime mal formé : %r", text)
            return None
        log.debug("DateTime caméra → %s", dt)
        return dt

    def get_battery_level(self) -> int | None:
        """Battery charge in %, or None if the camera doesn't expose it.
        PTP §13.3.1 : BatteryLevel (0x5001) is a UINT8 value 0–100."""
        log.debug("GetDevicePropValue BatteryLevel")
        self._send_operation(OP_GET_DEVICE_PROP_VALUE, [DPC_BATTERY_LEVEL])
        try:
            data = self._recv_data()
        except IOError as e:
            # DevicePropNotSupported / OperationNotSupported → no battery data.
            log.debug("BatteryLevel non supporté : %s", e)
            return None
        if not data:
            return None
        level = data[0]
        log.debug("BatteryLevel → %d%%", level)
        return level

    # ── Event channel ────────────────────────────────────────────────────────

    def poll_event(self, timeout: float = 0.0) -> tuple[int, list[int]] | None:
        """Read one event from evt_sock, or None if nothing arrived within `timeout`.

        Uses select() to avoid blocking when no event is pending — important when
        the cmd channel may be busy and we just want to check for ObjectAdded.
        Once select reports readable data, reads a full packet (may briefly block
        on tail bytes if the packet arrived split, but that's bounded by self.timeout).
        """
        if self.evt_sock is None:
            return None
        ready, _, _ = select.select([self.evt_sock], [], [], timeout)
        if not ready:
            return None
        ptype, payload = self._recv_packet(self.evt_sock)
        if ptype != PKT_EVENT:
            log.debug("Event channel: paquet 0x%04X ignoré", ptype)
            return None
        # PTP/IP Event payload: EventCode(uint16) + TxID(uint32) + up to 3 params(uint32)
        if len(payload) < 6:
            log.debug("Event tronqué (%d octets)", len(payload))
            return None
        code = struct.unpack_from("<H", payload, 0)[0]
        params: list[int] = []
        offset = 6
        while offset + 4 <= len(payload):
            params.append(struct.unpack_from("<I", payload, offset)[0])
            offset += 4
        log.debug("Event reçu: %#06X params=%s", code, params)
        return code, params

    # ── Network primitives ───────────────────────────────────────────────────

    def _send_packet(self, sock: socket.socket, ptype: int, payload: bytes = b"") -> None:
        length = 8 + len(payload)
        raw = struct.pack("<II", length, ptype) + payload
        label = _PKT_NAMES.get(ptype, f"0x{ptype:04X}")
        log.debug("SEND %-20s %3dB  %s", label, length, raw.hex())
        sock.sendall(raw)

    def _recv_packet(self, sock: socket.socket) -> tuple[int, bytes]:
        header = recv_exactly(sock, 8)
        length, ptype = struct.unpack_from("<II", header)
        payload = recv_exactly(sock, length - 8) if length > 8 else b""
        label = _PKT_NAMES.get(ptype, f"0x{ptype:04X}")
        log.debug("RECV %-20s %3dB  %s", label, length, (header + payload).hex())
        return ptype, payload

    def _send_operation(self, opcode: int, params: list[int] | None = None) -> None:
        # PTP/IP CMD_REQUEST: DataPhaseInfo(uint32) + OpCode(uint16) + TxID(uint32) + params
        params  = params or []
        payload = struct.pack("<IHI", 1, opcode, self._transaction_id)
        payload += struct.pack(f"<{len(params)}I", *params)
        self._send_packet(self.cmd_sock, PKT_CMD_REQUEST, payload)
        self._transaction_id += 1

    def _recv_response(self) -> int:
        ptype, data = self._recv_packet(self.cmd_sock)
        if ptype != PKT_CMD_RESPONSE:
            raise IOError(f"CMD_RESPONSE attendu, reçu 0x{ptype:04X} data={data.hex()}")
        code = struct.unpack_from("<H", data, 0)[0]
        log.debug("CMD_RESPONSE code=%s", rsp_name(code))
        return code

    def _recv_data(self) -> bytes:
        # DATA_START, intermediate DATA, and DATA_END all come on cmd_sock.
        # Some cameras send CMD_RESPONSE directly (error or empty-data success).
        ptype, payload = self._recv_packet(self.cmd_sock)

        if ptype == PKT_CMD_RESPONSE:
            code = struct.unpack_from("<H", payload, 0)[0] if len(payload) >= 2 else 0
            if code != RSP_OK:
                log.error("Erreur PTP : %s", rsp_name(code))
                raise IOError(f"Erreur PTP : {rsp_name(code)}")
            log.debug("Réponse sans données (RSP_OK)")
            return b""

        if ptype != PKT_DATA_START:
            raise IOError(f"DATA_START attendu, reçu 0x{ptype:04X}")
        # DATA_START payload is TransactionID(4) + TotalDataLength(8); discarded.

        chunks = []
        while True:
            ptype, chunk = self._recv_packet(self.cmd_sock)
            # PTP/IP DATA/DATA_END packets prefix the actual data with the
            # 4-byte TransactionID. Strip it so the caller sees only the PTP payload.
            chunks.append(chunk[4:])
            if ptype == PKT_DATA_END:
                break
        self._recv_response()
        result = b"".join(chunks)
        log.debug("Données reçues : %d octets", len(result))
        return result
