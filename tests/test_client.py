"""Unit tests for PtpIpClient (no real camera needed — sockets are mocked)."""

import struct
import unittest
from unittest.mock import MagicMock, patch

from nikon_transfer.protocol import (
    PKT_INIT_CMD_ACK, PKT_INIT_EVENT_ACK,
    PKT_CMD_RESPONSE, PKT_DATA_START, PKT_DATA_END,
    RSP_OK, RSP_INVALID_STORAGE_ID,
)
from nikon_transfer.client import PtpIpClient


def _make_packet(ptype: int, payload: bytes = b"") -> bytes:
    length = 8 + len(payload)
    return struct.pack("<II", length, ptype) + payload


def _data_start(total_len: int, tx_id: int = 0) -> bytes:
    # PTP/IP START_DATA_PACKET payload = TransactionID(4) + TotalDataLength(8)
    return _make_packet(PKT_DATA_START, struct.pack("<IQ", tx_id, total_len))


def _data_end(payload: bytes, tx_id: int = 0) -> bytes:
    # PTP/IP END_DATA_PACKET payload = TransactionID(4) + DataPayload
    return _make_packet(PKT_DATA_END, struct.pack("<I", tx_id) + payload)


def _response_packet(rsp_code: int = RSP_OK) -> bytes:
    return _make_packet(PKT_CMD_RESPONSE, struct.pack("<H", rsp_code))


def _packet_stream(*packets: bytes) -> list[bytes]:
    """Split complete packets into 8-byte header + rest, as separate recv() chunks."""
    chunks = []
    for pkt in packets:
        chunks.append(pkt[:8])
        if len(pkt) > 8:
            chunks.append(pkt[8:])
    return chunks


def _mock_sock(recv_chunks: list[bytes]) -> MagicMock:
    sock = MagicMock()
    sock.recv.side_effect = recv_chunks
    return sock


# ── Connection handshake ─────────────────────────────────────────────────────

class TestPtpIpClientConnect(unittest.TestCase):
    def _build_mocks(self):
        client   = PtpIpClient("192.168.1.1")
        cmd_sock = MagicMock()
        evt_sock = MagicMock()

        ack_payload = struct.pack("<I", 42)
        cmd_sock.recv.side_effect = _packet_stream(
            _make_packet(PKT_INIT_CMD_ACK, ack_payload),
            _response_packet(),                           # open session rsp
        )
        evt_sock.recv.side_effect = _packet_stream(
            _make_packet(PKT_INIT_EVENT_ACK, struct.pack("<I", 42)),
        )
        return client, cmd_sock, evt_sock

    def test_connect_opens_session(self):
        client, cmd_sock, evt_sock = self._build_mocks()
        with patch("socket.create_connection", side_effect=[cmd_sock, evt_sock]):
            client.connect()
        # INIT_CMD_REQUEST + INIT_EVENT_REQUEST + OP_OPEN_SESSION = 3 sendalls
        self.assertGreaterEqual(
            cmd_sock.sendall.call_count + evt_sock.sendall.call_count, 3
        )

    def test_disconnect_closes_socket(self):
        client, cmd_sock, evt_sock = self._build_mocks()
        with patch("socket.create_connection", side_effect=[cmd_sock, evt_sock]):
            client.connect()
        cmd_sock.recv.side_effect = _packet_stream(_response_packet())
        client.disconnect()
        cmd_sock.close.assert_called_once()
        evt_sock.close.assert_called_once()


# ── get_storage_ids ──────────────────────────────────────────────────────────

class TestGetStorageIds(unittest.TestCase):
    def test_parses_two_storages(self):
        client = PtpIpClient("192.168.1.1")
        ids_payload = struct.pack("<III", 2, 0x00010001, 0x00020001)

        client.cmd_sock = _mock_sock(_packet_stream(
            _data_start(len(ids_payload)),
            _data_end(ids_payload),
            _response_packet(),
        ))
        self.assertEqual(client.get_storage_ids(), [0x00010001, 0x00020001])

    def test_empty_storage_list(self):
        client = PtpIpClient("192.168.1.1")
        empty_payload = struct.pack("<I", 0)

        client.cmd_sock = _mock_sock(_packet_stream(
            _data_start(len(empty_payload)),
            _data_end(empty_payload),
            _response_packet(),
        ))
        self.assertEqual(client.get_storage_ids(), [])


# ── _recv_data robustness ────────────────────────────────────────────────────

