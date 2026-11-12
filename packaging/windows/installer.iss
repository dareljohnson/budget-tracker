; Inno Setup script: turns the `flet build windows` output (build\windows) into a single setup.exe.
; Adapted from https://flet.dev/docs/publish/windows. Normally run through packaging\windows\build.ps1;
; to compile by hand from the project root:
;   & "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" packaging\windows\installer.iss

#define AppId "com.budgettracker.budget_tracker"
#define AppExe "BudgetTracker.exe"
#define Root AddBackslash(SourcePath) + "..\.."
#define BuildDir Root + "\build\windows"

#define AppExePath BuildDir + "\" + AppExe
#if !FileExists(AppExePath)
  #error BudgetTracker.exe not found in build\windows: run "flet build windows" (or packaging\windows\build.ps1) first.
#endif
#define AppName GetStringFileInfo(AppExePath, "ProductName")
#define AppPublisher GetStringFileInfo(AppExePath, "CompanyName")
#define FullVersion GetStringFileInfo(AppExePath, "ProductVersion")
#define AppVersion Copy(FullVersion, 1, Pos("+", FullVersion + "+") - 1)

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#GetVersionNumbersString(AppExePath)}
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
DefaultDirName={autopf}\Budget Tracker
DisableProgramGroupPage=yes
; Per-user install by default (no admin prompt); the user may choose "all users" in the wizard.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
SetupIconFile={#Root}\packaging\windows\budget_tracker.ico
OutputDir={#Root}\build\installer
OutputBaseFilename=BudgetTracker-{#AppVersion}-windows-x64-setup
SolidCompression=yes
CloseApplications=yes

[Tasks]
Name: desktopicon; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; On upgrade, clear the bundled runtime so files removed in a new version don't linger.
Type: filesandordirs; Name: "{app}\app"; Check: IsUpgrade
Type: filesandordirs; Name: "{app}\data"; Check: IsUpgrade
Type: filesandordirs; Name: "{app}\DLLs"; Check: IsUpgrade
Type: filesandordirs; Name: "{app}\Lib"; Check: IsUpgrade
Type: filesandordirs; Name: "{app}\site-packages"; Check: IsUpgrade
Type: files; Name: "{app}\*.dll"; Check: IsUpgrade

[Files]
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Budget Tracker"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\Budget Tracker"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,Budget Tracker}"; Flags: nowait postinstall skipifsilent

[Code]
function IsUpgrade: Boolean;
begin
  Result := CompareText(RemoveBackslash(WizardForm.PrevAppDir), RemoveBackslash(WizardDirValue)) = 0;
end;
