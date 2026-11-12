#!/usr/bin/env bash
# Build the standalone Linux app (flet build linux) and package it.
#
# Output:
#   build/linux/                                         self-contained bundle (BudgetTracker + lib/ + data/)
#   build/installer/BudgetTracker-<ver>-linux-x64.tar.gz bundle + install.sh (adds it to the app menu)
#   build/installer/BudgetTracker-<ver>-x86_64.AppImage  only if `appimagetool` is on PATH
#
# Prerequisites (Debian/Ubuntu): see PACKAGING.md. Works on Linux or WSL. Flutter is downloaded by flet.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
VERSION="$(sed -nE 's/^version *= *"([^"]+)"/\1/p' pyproject.toml | head -1)"
BUNDLE_ID="com.budgettracker.budget_tracker"
NAME="BudgetTracker-$VERSION-linux-x64"

FLET="$ROOT/.venv/bin/flet"; [[ -x "$FLET" ]] || FLET=flet
if [[ "${1:-}" != "--skip-build" ]]; then
  "$FLET" build linux --python-version 3.13 --yes --no-rich-output
fi
[[ -x build/linux/BudgetTracker ]] || { echo "build/linux/BudgetTracker not found" >&2; exit 1; }

mkdir -p build/installer
STAGE="$(mktemp -d)"
cp -R build/linux "$STAGE/$NAME"
install -m 755 packaging/linux/install-bundle.sh "$STAGE/$NAME/install.sh"
tar -C "$STAGE" -czf "build/installer/$NAME.tar.gz" "$NAME"
echo "Tarball:  build/installer/$NAME.tar.gz"

if command -v appimagetool >/dev/null 2>&1; then
  APPDIR="$STAGE/BudgetTracker.AppDir"
  mkdir -p "$APPDIR/usr"
  cp -R build/linux/. "$APPDIR/usr/"
  cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/BudgetTracker" "$@"
EOF
  chmod +x "$APPDIR/AppRun"
  cp packaging/linux/budget_tracker.png "$APPDIR/$BUNDLE_ID.png"
  cat > "$APPDIR/$BUNDLE_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Budget Tracker
Comment=Personal budget tracker
Exec=BudgetTracker
Icon=$BUNDLE_ID
Categories=Office;Finance;
Terminal=false
EOF
  ARCH=x86_64 appimagetool "$APPDIR" "build/installer/BudgetTracker-$VERSION-x86_64.AppImage"
  echo "AppImage: build/installer/BudgetTracker-$VERSION-x86_64.AppImage"
else
  echo "appimagetool not found; skipped AppImage (see PACKAGING.md)."
fi
rm -rf "$STAGE"
