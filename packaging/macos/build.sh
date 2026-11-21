#!/usr/bin/env bash
# Build the standalone macOS app (flet build macos) and package it as a drag-to-install .dmg.
#
# Output: build/macos/Budget Tracker.app and build/installer/BudgetTracker-<ver>-macos.dmg
# Prerequisites: Xcode (from the App Store) + `sudo xcode-select -s /Applications/Xcode.app`, and CocoaPods
# (`brew install cocoapods`). Flutter is downloaded automatically by flet on first build.
#
# Optional signing/notarization (needed so Gatekeeper opens the app without warnings):
#   MACOS_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
#   NOTARY_PROFILE=<profile saved with `xcrun notarytool store-credentials`>
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
VERSION="$(sed -nE 's/^version *= *"([^"]+)"/\1/p' pyproject.toml | head -1)"
APP="build/macos/Budget Tracker.app"
DMG="build/installer/BudgetTracker-$VERSION-macos.dmg"

FLET="$ROOT/.venv/bin/flet"; [[ -x "$FLET" ]] || FLET=flet
if [[ "${1:-}" != "--skip-build" ]]; then
  # --no-compile-packages keeps cv2/config.py; compiling it to .pyc breaks the OCR import.
  "$FLET" build macos --python-version 3.13 --no-compile-packages --yes --no-rich-output
fi
[[ -d "$APP" ]] || { echo "$APP not found" >&2; exit 1; }

if [[ -n "${MACOS_SIGN_IDENTITY:-}" ]]; then
  # Keep the entitlements flet build applied (JIT / unsigned memory are required by the Python runtime).
  codesign --force --deep --options runtime --timestamp --preserve-metadata=entitlements \
    --sign "$MACOS_SIGN_IDENTITY" "$APP"
fi

STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

# Give the mounted disk image the app icon; otherwise Finder shows the generic
# disk-image icon even though the .app inside has its own icon.
if command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1; then
  ICONSET="$(mktemp -d)/AppIcon.iconset"
  mkdir -p "$ICONSET"
  for size in 16 32 128 256 512; do
    sips -z "$size" "$size" "$ROOT/src/assets/icon.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    sips -z "$((size * 2))" "$((size * 2))" "$ROOT/src/assets/icon.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
  done
  if iconutil -c icns "$ICONSET" -o "$STAGE/.VolumeIcon.icns"; then
    if command -v SetFile >/dev/null 2>&1; then
      SetFile -a C "$STAGE"
    else
      echo "SetFile not found; the DMG volume may keep the generic disk icon." >&2
    fi
  fi
  rm -rf "$(dirname "$ICONSET")"
else
  echo "sips or iconutil not found; the DMG will keep the generic disk icon." >&2
fi

mkdir -p build/installer
rm -f "$DMG"
hdiutil create -volname "Budget Tracker" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"

if [[ -n "${MACOS_SIGN_IDENTITY:-}" ]]; then
  codesign --force --timestamp --sign "$MACOS_SIGN_IDENTITY" "$DMG"
fi
if [[ -n "${NOTARY_PROFILE:-}" ]]; then
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
fi
echo "Disk image: $DMG"
