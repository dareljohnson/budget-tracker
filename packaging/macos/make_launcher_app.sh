#!/usr/bin/env bash
# Create ~/Applications/Budget Tracker.app, a small launcher that runs the app from this source folder
# using the project's .venv. No Xcode needed. Use packaging/macos/build.sh for a standalone .dmg instead.
#
#   bash packaging/macos/make_launcher_app.sh            # install
#   bash packaging/macos/make_launcher_app.sh --uninstall
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="$HOME/Applications/Budget Tracker.app"

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -rf "$APP" && echo "Removed $APP"
  exit 0
fi

PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Virtual environment not found. Create it first:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -e '.[desktop]'" >&2
  exit 1
fi

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/MacOS/BudgetTracker" <<EOF
#!/bin/bash
cd "$ROOT"
exec "$PYTHON" "$ROOT/packaging/launch.pyw"
EOF
chmod +x "$APP/Contents/MacOS/BudgetTracker"

# Build an .icns from the 1024px source icon with the tools that ship with macOS.
ICONSET="$(mktemp -d)/AppIcon.iconset"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z $size $size "$ROOT/src/assets/icon.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  sips -z $((size * 2)) $((size * 2)) "$ROOT/src/assets/icon.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf "$(dirname "$ICONSET")"

cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Budget Tracker</string>
  <key>CFBundleDisplayName</key><string>Budget Tracker</string>
  <key>CFBundleIdentifier</key><string>com.budgettracker.launcher</string>
  <key>CFBundleExecutable</key><string>BudgetTracker</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.finance</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

echo "Created $APP (find it in Launchpad/Spotlight; drag it to the Dock to pin it)."
