import tomllib
from pathlib import Path

import flet as ft

from budget_tracker.main import _pinned_page


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


def test_nav_indices_stay_in_sync_with_the_labels():
    from budget_tracker import main as shell

    assert shell.NAV_ITEMS[1][0] == "Year"
    assert shell.NAV_ITEMS[shell.TRANSACTIONS_INDEX][0] == "Transactions"
    assert shell.NAV_ITEMS[shell.BILLS_INDEX][0] == "Bills"
