"""Background payment reminders that work while the app is closed.

Run ``budget-tracker-remind`` (or ``python -m budget_tracker.reminders``) to show a Windows toast notification
for bills that are overdue or due soon. Schedule it with Windows Task Scheduler to get daily reminders; see README.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
from collections.abc import Callable
from datetime import date
from xml.sax.saxutils import escape

from .config import APP_NAME, db_path
from .db import connect
from .formatting import format_money
from .models import DueBill
from .repository import Repository

log = logging.getLogger(__name__)

# PowerShell's AppUserModelID lets an unpackaged script raise toasts without registering its own app identity.
_TOAST_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
_TOAST_SCRIPT = """
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($env:BT_TOAST_XML)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:BT_TOAST_APP_ID).Show($toast)
"""


def reminder_message(due: list[DueBill]) -> tuple[str, str] | None:
    """Build a (title, body) notification for due bills, or None when nothing is due."""
    if not due:
        return None
    overdue = sum(1 for d in due if d.status == "overdue")
    title = f"{len(due)} payment{'s' if len(due) != 1 else ''} due soon"
    if overdue:
        title = f"{overdue} overdue, {len(due) - overdue} due soon" if len(due) > overdue else f"{overdue} payment(s) overdue"
    lines = [
        f"{d.bill.name}: {format_money(d.amount_cents) if d.amount_cents else 'amount TBD'} - {d.due_text.lower()}" for d in due[:4]
    ]
    if len(due) > 4:
        lines.append(f"+{len(due) - 4} more")
    return title, "\n".join(lines)


def show_windows_toast(title: str, body: str) -> None:
    """Display a Windows toast notification via PowerShell (no extra dependencies)."""
    xml = (
        "<toast><visual><binding template='ToastGeneric'>"
        f"<text>{escape(APP_NAME)}: {escape(title)}</text>"
        + "".join(f"<text>{escape(line)}</text>" for line in body.splitlines()[:2])
        + "</binding></visual></toast>"
    )
    env = {**os.environ, "BT_TOAST_XML": xml, "BT_TOAST_APP_ID": _TOAST_APP_ID}
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _TOAST_SCRIPT],
        env=env,
        check=True,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        timeout=30,
    )


def check_and_notify(
    repo: Repository,
    *,
    today: date | None = None,
    notifier: Callable[[str, str], None] = show_windows_toast,
) -> list[DueBill]:
    due = repo.due_bills(today or date.today(), repo.reminder_days())
    if message := reminder_message(due):
        notifier(*message)
    return due


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show a notification for bills that are overdue or due soon.")
    parser.add_argument("--print", action="store_true", help="print reminders instead of showing a notification")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    repo = Repository(connect(db_path()))
    try:
        window = repo.reminder_days()
        notifier = (lambda title, body: print(f"{title}\n{body}")) if args.print else show_windows_toast
        due = check_and_notify(repo, notifier=notifier)
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("Could not show notification: %s", exc)
        return 1
    finally:
        repo.close()
    if not due:
        log.info("No payments due in the next %d day(s)", window)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
