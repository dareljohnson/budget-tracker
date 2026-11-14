# Packaging & launchers

How to run Budget Tracker like a normal desktop app on **Windows 11, macOS and Linux**: either a quick
**launcher** that runs it from this source folder, or a standalone **installer** that bundles Python so the
user needs nothing else installed.

## Options at a glance

| Option | Windows 11 | macOS | Linux | Needs Python on the user's PC? |
|---|---|---|---|---|
| **Launcher** (run from source) | Start menu + desktop shortcut | `~/Applications/Budget Tracker.app` | App-menu `.desktop` entry | Yes (the project's `.venv`) |
| **Native build** (`flet build`) | `BudgetTracker.exe` folder → Inno Setup `setup.exe` + portable `.zip` | `Budget Tracker.app` → `.dmg` | Bundle → `.tar.gz` (+ `.AppImage`) | No, Python is bundled |
| **CI build** (GitHub Actions) | All three, built on the right OS automatically | | | No |

**Each platform must be built on that platform** (or on a CI runner for it): Windows on Windows, macOS on a Mac,
Linux on Linux or WSL. This is a Flutter restriction; `flet build` uses Flutter under the hood.

> `flet pack` (PyInstaller) is an alternative that skips the Visual Studio, Xcode and Flutter toolchains, but it
> produces less polished results. This project uses `flet build`, which is Flet's recommended route.

---

## 1. Launchers (no build tools)

All launchers run `packaging/launch.pyw` with the project's virtual environment. Set up the environment once
with **Python 3.13 or newer** (the project's `requires-python`); check with `python --version` first and, if
that points at an older interpreter, name your 3.13 install explicitly (e.g. `py -3.13 -m venv .venv`):

```powershell
# Windows
python -m venv .venv
.venv\Scripts\pip install -e ".[desktop]"
```
```bash
# macOS / Linux
python3 -m venv .venv
.venv/bin/pip install -e '.[desktop]'
```

| Platform | Install | Remove |
|---|---|---|
| Windows | `.\packaging\windows\Install-Shortcut.ps1` (add `-NoDesktop` for Start menu only) | `.\packaging\windows\Install-Shortcut.ps1 -Uninstall` |
| macOS | `bash packaging/macos/make_launcher_app.sh` | `bash packaging/macos/make_launcher_app.sh --uninstall` |
| Linux | `bash packaging/linux/install_launcher.sh` | `bash packaging/linux/install_launcher.sh --uninstall` |

The Windows shortcut uses `pythonw.exe`, so no console window opens. If you move the project folder, run the
install command again.

### Moving the project to another PC

A virtual environment belongs to the machine that created it. `.venv\pyvenv.cfg` records the absolute path of
the base Python, and `pythonw.exe` in `.venv\Scripts` is only a small launcher that re-runs that interpreter.
Copying the project folder (or a backup) with `.venv` included therefore fails on the new PC with a
*"Python venv launcher is sorry to say ..."* dialog. Never copy `.venv` between machines; recreate it:

1. Delete the copied environment and create a fresh one (Windows shown; see the commands above for
   macOS/Linux):
   ```powershell
   Remove-Item .venv -Recurse -Force
   python -m venv .venv
   .venv\Scripts\pip install -e ".[desktop]"
   ```
2. Re-run the launcher install for that platform — shortcuts and launchers store absolute paths.
3. Copy your database from the old PC if you want your data; its location depends on how the old copy ran
   (see [Where data lives](#where-data-lives)).

The Windows launcher script detects a relocated `.venv` and stops with these instructions rather than letting
the venv launcher fail silently.

---

## 2. Native installers (`flet build`)

### How it works

1. `flet build <platform>` creates a Flutter project in `build/flutter`. It packages `src/main.py`, the
   `budget_tracker` package and `src/assets/icon.png`, along with a **Python 3.13** runtime (pinned with
   `--python-version 3.13`) and the dependencies listed in `[project].dependencies` (`flet`,
   `pdfplumber`, `pypdfium2`, `Pillow`, `rapidocr-onnxruntime` and `onnxruntime`).
2. Flutter compiles a native executable into `build/<platform>/`.
3. The scripts in `packaging/<platform>/build.*` wrap that folder in an installer under `build/installer/`.

Flutter is **downloaded automatically** to `~/flutter/<version>` on the first build, which is a large one-time
download. App metadata comes from `pyproject.toml`:

```toml
[tool.flet]
product = "Budget Tracker"     # window title / launcher name
artifact = "BudgetTracker"     # BudgetTracker.exe, Linux binary
org = "com.budgettracker"      # bundle id: com.budgettracker.budget_tracker
company = "Budget Tracker"     # keep stable between releases
[tool.flet.app]
path = "src"                   # contains main.py + assets/
[tool.flet.macos]
artifact = "Budget Tracker"    # Budget Tracker.app
```

The version shown in installers is `[project].version`. **Bump it for every release** so upgrades install
cleanly.

### Runtime dependencies

Statement import runs entirely locally and uses these runtime dependencies:

- **pdfplumber** — reads PDF text and table geometry.
- **pypdfium2** — rasterises PDF pages for OCR.
- **Pillow** — loads PNG and JPEG statements.
- **RapidOCR (`rapidocr-onnxruntime`) on ONNX Runtime (`onnxruntime`)** — the bundled OCR engine for
  scans, photos, and PDFs with no usable text layer.

All of them are normal runtime dependencies in `pyproject.toml`, so `flet build` bundles them
automatically. Expect the OCR models and ONNX Runtime to add **roughly 200 MB** to the installer and
portable payload. The build scripts already pass `--no-compile-packages`: compiling `cv2/config.py` to
a `.pyc` breaks the OCR import.

Import supports most US checking or savings statements — specifically, those that print a standard
date / description / amount transaction table — in PDF, PNG, or JPEG form, and is accepted only when
the parsed rows reconcile to the statement's printed summary. Layouts without a recognizable
transaction table are refused; credit cards, non-USD statements, bank feeds, and OFX/QIF/CAMT/CSV
import are out of scope. Statement files are never copied into the database: of the file itself, the
app keeps only its SHA-256 hash and, at most, the last four account digits. See
[Importing bank statements](README.md#importing-bank-statements) in the README.

### Windows 11

**Prerequisites**
- Visual Studio 2022 or 2026 with the **"Desktop development with C++"** workload. In the Visual Studio
  Installer, choose *Modify*, tick that workload, and install it (this needs admin rights).
- Optional: Inno Setup, used to create `setup.exe`: `winget install --id JRSoftware.InnoSetup -e`
- Developer Mode on (*Settings → System → For developers*). Flutter needs it for symlinks.

**Build**
```powershell
.\packaging\windows\build.ps1
```

**Output** (`build\installer\`):
- `BudgetTracker-<version>-windows-x64-setup.exe`: the installer.
  - Installs per-user by default, so no admin prompt.
  - Adds a Start menu entry, and a desktop icon if the user picks that option.
  - Registers an uninstaller under *Settings → Apps*.
  - Upgrades an existing install in place.
  - Can run silently: `/VERYSILENT /SUPPRESSMSGBOXES /CURRENTUSER`.
- `BudgetTracker-<version>-windows-x64.zip`: a portable version. Unzip it and run `BudgetTracker.exe`. Keep the
  folder together, because the `.exe` needs the DLLs next to it.

**Code signing.** Unsigned apps trigger SmartScreen ("Windows protected your PC" → *More info* → *Run anyway*).
A code-signing certificate from a trusted CA plus `signtool` removes "Unknown publisher". See
<https://flet.dev/docs/publish/windows#code-signing>.

### macOS

**Prerequisites:** Xcode from the App Store, then `sudo xcode-select -s /Applications/Xcode.app`, plus
CocoaPods (`brew install cocoapods`).

**Build**
```bash
bash packaging/macos/build.sh
```

**Output:** `build/installer/BudgetTracker-<version>-macos.dmg`. The user opens it and drags *Budget Tracker* to
*Applications*.

**Signing and notarization.** Without them, Gatekeeper blocks the first launch and the user must right-click and
choose *Open*. With an Apple Developer ID, set these before building:
```bash
export MACOS_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export NOTARY_PROFILE=budget-tracker   # created once with: xcrun notarytool store-credentials budget-tracker
```

### Linux (Ubuntu/Debian; also works in WSL)

**Prerequisites**
```bash
sudo apt update
sudo apt install -y binutils clang cmake ninja-build pkg-config llvm lld libgtk-3-dev \
  libsecret-1-0 libsecret-1-dev libunwind-dev libasound2-dev libmpv-dev mpv \
  libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libgstreamer-plugins-bad1.0-dev \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-libav
```
Optional: install [`appimagetool`](https://github.com/AppImage/appimagetool/releases) on `PATH` to also produce
an AppImage.

**Build**
```bash
bash packaging/linux/build.sh
```

**Output** (`build/installer/`):
- `BudgetTracker-<version>-linux-x64.tar.gz`: extract it anywhere and run `./install.sh` to add the app to the
  application menu (`./install.sh --uninstall` removes it).
- `BudgetTracker-<version>-x86_64.AppImage` (if appimagetool is available): a single file. Make it executable and
  run it.

For `.deb`, `.rpm` or Flatpak packages, point those tools at `build/linux/`. The bundle already ships
`share/applications/*.desktop` and the icons.

---

## Versioning

`src/budget_tracker/__init__.py` (`__version__`) and `pyproject.toml` (`project.version`) are the two places the
version lives. They must match, and `tests/test_app_shell.py` fails if they drift. Bump both together for a
release — minor for feature releases, patch for fixes. The build scripts and installer read the packaged
executable and `pyproject.toml`, so no other file carries the number. The app's nav rail shows the value.

## 3. Build all three with GitHub Actions

`.github/workflows/build-installers.yml`:
1. Runs the tests.
2. Builds on `windows-latest`, `macos-latest` and `ubuntu-24.04` in parallel.
3. Uploads the installers as workflow artifacts.

To use it:
1. Push the project to a GitHub repository.
2. **Actions → Build installers → Run workflow** builds the installers and attaches them as artifacts.
3. Or tag a release to also publish a **GitHub Release** with all installers attached:
   ```bash
   git tag v1.3.0 && git push origin v1.3.0
   ```

Signing secrets aren't wired in by default. Add them as repository secrets and export `MACOS_SIGN_IDENTITY` /
`NOTARY_PROFILE` (macOS) or a `signtool` step (Windows) when you have certificates.

---

## Where data lives

The app keeps one SQLite database per user. Its location depends on how the app was started:

| How it runs | Windows database |
|---|---|
| Installed build (`setup.exe` or portable `.zip`) | `%LOCALAPPDATA%\BudgetTracker\budget_tracker.db` |
| Source run with a regular (python.org) Python venv | `%LOCALAPPDATA%\BudgetTracker\budget_tracker.db` |
| Source run with a **Microsoft Store** Python venv | `%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\Local\BudgetTracker\budget_tracker.db` |

On macOS and Linux, both source runs and bundles use `~/.local/share/budget_tracker/budget_tracker.db`.

The Store exception exists because the Store Python ships as an MSIX package: Windows redirects that
process's writes to `%LOCALAPPDATA%` into the package's private `LocalCache`. The app works normally, but the
file is not where the other modes look. (The `Python.3.13` part of the path follows your Store Python
version; find the folder with `Get-ChildItem "$env:LOCALAPPDATA\Packages" -Filter 'PythonSoftwareFoundation.Python.*'`.)

Installed builds and source runs with regular Python share the same path, so switching between those two
keeps your data. To switch from a Store-Python source run to the installed build, move the database to the
target path — or make both modes agree by setting `BUDGET_TRACKER_HOME`:

```powershell
$env:BUDGET_TRACKER_HOME = 'C:\Users\you\BudgetTrackerData'
```

To make the Start-menu shortcut use it too, set it as a persistent user variable (for example
`setx BUDGET_TRACKER_HOME "C:\Users\you\BudgetTrackerData"`) and sign out and back in.

Uninstalling never deletes your data. Back up the file before experimenting.

## App icon

`src/assets/icon.png` (1024×1024) is the source icon for every platform; `flet build` generates the
platform-specific sizes from it. `src/assets/icon_windows.ico` is the explicit Windows build icon.
`src/assets/icon.ico` is the Windows window/taskbar icon the app sets at runtime
(`page.window.icon`; Flet needs an absolute `.ico` path). The launcher icons
(`packaging/windows/budget_tracker.ico` and `packaging/linux/budget_tracker.png`) are generated by the
same script. To change the design, edit the shapes and colors in `packaging/make_icons.py`, then run:
```bash
python packaging/make_icons.py
```
To use your own artwork instead, replace those icon files with a square image that has a transparent
background (and regenerate `src/assets/icon.ico`, which must stay an `.ico`).

## Troubleshooting

| Problem | Fix |
|---|---|
| `flet build windows`: *Visual Studio not installed* / *Unable to find suitable Visual Studio toolchain* | Install the **Desktop development with C++** workload (see Windows prerequisites). |
| Windows build fails creating symlinks | Turn on Developer Mode. |
| macOS: *CocoaPods not installed* | `brew install cocoapods` |
| Linux: linker error mentioning `lld` | `sudo apt install lld` |
| Stale or odd build after changing settings | `flet clean`, then build again. |
| Built app shows the Flet logo | Make sure `src/assets/icon.png` exists, then `flet clean` and rebuild. |
| Windows launcher does nothing | Run `.venv\Scripts\python packaging\launch.pyw` in a terminal to see the error (usually a missing `.venv`). |
| Shortcut shows *"Python venv launcher is sorry to say ... did not find executable"* | The `.venv` was copied from another PC. Delete and recreate it — see [Moving the project to another PC](#moving-the-project-to-another-pc). |

## Files

```
pyproject.toml                       [tool.flet] app metadata used by flet build
src/main.py                          entry point flet build packages
src/assets/icon.png                  app icon (1024x1024)
packaging/
  launch.pyw                         runs the app from source without a console
  make_icons.py                      regenerates the icons (stdlib only)
  windows/Install-Shortcut.ps1       Start menu/desktop shortcut launcher
  windows/build.ps1                  flet build windows → setup.exe + zip
  windows/installer.iss              Inno Setup script
  windows/budget_tracker.ico         shortcut icon
  macos/make_launcher_app.sh         ~/Applications launcher .app
  macos/build.sh                     flet build macos → .dmg (optional sign + notarize)
  linux/install_launcher.sh          app-menu launcher
  linux/build.sh                     flet build linux → .tar.gz (+ AppImage)
  linux/install-bundle.sh            install.sh shipped inside the tarball
  linux/budget_tracker.png           launcher icon
.github/workflows/build-installers.yml  CI builds for all three platforms
```
