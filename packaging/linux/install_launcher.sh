#!/usr/bin/env bash
# Add Budget Tracker to the Linux application menu, running it from this source folder with the
# project's .venv. Use packaging/linux/build.sh for a standalone bundle instead.
#
#   bash packaging/linux/install_launcher.sh            # install
#   bash packaging/linux/install_launcher.sh --uninstall
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DESKTOP="$DATA/applications/budget-tracker.desktop"
ICON="$DATA/icons/hicolor/256x256/apps/budget-tracker.png"

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -f "$DESKTOP" "$ICON"
  update-desktop-database "$DATA/applications" 2>/dev/null || true
  echo "Removed launcher"
  exit 0
fi

PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Virtual environment not found. Create it first:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -e '.[desktop]'" >&2
  exit 1
fi

mkdir -p "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
cp "$ROOT/packaging/linux/budget_tracker.png" "$ICON"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Budget Tracker
Comment=Personal budget tracker
Exec="$PYTHON" "$ROOT/packaging/launch.pyw"
Path=$ROOT
Icon=budget-tracker
Terminal=false
Categories=Office;Finance;
EOF
update-desktop-database "$DATA/applications" 2>/dev/null || true
echo "Installed $DESKTOP"
