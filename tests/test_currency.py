"""JMD/USD conversion: rate fetching, caching, rounding and the converter dialog."""

from __future__ import annotations

import asyncio
import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import flet as ft

from budget_tracker import currency
from budget_tracker.db import connect
from budget_tracker.repository import Repository
from budget_tracker.services import BudgetService
from budget_tracker.ui.context import AppContext


class _PageStub:
    def __init__(self) -> None:
        self.overlay: list = []
        self.run_task = mock.Mock(return_value=mock.Mock())

    def show_dialog(self, control) -> None:
        if hasattr(control, "open"):
            control.open = True
        self.overlay.append(control)

    def pop_dialog(self) -> None:
        for control in reversed(self.overlay):
            if isinstance(control, ft.AlertDialog):
                control.open = False
                self.overlay.remove(control)
                return control
        return None

    def update(self, *_) -> None:
        pass

    @property
    def snackbars(self) -> list:
        return [control for control in self.overlay if isinstance(control, ft.SnackBar)]


def _response(payload: dict) -> io.BytesIO:
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


class TestCurrencyCore(unittest.TestCase):
    def test_conversion_rounds_to_usd_cents(self):
        # 10,000.00 JMD at 158.38 JMD/USD = 63.139... USD.
        self.assertEqual(currency.to_usd_cents(1_000_000, 158.38), 6_314)
        self.assertEqual(currency.format_jmd(1_000_000), "J$10,000.00")

    def test_conversion_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            currency.to_usd_cents(1_000, 0)
        with self.assertRaises(ValueError):
            currency.to_usd_cents(-1, 158.0)

    def test_parse_rate_accepts_commas_and_rejects_garbage(self):
        self.assertEqual(currency.parse_rate("158.38"), 158.38)
        self.assertEqual(currency.parse_rate("1,58.38"), 158.38)
        for value in ("", "abc", "0", "-5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                currency.parse_rate(value)

    def test_fetch_reads_the_jmd_rate_and_update_time(self):
        payload = {
            "result": "success",
            "rates": {"USD": 1, "JMD": 158.379827},
            "time_last_update_unix": 1_700_000_000,
        }
        with mock.patch.object(currency.urllib.request, "urlopen", return_value=_response(payload)) as urlopen:
            result = currency.fetch_jmd_per_usd()

        self.assertAlmostEqual(result.rate, 158.379827)
        self.assertEqual(result.source, "exchangerate-api.com")
        self.assertEqual(result.fetched_at.tzinfo, timezone.utc)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, currency.RATE_URL)
        self.assertTrue(request.get_header("User-agent", "").startswith("BudgetTracker/"))

    def test_fetch_turns_failures_into_rate_errors(self):
        cases = [
            mock.Mock(side_effect=OSError("offline")),
            mock.Mock(return_value=_response({"result": "success", "rates": {}})),
            mock.Mock(return_value=_response({"result": "error"})),
            mock.Mock(return_value=_response({"result": "success", "rates": {"JMD": "NaN"}})),
        ]
        for patcher in cases:
            with self.subTest(patcher=patcher), mock.patch.object(
                currency.urllib.request, "urlopen", patcher
            ), self.assertRaises(currency.RateError):
                currency.fetch_jmd_per_usd()


class TestCurrencyCache(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))

    def tearDown(self) -> None:
        self.repo.close()

    def _rate(self, value: float = 158.0, source: str = "exchangerate-api.com", age: timedelta = timedelta(0)):
        return currency.ExchangeRate(value, datetime.now(timezone.utc) - age, source)

    def test_rate_round_trip(self):
        saved = self._rate(158.3798)
        currency.save_rate(self.repo, saved)

        loaded = currency.load_cached_rate(self.repo)

        self.assertIsNotNone(loaded)
        self.assertAlmostEqual(loaded.rate, 158.3798)
        self.assertEqual(loaded.source, "exchangerate-api.com")

    def test_cache_freshness(self):
        self.assertTrue(currency.is_fresh(self._rate(age=timedelta(hours=1))))
        self.assertFalse(currency.is_fresh(self._rate(age=timedelta(hours=13))))
        self.assertFalse(currency.is_fresh(self._rate(source="manual")))

    def test_invalid_saved_values_are_ignored(self):
        self.repo.set_setting(currency.RATE_SETTING, "0")
        self.repo.set_setting(currency.RATE_AT_SETTING, "not-a-date")
        self.assertIsNone(currency.load_cached_rate(self.repo))


class TestCurrencyDialog(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))
        self.page = _PageStub()
        self.ctx = AppContext(page=self.page, repo=self.repo, service=BudgetService(self.repo))
        self.addCleanup(self.repo.close)

    def _dialog(self, **kwargs):
        from budget_tracker.ui.views.currency import CurrencyConverterDialog

        return CurrencyConverterDialog(self.ctx, **kwargs)

    def test_manual_rate_and_jmd_amount_produce_usd(self):
        dialog = self._dialog()
        dialog.rate.value = "158.38"
        dialog.jmd.value = "10000"

        dialog._recalculate()

        self.assertEqual(dialog.usd.value, "$63.14")
        self.assertIn("J$10,000.00", dialog.detail.value)
        self.assertFalse(dialog.copy_button.disabled)
        self.assertFalse(dialog.use_button.disabled)

    def test_invalid_rate_disables_the_actions(self):
        dialog = self._dialog()
        dialog.rate.value = "abc"
        dialog.jmd.value = "10000"

        dialog._recalculate()

        self.assertEqual(dialog.usd.value, "$0.00")
        self.assertIn("valid JMD-per-USD rate", dialog.detail.value)
        self.assertTrue(dialog.copy_button.disabled)
        self.assertTrue(dialog.use_button.disabled)

    def test_use_hands_usd_cents_to_the_editor(self):
        used: list[int] = []
        dialog = self._dialog(on_use=used.append)
        dialog.open()
        dialog.rate.value = "158.38"
        dialog.jmd.value = "10000"
        dialog._recalculate()

        dialog._use(None)

        self.assertEqual(used, [6_314])
        self.assertTrue(any("Converted" in bar.content.value for bar in self.page.snackbars))

    def test_refresh_replaces_the_rate_and_saves_it(self):
        dialog = self._dialog()
        fetched = currency.ExchangeRate(160.0, datetime.now(timezone.utc), "exchangerate-api.com")

        with mock.patch.object(currency, "fetch_jmd_per_usd", return_value=fetched):
            asyncio.run(dialog._refresh(None))

        self.assertEqual(dialog.rate.value, "160.0000")
        self.assertIn("Live rate", dialog.status.value)
        self.assertEqual(self.repo.get_setting(currency.RATE_SETTING), "160.000000")

    def test_cached_rate_is_loaded(self):
        currency.save_rate(
            self.repo,
            currency.ExchangeRate(157.5, datetime.now(timezone.utc), "exchangerate-api.com"),
        )
        dialog = self._dialog()
        self.assertEqual(dialog.rate.value, "157.5000")
        self.assertIn("Saved rate", dialog.status.value)

    def test_transaction_editor_jmd_button_fills_the_amount(self):
        from budget_tracker.ui.views.transactions import TransactionEditor

        editor = TransactionEditor(self.ctx, None)
        self.assertEqual(editor.amount_row.controls[1].content, "JMD")

        editor._apply_usd_amount(6_314)

        self.assertEqual(editor.amount.value, "63.14")


if __name__ == "__main__":
    unittest.main()
