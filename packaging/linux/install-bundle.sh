#!/usr/bin/env sh
# Shipped inside the Linux tarball as install.sh. Registers the extracted bundle in the app menu for the
# current user (the bundle stays where you extracted it). Run again after moving the folder.
#
#   ./install.sh            # add to the application menu
#   ./install.sh --uninstall
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
ID="com.budgettracker.budget-tracker"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$DATA/applications/$ID.desktop"
  find "$DATA/icons/hicolor" -name "$ID.png" -delete 2>/dev/null || true
  update-desktop-database "$DATA/applications" 2>/dev/null || true
  echo "Removed Budget Tracker from the application menu. Delete $HERE to remove the app."
  exit 0
fi

mkdir -p "$DATA"
cp -r "$HERE/share/." "$DATA/"
ENTRY="$DATA/applications/$ID.desktop"
if [ ! -f "$ENTRY" ]; then
  echo "Desktop entry $ENTRY was not installed; the bundle is incomplete." >&2
  exit 1
fi
sed -i "s|^Exec=.*|Exec=\"$HERE/BudgetTracker\" %U|" "$ENTRY"
update-desktop-database "$DATA/applications" 2>/dev/null || true
echo "Budget Tracker added to the application menu."
