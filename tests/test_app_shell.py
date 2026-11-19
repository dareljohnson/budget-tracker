import asyncio
import tomllib
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import flet as ft

from budget_tracker.main import _pinned_page, apply_preset


def test_app_version_matches_pyproject():
    from budget_tracker import __version__

    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as handle:
        project_version = tomllib.load(handle)["project"]["version"]
    assert __version__ == project_version


def test_pinned_page_pins_the_header_and_scrolls_the_body():
    header = ft.Text("Header")
    body = ft.Text("Body")
    pinned = _pinned_page(ft.Column([header, body], spacing=12))

    assert isinstance(pinned, ft.Column)
    assert pinned.controls[0] is header
    body_column = pinned.controls[1]
    assert isinstance(body_column, ft.Column)
    assert body_column.controls == [body]
    assert body_column.scroll == ft.ScrollMode.AUTO
    assert body_column.expand is True


def test_pinned_page_leaves_a_non_column_control_alone():
    control = ft.Text("Plain")
    assert _pinned_page(control) is control


def test_apply_preset_persists_the_choice_and_notifies_every_view():
    page = SimpleNamespace(theme=None, dark_theme=None)
    repo = mock.Mock()
    ctx = mock.Mock()

    apply_preset(page, repo, ctx, "ocean")

    repo.set_setting.assert_called_once_with("theme_preset", "ocean")
    ctx.data_changed.assert_called_once_with()
    assert isinstance(page.theme, ft.Theme)
    assert isinstance(page.dark_theme, ft.Theme)


def test_nav_indices_stay_in_sync_with_the_labels():
    from budget_tracker import main as shell

    assert shell.NAV_ITEMS[1][0] == "Year"
    assert shell.NAV_ITEMS[shell.REPORTS_INDEX][0] == "Reports"
    assert shell.NAV_ITEMS[shell.TRANSACTIONS_INDEX][0] == "Transactions"
    assert shell.NAV_ITEMS[shell.BILLS_INDEX][0] == "Bills"


def test_payment_alert_task_sends_at_startup_and_refreshes_on_a_new_day():
    from budget_tracker import main as shell

    ctx = mock.Mock()
    days = iter([date(2026, 3, 10), date(2026, 3, 10), date(2026, 3, 11)])
    sleep = mock.AsyncMock(side_effect=[None, None, asyncio.CancelledError()])
    send = mock.Mock(return_value=False)

    with mock.patch.object(shell.asyncio, "to_thread", new=mock.AsyncMock(side_effect=lambda fn: fn())):
        try:
            asyncio.run(
                shell.payment_alert_task(
                    ctx,
                    today=lambda: next(days),
                    now=lambda: datetime(2026, 3, 10, 9, 0),
                    sleep=sleep,
                    send_due=send,
                )
            )
        except asyncio.CancelledError:
            pass

    # One text check at startup and one after the date changes.
    assert send.call_count == 2
    ctx.data_changed.assert_called_once_with()


def test_payment_alert_task_keeps_ticking_when_a_text_fails():
    from budget_tracker import main as shell

    ctx = mock.Mock()
    sleep = mock.AsyncMock(side_effect=asyncio.CancelledError())
    send = mock.Mock(side_effect=shell.SmsError("offline"))

    with mock.patch.object(shell.asyncio, "to_thread", new=mock.AsyncMock(side_effect=lambda fn: fn())), \
            mock.patch.object(shell.log, "warning") as warned:
        try:
            asyncio.run(
                shell.payment_alert_task(
                    ctx,
                    today=lambda: date(2026, 3, 10),
                    now=lambda: datetime(2026, 3, 10, 9, 0),
                    sleep=sleep,
                    send_due=send,
                )
            )
        except asyncio.CancelledError:
            pass

    send.assert_called_once()
    warned.assert_called_once()


def test_payment_alert_task_does_not_text_before_the_morning_check():
    from budget_tracker import main as shell

    ctx = mock.Mock()
    sleep = mock.AsyncMock(side_effect=asyncio.CancelledError())
    send = mock.Mock(return_value=True)

    with mock.patch.object(shell.asyncio, "to_thread", new=mock.AsyncMock(side_effect=lambda fn: fn())):
        try:
            asyncio.run(
                shell.payment_alert_task(
                    ctx,
                    today=lambda: date(2026, 3, 10),
                    now=lambda: datetime(2026, 3, 10, 7, 0),
                    sleep=sleep,
                    send_due=send,
                )
            )
        except asyncio.CancelledError:
            pass

    send.assert_not_called()
    ctx.data_changed.assert_not_called()


def test_payment_alert_task_waits_for_the_morning_check_before_texting():
    from budget_tracker import main as shell

    ctx = mock.Mock()
    clock = SimpleNamespace(hour=7)
    send = mock.Mock(return_value=True)

    async def sleep(_):
        if clock.hour < 8:
            clock.hour = 8
            return
        raise asyncio.CancelledError()

    with mock.patch.object(shell.asyncio, "to_thread", new=mock.AsyncMock(side_effect=lambda fn: fn())):
        try:
            asyncio.run(
                shell.payment_alert_task(
                    ctx,
                    today=lambda: date(2026, 3, 10),
                    now=lambda: datetime(2026, 3, 10, clock.hour, 0),
                    sleep=sleep,
                    send_due=send,
                )
            )
        except asyncio.CancelledError:
            pass

    send.assert_called_once()
    ctx.data_changed.assert_not_called()
