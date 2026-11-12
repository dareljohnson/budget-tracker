"""Start Budget Tracker from a source checkout without a console window.

Used by the shortcut/launcher scripts in this folder:
  Windows: .venv\\Scripts\\pythonw.exe packaging\\launch.pyw
  macOS/Linux: .venv/bin/python packaging/launch.pyw
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from budget_tracker.main import run  # noqa: E402

run()
