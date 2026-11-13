from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_window_icon_asset_is_a_windows_icon():
    icon = ROOT / "src" / "assets" / "icon.ico"
    assert icon.is_file()
    assert icon.read_bytes()[:4] == b"\x00\x00\x01\x00"


def test_assets_dir_resolves_from_source():
    from budget_tracker.main import _assets_dir

    assets = Path(_assets_dir())
    assert (assets / "icon.ico").is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows taskbar identity")
def test_taskbar_identity_is_set_for_source_runs(monkeypatch):
    monkeypatch.delenv("FLET_APP_USER_MODEL_ID", raising=False)
    from budget_tracker.main import _taskbar_identity

    identity = _taskbar_identity()
    assert identity["FLET_APP_USER_MODEL_ID"] == "com.budgettracker.budget_tracker"
    assert identity["FLET_APP_RELAUNCH_DISPLAY_NAME"] == "Budget Tracker"
    assert identity["FLET_APP_RELAUNCH_ICON"].endswith("icon.ico,0")
    assert identity["FLET_APP_RELAUNCH_COMMAND"]
