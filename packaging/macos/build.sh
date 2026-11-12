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
  "$FLET" build macos --python-version 3.13 --yes --no-rich-output
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
