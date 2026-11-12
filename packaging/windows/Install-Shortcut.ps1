<#
.SYNOPSIS
    Adds (or removes) Start menu and desktop shortcuts that run Budget Tracker from this source folder.

.DESCRIPTION
    No build tools needed: the shortcut starts the app with the project's virtual environment
    (.venv\Scripts\pythonw.exe, so no console window appears). Use packaging\windows\build.ps1
    instead to produce a standalone installer that doesn't need Python.

.EXAMPLE
    .\packaging\windows\Install-Shortcut.ps1              # Start menu + desktop
    .\packaging\windows\Install-Shortcut.ps1 -NoDesktop   # Start menu only
    .\packaging\windows\Install-Shortcut.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$NoDesktop,
    # Where to create shortcuts. Defaults to the Start menu (and the desktop unless -NoDesktop).
    [string[]]$Folder
)

$ErrorActionPreference = 'Stop'
$name = 'Budget Tracker'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path

if (-not $Folder) {
    $Folder = @([Environment]::GetFolderPath('Programs'))
    if (-not $NoDesktop) { $Folder += [Environment]::GetFolderPath('Desktop') }
}

if ($Uninstall) {
    foreach ($dir in $Folder) {
        $lnk = Join-Path $dir "$name.lnk"
        if (Test-Path $lnk) { Remove-Item $lnk; Write-Host "Removed $lnk" }
    }
    return
}

$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) {
    throw "Virtual environment not found at $root\.venv. Create it first with Python 3.13+: python -m venv .venv; .venv\Scripts\pip install -e "".[desktop]"""
}

# A .venv copied from another PC still points at that PC's Python. Its pythonw.exe is only a
# launcher: it reads pyvenv.cfg and re-runs the interpreter named by `home`, so when that path is
# missing, clicking the shortcut shows "Python venv launcher is sorry to say ...". Detect it here
# and explain how to recover instead.
$cfg = Join-Path $root '.venv\pyvenv.cfg'
if (Test-Path $cfg) {
    $m = Select-String -Path $cfg -Pattern '^\s*home\s*=\s*(.+?)\s*$' | Select-Object -First 1
    if ($m -and -not (Test-Path $m.Matches[0].Groups[1].Value)) {
        $venvHome = $m.Matches[0].Groups[1].Value
        throw @"
The virtual environment at $root\.venv was created on another PC, so it cannot run here:
pyvenv.cfg points at $venvHome, which does not exist on this machine.

Recreate the environment with Python 3.13 or newer, then run this script again:
  Remove-Item '$root\.venv' -Recurse -Force
  python -m venv '$root\.venv'
  & '$root\.venv\Scripts\python.exe' -m pip install -e '$root[desktop]'
"@
    }
}

$shell = New-Object -ComObject WScript.Shell
try {
    foreach ($dir in $Folder) {
        New-Item -ItemType Directory -Force $dir | Out-Null
        $lnk = Join-Path $dir "$name.lnk"
        $shortcut = $shell.CreateShortcut($lnk)
        $shortcut.TargetPath = $pythonw
        $shortcut.Arguments = '"' + (Join-Path $root 'packaging\launch.pyw') + '"'
        $shortcut.WorkingDirectory = $root
        $shortcut.IconLocation = (Join-Path $root 'packaging\windows\budget_tracker.ico') + ',0'
        $shortcut.Description = 'Personal budget tracker'
        $shortcut.Save()
        Write-Host "Created $lnk"
    }
}
finally {
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
}
