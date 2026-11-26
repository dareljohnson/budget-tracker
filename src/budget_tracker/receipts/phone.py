"""Receive a receipt photo from a phone on the same network.

The desktop app starts a tiny HTTP server bound to the LAN, shows a QR code, and waits for
one JPEG or PNG upload. The token is part of the URL, uploads are size-limited, and the
server is stopped as soon as the photo arrives or the dialog closes.
"""

from __future__ import annotations

import base64
import html
import io
import queue
import secrets
import socket
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


class PhoneReceiptError(Exception):
    """A phone upload could not be set up or accepted."""


def lan_ip() -> str | None:
    """The computer's LAN IPv4 address, or ``None`` when there is no usable network."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            # A UDP connect does not send a packet; it only selects the default route.
            sock.connect(("192.0.2.1", 80))
            address = sock.getsockname()[0]
    except OSError:
        try:
            address = socket.gethostbyname(socket.gethostname())
        except OSError:
            return None
    if not address or address.startswith("127.") or address.startswith("169.254."):
        return None
    return address


def qr_data_uri(url: str) -> str | None:
    """A PNG data URI for ``url``, or ``None`` when QR generation is unavailable."""
    try:
        import qrcode

        image = qrcode.make(url)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
    except Exception:  # noqa: BLE001 - QR is a convenience; the URL is always shown
        return None
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _upload_page(token: str) -> str:
    safe_token = html.escape(token, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scan a receipt</title>
<style>
  body {{ margin: 0; font-family: system-ui, -apple-system, Segoe UI, sans-serif;
         background: #f6f3fb; color: #1c1b1f; }}
  main {{ max-width: 520px; margin: 0 auto; padding: 28px 20px 40px; }}
  h1 {{ font-size: 1.35rem; margin: 0 0 8px; }}
  p {{ line-height: 1.45; color: #49454f; }}
  label {{ display: block; margin: 24px 0 12px; padding: 18px; text-align: center;
           border-radius: 14px; background: #4a5bb5; color: white; font-weight: 600;
           font-size: 1.05rem; cursor: pointer; }}
  input {{ position: absolute; width: 1px; height: 1px; opacity: 0; }}
  #status {{ min-height: 48px; margin-top: 18px; padding: 12px; border-radius: 10px;
             background: #ece7f2; color: #49454f; white-space: pre-wrap; }}
</style>
</head>
<body>
<main>
  <h1>Scan a receipt</h1>
  <p>Take a photo of the receipt, or choose one from your photos. Stay on the same Wi-Fi
     as the computer running Budget Tracker.</p>
  <label for="photo">Take or choose a photo</label>
  <input id="photo" type="file" accept="image/*" capture="environment">
  <div id="status">Waiting for a photo...</div>
</main>
<script>
const TOKEN = {safe_token!r};
const input = document.getElementById('photo');
const status = document.getElementById('status');

async function asJpeg(file) {{
  if (!window.createImageBitmap) return file;
  try {{
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, 2200 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext('2d');
    if (!context) return file;
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.9));
    return blob || file;
  }} catch (error) {{
    return file;
  }}
}}

input.addEventListener('change', async () => {{
  const file = input.files && input.files[0];
  if (!file) return;
  status.textContent = 'Preparing and uploading…';
  try {{
    const upload = await asJpeg(file);
    const response = await fetch('/upload?token=' + encodeURIComponent(TOKEN), {{
      method: 'POST',
      headers: {{'Content-Type': upload.type || 'application/octet-stream'}},
      body: upload
    }});
    const text = await response.text();
    if (response.ok) {{
      status.textContent = 'Receipt received. You can close this page.';
      input.disabled = true;
    }} else {{
      status.textContent = text || 'The photo could not be uploaded.';
    }}
  }} catch (error) {{
    status.textContent = 'Upload failed: ' + error;
  }}
}});
</script>
</body>
</html>"""


class PhoneReceiptServer:
    """A one-photo HTTP receiver for the phone hand-off."""

    def __init__(
        self,
        destination_dir: Path,
        *,
        host: str = "0.0.0.0",
        port: int = 0,
        public_ip: str | None = None,
        max_bytes: int = MAX_UPLOAD_BYTES,
    ) -> None:
        self.destination_dir = Path(destination_dir)
        self.destination_dir.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(18)
        self.max_bytes = max_bytes
        self._queue: queue.Queue[Path] = queue.Queue()
        self._httpd = ThreadingHTTPServer((host, port), _handler_for(self))
        self._httpd.daemon_threads = True
        self.port = int(self._httpd.server_address[1])
        self.public_ip = public_ip
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str | None:
        ip = self.public_ip or lan_ip()
        if ip is None:
            return None
        return f"http://{ip}:{self.port}/{self.token}"

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="phone-receipt-server", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        try:
            self._httpd.shutdown()
        except Exception:  # noqa: BLE001 - shutdown is best effort
            pass
        try:
            self._httpd.server_close()
        except Exception:  # noqa: BLE001
            pass
        self._thread = None

    def wait(self, timeout: float) -> Path | None:
        """The uploaded file path, or ``None`` when nothing arrived in ``timeout`` seconds."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _page(self) -> str:
        return _upload_page(self.token)

    def _accept_upload(self, body: bytes) -> Path:
        if body.startswith(_PNG_MAGIC):
            extension = ".png"
        elif body.startswith(_JPEG_MAGIC):
            extension = ".jpg"
        else:
            raise PhoneReceiptError("Use a JPEG or PNG photo of the receipt.")
        target = self.destination_dir / f"phone-{uuid4().hex}{extension}"
        target.write_bytes(body)
        return target


def _handler_for(app: PhoneReceiptServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "BudgetTrackerPhone/1"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def _send(self, status: HTTPStatus, body: str, *, content_type: str = "text/html; charset=utf-8") -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            path = self.path.split("?", 1)[0]
            if path == f"/{app.token}":
                self._send(HTTPStatus.OK, app._page())
            else:
                self._send(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:  # noqa: N802
            from urllib.parse import parse_qs, urlparse

            parsed = urlparse(self.path)
            token = parse_qs(parsed.query).get("token", [""])[0]
            if parsed.path != "/upload" or token != app.token:
                self._send(HTTPStatus.FORBIDDEN, "This upload link is not valid.")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0:
                self._send(HTTPStatus.BAD_REQUEST, "No photo was received.")
                return
            if length > app.max_bytes:
                self._send(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    "That photo is too large. Try a smaller one.",
                )
                return
            body = self.rfile.read(length)
            try:
                path = app._accept_upload(body)
            except PhoneReceiptError as exc:
                self._send(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, str(exc))
                return
            app._queue.put(path)
            self._send(HTTPStatus.OK, "Receipt received. You can close this page.")

    return Handler
