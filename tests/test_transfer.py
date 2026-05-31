"""Tests for transfer orchestration (camera mocked)."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nikon_transfer.transfer import discover_camera, transfer_photos


class TestDiscoverCamera(unittest.TestCase):
    def test_reachable(self):
        with patch("socket.create_connection") as mock_conn:
            mock_conn.return_value = MagicMock()
            self.assertTrue(discover_camera("192.168.1.1"))

    def test_unreachable(self):
        import socket
        with patch("socket.create_connection", side_effect=socket.timeout):
            self.assertFalse(discover_camera("192.168.1.1"))


class TestTransferPhotos(unittest.TestCase):
    def _make_client(self, objects: list[dict]) -> MagicMock:
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__  = MagicMock(return_value=False)
        client.get_storage_ids.return_value    = [0x00010001]
        client.get_object_handles.return_value = [o["handle"] for o in objects]
        client.get_object_info.side_effect     = lambda h: next(o for o in objects if o["handle"] == h)
        client.get_object.side_effect          = lambda h: next(o for o in objects if o["handle"] == h)["data"]
        return client

    def _run(self, objects, dest, new_only=False, dry_run=False, extensions=None):
        if extensions is None:
            extensions = {".nef", ".jpg"}
        mock_client = self._make_client(objects)
        with patch("nikon_transfer.transfer.PtpIpClient", return_value=mock_client):
            return transfer_photos(
                host="192.168.1.1",
                dest=dest,
                new_only=new_only,
                dry_run=dry_run,
                extensions=extensions,
            )

    def test_transfers_new_files(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d)
            objects = [
                {"handle": 1, "filename": "DSC_0001.NEF", "size": 5, "format": 0x3800, "data": b"NIKON"},
                {"handle": 2, "filename": "DSC_0002.NEF", "size": 3, "format": 0x3800, "data": b"CAM"},
            ]
            stats = self._run(objects, dest)
            self.assertEqual(stats["transferred"], 2)
            self.assertEqual(stats["errors"], 0)
            self.assertEqual((dest / "DSC_0001.NEF").read_bytes(), b"NIKON")

    def test_skips_existing_with_new_only(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d)
            (dest / "DSC_0001.NEF").write_bytes(b"already here")
            objects = [
                {"handle": 1, "filename": "DSC_0001.NEF", "size": 12, "format": 0x3800, "data": b"already here"},
                {"handle": 2, "filename": "DSC_0002.NEF", "size": 3,  "format": 0x3800, "data": b"CAM"},
            ]
            stats = self._run(objects, dest, new_only=True)
            self.assertEqual(stats["skipped"],     1)
            self.assertEqual(stats["transferred"], 1)

    def test_dry_run_does_not_write(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d)
            objects = [
                {"handle": 1, "filename": "DSC_0001.JPG", "size": 4, "format": 0x3801, "data": b"JPEG"},
            ]
            stats = self._run(objects, dest, dry_run=True)
            self.assertEqual(stats["transferred"], 1)
            self.assertFalse((dest / "DSC_0001.JPG").exists())

    def test_extension_filter(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d)
            objects = [
                {"handle": 1, "filename": "DSC_0001.NEF", "size": 5, "format": 0x3800, "data": b"NIKON"},
                {"handle": 2, "filename": "DSC_0002.MOV", "size": 3, "format": 0x300D, "data": b"VID"},
            ]
            stats = self._run(objects, dest, extensions={".nef"})
            self.assertEqual(stats["transferred"], 1)
            self.assertFalse((dest / "DSC_0002.MOV").exists())


if __name__ == "__main__":
    unittest.main()
