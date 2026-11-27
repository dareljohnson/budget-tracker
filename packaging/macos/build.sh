#!/usr/bin/env bash
# Build the standalone macOS app (flet build macos) and package it as a drag-to-install .dmg.
#
# Output: build/macos/Budget Tracker.app and
#         build/installer/BudgetTracker-<ver>-macos-<arch>.dmg
#
# Set MACOS_ARCH=arm64 (the default) for Apple Silicon or MACOS_ARCH=x86_64 for
# Intel Macs. The Intel build uses packaging/macos/constraints-intel.txt because
# the newest cryptography, ONNX Runtime and OpenCV releases no longer support
# current Intel macOS releases.
#
# Prerequisites: Xcode (from the App Store) + `sudo xcode-select -s /Applications/Xcode.app`, and CocoaPods
# (`brew install cocoapods`). Flutter is downloaded automatically by flet on first build.
#
# Optional signing/notarization (needed so Gatekeeper opens the app without warnings):
#   MACOS_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
#   NOTARY_PROFILE=<profile saved with `xcrun notarytool store-credentials`>
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
ARCH="${MACOS_ARCH:-arm64}"
case "$ARCH" in
  arm64|x86_64) ;;
  *) echo "MACOS_ARCH must be arm64 or x86_64 (got '$ARCH')" >&2; exit 1 ;;
esac
VERSION="$(sed -nE 's/^version *= *"([^"]+)"/\1/p' pyproject.toml | head -1)"
APP="build/macos/Budget Tracker.app"
DMG="build/installer/BudgetTracker-$VERSION-macos-$ARCH.dmg"

# serious_python runs pip in a subprocess and passes the parent environment
# through, so PIP_CONSTRAINT applies the Intel wheel pins to flet's package step.
if [[ "$ARCH" == "x86_64" ]]; then
  export PIP_CONSTRAINT="$ROOT/packaging/macos/constraints-intel.txt"
fi

FLET="$ROOT/.venv/bin/flet"; [[ -x "$FLET" ]] || FLET=flet
if [[ "${1:-}" != "--skip-build" ]]; then
  # --no-compile-packages keeps cv2/config.py; compiling it to .pyc breaks the OCR import.
  "$FLET" build macos --arch "$ARCH" --python-version 3.13 --no-compile-packages --yes --no-rich-output
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
created=0
for attempt in 1 2 3; do
  rm -f "$DMG"
  if hdiutil create -volname "Budget Tracker" -srcfolder "$STAGE" -ov -format UDZO \
      -fs HFS+ "$DMG"; then
    created=1
    break
  fi
  echo "hdiutil attempt $attempt failed; retrying..." >&2
  sleep 5
done
rm -rf "$STAGE"
if [[ "$created" -ne 1 ]]; then
  echo "Could not create $DMG" >&2
  exit 1
fi

if [[ -n "${MACOS_SIGN_IDENTITY:-}" ]]; then
  codesign --force --timestamp --sign "$MACOS_SIGN_IDENTITY" "$DMG"
fi
if [[ -n "${NOTARY_PROFILE:-}" ]]; then
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
fi
echo "Disk image: $DMG"
