"""Phone-to-desktop receipt uploads."""

import base64
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from budget_tracker.receipts.phone import PhoneReceiptServer, qr_data_uri

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class TestPhoneReceiptServer(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.destination = Path(self._temp.name)
        self.server = PhoneReceiptServer(
            self.destination,
            host="127.0.0.1",
            public_ip="127.0.0.1",
            max_bytes=1024,
        )
        self.server.start()
        self.addCleanup(self.server.stop)
        self.base = f"http://127.0.0.1:{self.server.port}"

    def test_upload_page_contains_the_phone_form(self):
        with urllib.request.urlopen(f"{self.base}/{self.server.token}", timeout=5) as response:
            page = response.read().decode("utf-8")
        self.assertIn("Scan a receipt", page)
        self.assertIn("Take or choose a photo", page)
        self.assertIn(self.server.token, page)

    def test_wrong_page_token_is_not_served(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(f"{self.base}/not-the-token", timeout=5)
        self.assertEqual(raised.exception.code, 404)

    def test_png_upload_is_saved_and_queued(self):
        request = urllib.request.Request(
            f"{self.base}/upload?token={self.server.token}",
            data=PNG,
            method="POST",
            headers={"Content-Type": "image/png"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read().decode("utf-8")
        self.assertIn("Receipt received", body)
        uploaded = self.server.wait(2)
        self.assertIsNotNone(uploaded)
        self.assertEqual(uploaded.read_bytes(), PNG)

    def test_wrong_token_is_rejected(self):
        request = urllib.request.Request(
            f"{self.base}/upload?token=wrong",
            data=PNG,
            method="POST",
            headers={"Content-Type": "image/png"},
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(raised.exception.code, 403)

    def test_non_image_body_is_rejected(self):
        request = urllib.request.Request(
            f"{self.base}/upload?token={self.server.token}",
            data=b"not an image",
            method="POST",
            headers={"Content-Type": "text/plain"},
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(raised.exception.code, 415)

    def test_oversized_upload_is_rejected(self):
        request = urllib.request.Request(
            f"{self.base}/upload?token={self.server.token}",
            data=b"x" * 2048,
            method="POST",
            headers={"Content-Type": "image/png"},
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(raised.exception.code, 413)

    def test_qr_data_uri_is_a_png(self):
        uri = qr_data_uri(f"{self.base}/{self.server.token}")
        self.assertIsNotNone(uri)
        self.assertTrue(uri.startswith("data:image/png;base64,"))
