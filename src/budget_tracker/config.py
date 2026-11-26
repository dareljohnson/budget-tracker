"""Application configuration and filesystem locations."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Budget Tracker"
DB_FILENAME = "budget_tracker.db"
CURRENCY_SYMBOL = "$"


def data_dir() -> Path:
    """Return the per-user data directory, creating it if needed.

    Override with the ``BUDGET_TRACKER_HOME`` environment variable.
    """
    override = os.environ.get("BUDGET_TRACKER_HOME")
    if override:
        path = Path(override)
    elif os.name == "nt":
        path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BudgetTracker"
    else:
        path = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "budget_tracker"
    path.mkdir(parents=True, exist_ok=True)
    return path


def actual_data_dir() -> Path:
    """The data folder as Explorer sees it.

    A Microsoft Store Python redirects ``%LOCALAPPDATA%`` writes into
    ``Packages\\<package family>\\LocalCache\\Local``. The app process reads and writes the
    logical path transparently, but Explorer and other programs only see the package path.
    Honor an explicit ``BUDGET_TRACKER_HOME`` and non-Windows platforms first.
    """
    if os.name != "nt" or os.environ.get("BUDGET_TRACKER_HOME"):
        return data_dir()
    local = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    base = Path(getattr(sys, "base_prefix", sys.prefix))
    if "WindowsApps" in base.parts:
        name = next(
            (
                part
                for part in base.parts
                if part.startswith("PythonSoftwareFoundation.Python.")
            ),
            None,
        )
        if name and "__" in name:
            family = name.split("_", 1)[0] + "_" + name.rsplit("__", 1)[-1]
            candidate = (
                local
                / "Packages"
                / family
                / "LocalCache"
                / "Local"
                / "BudgetTracker"
            )
            if candidate.is_dir():
                return candidate
        packages = local / "Packages"
        for candidate in sorted(
            packages.glob("PythonSoftwareFoundation.Python.*/LocalCache/Local/BudgetTracker")
        ):
            if candidate.is_dir():
                return candidate
    return data_dir()


def db_path() -> Path:
    return data_dir() / DB_FILENAME


def export_dir() -> Path:
    downloads = Path.home() / "Downloads"
    target = downloads if downloads.is_dir() else data_dir()
    target.mkdir(parents=True, exist_ok=True)
    return target