class TestRecvDataFallback(unittest.TestCase):
    """Camera sends CMD_RESPONSE instead of DATA_START."""

    def test_rsp_ok_no_data_returns_empty(self):
        client = PtpIpClient("192.168.1.1")
        client.cmd_sock = _mock_sock(_packet_stream(_response_packet(RSP_OK)))
        self.assertEqual(client._recv_data(), b"")

    def test_error_response_raises(self):
        client = PtpIpClient("192.168.1.1")
        client.cmd_sock = _mock_sock(_packet_stream(
            _response_packet(RSP_INVALID_STORAGE_ID)
        ))
        with self.assertRaises(IOError) as ctx:
            client._recv_data()
        self.assertIn("InvalidStorageID", str(ctx.exception))


# ── get_object_handles ───────────────────────────────────────────────────────

class TestGetObjectHandles(unittest.TestCase):
    def test_returns_handles(self):
        client = PtpIpClient("192.168.1.1")
        payload = struct.pack("<III", 2, 0x0001, 0x0002)

        client.cmd_sock = _mock_sock(_packet_stream(
            _data_start(len(payload)),
            _data_end(payload),
            _response_packet(),
        ))
        self.assertEqual(client.get_object_handles(0x00010001), [0x0001, 0x0002])

    def test_empty_handles_on_rsp_ok_no_data(self):
        client = PtpIpClient("192.168.1.1")
        client.cmd_sock = _mock_sock(_packet_stream(_response_packet(RSP_OK)))
        self.assertEqual(client.get_object_handles(0x00010001), [])


def _ptp_str(text: str) -> bytes:
    if not text:
        return b"\x00"
    encoded = (text + "\x00").encode("utf-16-le")
    return bytes([len(text) + 1]) + encoded


def _ptp_u16_array(values: list[int]) -> bytes:
    return struct.pack(f"<I{len(values)}H", len(values), *values)


# ── get_device_info ──────────────────────────────────────────────────────────

class TestGetDeviceInfo(unittest.TestCase):
    def test_parses_full_structure(self):
        payload = (
            struct.pack("<HIH", 100, 0x0000000A, 1)   # std=1.00, Nikon vendor, v1
            + _ptp_str("")                            # vendor desc
            + struct.pack("<H", 0)                    # functional mode
            + _ptp_u16_array([0x1001, 0x1002, 0x1009])
            + _ptp_u16_array([])                      # events
            + _ptp_u16_array([0x5001, 0x5011])        # device props
            + _ptp_u16_array([])                      # capture formats
            + _ptp_u16_array([0x3000, 0x3801])        # image formats
            + _ptp_str("Nikon Corporation")
            + _ptp_str("D5300")
            + _ptp_str("V1.03")
            + _ptp_str("2123456")
        )
        client = PtpIpClient("192.168.1.1")
        client.cmd_sock = _mock_sock(_packet_stream(
            _data_start(len(payload)),
            _data_end(payload),
            _response_packet(),
        ))
        info = client.get_device_info()
        self.assertEqual(info["manufacturer"], "Nikon Corporation")
        self.assertEqual(info["model"], "D5300")
        self.assertEqual(info["device_version"], "V1.03")
        self.assertEqual(info["serial_number"], "2123456")
        self.assertEqual(info["vendor_extension"], 0x0000000A)
        self.assertIn(0x5001, info["properties"])
        self.assertIn(0x5011, info["properties"])


# ── get_device_datetime ──────────────────────────────────────────────────────

class TestGetDeviceDateTime(unittest.TestCase):
    def test_parses_iso_string(self):
        import datetime as _dt
        payload = _ptp_str("20260530T191500")
        client = PtpIpClient("192.168.1.1")
        client.cmd_sock = _mock_sock(_packet_stream(
            _data_start(len(payload)),
            _data_end(payload),
            _response_packet(),
        ))
        self.assertEqual(
            client.get_device_datetime(),
            _dt.datetime(2026, 5, 30, 19, 15, 0),
        )

    def test_returns_none_when_unsupported(self):
        client = PtpIpClient("192.168.1.1")
        client.cmd_sock = _mock_sock(_packet_stream(
            _response_packet(0x200A),   # DevicePropNotSupported
        ))
        self.assertIsNone(client.get_device_datetime())


# ── get_battery_level ────────────────────────────────────────────────────────

class TestGetBatteryLevel(unittest.TestCase):
    def test_returns_percentage(self):
        client = PtpIpClient("192.168.1.1")
        # BatteryLevel is UINT8 — one byte: 76 % charge.
        payload = bytes([76])
        client.cmd_sock = _mock_sock(_packet_stream(
            _data_start(len(payload)),
            _data_end(payload),
            _response_packet(),
        ))
        self.assertEqual(client.get_battery_level(), 76)

    def test_returns_none_when_unsupported(self):
        """Camera firmware may answer DevicePropNotSupported."""
        client = PtpIpClient("192.168.1.1")
        client.cmd_sock = _mock_sock(_packet_stream(
            _response_packet(0x200A),   # DevicePropNotSupported
        ))
        self.assertIsNone(client.get_battery_level())


if __name__ == "__main__":
    unittest.main()
