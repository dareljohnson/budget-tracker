<#
.SYNOPSIS
    Builds the standalone Windows app (flet build windows) and wraps it in an installer.

.DESCRIPTION
    Output, under build\:
      windows\                                          self-contained app folder (BudgetTracker.exe + runtime)
      installer\BudgetTracker-<ver>-windows-x64-setup.exe   Inno Setup installer (if Inno Setup is installed)
      installer\BudgetTracker-<ver>-windows-x64.zip         portable zip (always)

    Prerequisites: Visual Studio 2022/2026 with the "Desktop development with C++" workload.
    Flutter is downloaded automatically by flet on first build. Inno Setup is optional:
      winget install --id JRSoftware.InnoSetup -e

.EXAMPLE
    .\packaging\windows\build.ps1
    .\packaging\windows\build.ps1 -SkipBuild      # only re-package an existing build\windows
#>
[CmdletBinding()]
param([switch]$SkipBuild)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $root

$version = (Select-String -Path pyproject.toml -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value

if (-not $SkipBuild) {
    $flet = Join-Path $root '.venv\Scripts\flet.exe'
    if (-not (Test-Path $flet)) { $flet = 'flet' }
    # --no-compile-packages keeps cv2/config.py; compiling it to .pyc breaks the OCR import.
    & $flet build windows --python-version 3.13 --no-compile-packages --yes --no-rich-output
    if ($LASTEXITCODE -ne 0) { throw "flet build windows failed (exit $LASTEXITCODE). See PACKAGING.md > Troubleshooting." }
}
if (-not (Test-Path 'build\windows\BudgetTracker.exe')) { throw 'build\windows\BudgetTracker.exe not found.' }

New-Item -ItemType Directory -Force build\installer | Out-Null
$zip = "build\installer\BudgetTracker-$version-windows-x64.zip"
Compress-Archive -Path 'build\windows\*' -DestinationPath $zip -Force
Write-Host "Portable zip: $zip"

$iscc = @(
    (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe"
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if ($iscc) {
    & $iscc /Q packaging\windows\installer.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed (exit $LASTEXITCODE)." }
    Write-Host "Installer:    build\installer\BudgetTracker-$version-windows-x64-setup.exe"
}
else {
    Write-Warning 'Inno Setup not found; skipped setup.exe. Install it with: winget install --id JRSoftware.InnoSetup -e'
}
