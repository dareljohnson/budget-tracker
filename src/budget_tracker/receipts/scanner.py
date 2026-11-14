"""Scan one page from the first scanner attached to this Windows computer.

Windows Image Acquisition (WIA) is driven through a short PowerShell script, so scanning
needs no extra Python dependency. The script connects to the first scanner device and
transfers one page to the destination path we pass in. WIA drivers do not always honour
the requested format (some return BMP regardless), so the result is normalised to PNG
with Pillow before it is returned.
"""

import os
import shutil
import subprocess
from pathlib import Path

from PIL import Image

from budget_tracker.receipts.errors import ReceiptScanError

IS_WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_UNAVAILABLE = "Scanning needs Windows with PowerShell. Choose a photo or scan file instead."
_NO_IMAGE = "The scanner did not return an image."


def _powershell() -> str | None:
    """Return the PowerShell executable, preferring Windows PowerShell over pwsh."""
    return shutil.which("powershell") or shutil.which("pwsh")


def scanner_available() -> bool:
    """Say whether a scan can be attempted here without prompting or starting a process."""
    return IS_WINDOWS and _powershell() is not None


def _scan_script() -> str:
    """Return the WIA PowerShell script that scans one page to ``$Destination``."""
    return """param([Parameter(Mandatory=$true)][string]$Destination)
$ErrorActionPreference = 'Stop'
$manager = New-Object -ComObject WIA.DeviceManager
$device = $null
foreach ($info in $manager.DeviceInfos) {
    if ($info.Type -eq 1) { $device = $info.Connect(); break }
}
if ($null -eq $device) { throw 'No scanner is connected.' }
$item = $device.Items.Item(1)
$image = $item.Transfer('{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}')
if (Test-Path $Destination) { Remove-Item $Destination -Force }
$image.SaveFile($Destination)
"""


def _last_stderr_line(stderr: str | None) -> str:
    """Return the last non-blank stderr line with PowerShell's decoration stripped."""
    if stderr:
        lines = [line.strip() for line in stderr.splitlines() if line.strip()]
        if lines:
            line = lines[-1]
            if line.startswith("+ "):
                line = line[2:].strip()
            for prefix in ("FullyQualifiedErrorId : ", "CategoryInfo : "):
                if line.startswith(prefix):
                    line = line[len(prefix):].strip()
                    break
            return line
    return ""


def scan_receipt(destination: Path, *, timeout: float = 60) -> Path:
    """Scan one page with the first attached Windows scanner and save it as ``destination``.

    The PowerShell process runs hidden and non-interactively, so no console window flashes
    up while the user waits. A missing scanner, a timeout, a failed script or an empty
    output file raises :class:`ReceiptScanError`; the temporary script is always removed.
    """
    if not scanner_available():
        raise ReceiptScanError(_UNAVAILABLE)
    powershell = _powershell()
    destination = Path(destination)
    script_path = destination.with_suffix(".ps1")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(_scan_script(), encoding="utf-8")
        try:
            result = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script_path),
                    str(destination),
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise ReceiptScanError(_last_stderr_line(exc.stderr) or _NO_IMAGE) from exc
    finally:
        script_path.unlink(missing_ok=True)

    if result.returncode != 0:
        raise ReceiptScanError(_last_stderr_line(result.stderr) or _NO_IMAGE)
    if not destination.is_file() or destination.stat().st_size == 0:
        raise ReceiptScanError(_last_stderr_line(result.stderr) or _NO_IMAGE)
    _normalize_image(destination)
    return destination


def _normalize_image(destination: Path) -> None:
    """Rewrite a scanner's output as PNG when its driver ignored the requested format.

    WIA drivers commonly return BMP even when a PNG format GUID is requested. The file is
    read into memory and closed before rewriting it, because Windows will not let the
    image be saved over a file that is still open.
    """
    try:
        with Image.open(destination) as image:
            image.load()
            converted = None if image.format == "PNG" else image.convert("RGB")
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise ReceiptScanError("The scanner returned an image that could not be read.") from exc
    if converted is None:
        return
    try:
        converted.save(destination, format="PNG")
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise ReceiptScanError("The scanned image could not be prepared for review.") from exc
    finally:
        converted.close()
