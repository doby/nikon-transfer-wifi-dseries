import io
import socket
import struct
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from nikon_transfer.utils import recv_exactly, read_ptp_string, format_size, md5_file


class TestRecvExactly(unittest.TestCase):
    def _make_sock(self, *chunks: bytes):
        sock = MagicMock(spec=socket.socket)
        sock.recv.side_effect = list(chunks)
        return sock

    def test_single_recv(self):
        sock = self._make_sock(b"hello")
        self.assertEqual(recv_exactly(sock, 5), b"hello")

    def test_fragmented_recv(self):
        sock = self._make_sock(b"he", b"ll", b"o")
        self.assertEqual(recv_exactly(sock, 5), b"hello")

    def test_eof_raises(self):
        sock = self._make_sock(b"hi", b"")
        with self.assertRaises(EOFError):
            recv_exactly(sock, 10)


class TestReadPtpString(unittest.TestCase):
    def _make(self, text: str) -> bytes:
        encoded = (text + "\x00").encode("utf-16-le")
        return bytes([len(text) + 1]) + encoded

    def test_normal_string(self):
        data = self._make("DSC_0001.NEF")
        s, offset = read_ptp_string(data, 0)
        self.assertEqual(s, "DSC_0001.NEF")
        self.assertEqual(offset, len(data))

    def test_empty_string(self):
        data = b"\x00extra"
        s, offset = read_ptp_string(data, 0)
        self.assertEqual(s, "")
        self.assertEqual(offset, 1)

    def test_offset_respected(self):
        padding = b"\xff\xff\xff"
        data = padding + self._make("IMG.JPG")
        s, _ = read_ptp_string(data, 3)
        self.assertEqual(s, "IMG.JPG")


class TestFormatSize(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(format_size(512), "512.0 o")

    def test_kilobytes(self):
        self.assertEqual(format_size(2048), "2.0 Ko")

    def test_megabytes(self):
        self.assertEqual(format_size(5 * 1024 * 1024), "5.0 Mo")

    def test_gigabytes(self):
        self.assertEqual(format_size(2 * 1024 ** 3), "2.0 Go")


class TestMd5File(unittest.TestCase):
    def test_known_hash(self, tmp_path=None):
        import tempfile, hashlib
        content = b"nikon"
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(content)
            name = Path(f.name)
        try:
            expected = hashlib.md5(content).hexdigest()
            self.assertEqual(md5_file(name), expected)
        finally:
            name.unlink()


if __name__ == "__main__":
    unittest.main()
