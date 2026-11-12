"""Application configuration and filesystem locations."""

from __future__ import annotations

import os
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


def db_path() -> Path:
    return data_dir() / DB_FILENAME


def export_dir() -> Path:
    downloads = Path.home() / "Downloads"
    target = downloads if downloads.is_dir() else data_dir()
    target.mkdir(parents=True, exist_ok=True)
    return target
