; Inno Setup script for Undercut.
; Built by .github/workflows/release.yml on the CI runner; AppVersion is
; passed in with /DAppVersion=x.y.z so it never drifts from app/__init__.py.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "Undercut"
#define AppPublisher "Mario Domingos"
#define AppURL "https://github.com/mariosundays/Undercut"
#define AppExeName "Undercut.exe"

[Setup]
AppId={{7C2F1E64-3A9B-4C1D-8E52-1B6D9A4F2C08}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
; Per-user install to %LOCALAPPDATA%\Programs\Undercut. No admin prompt, and
; no writing into Program Files - antivirus behaviour monitors (Bitdefender)
; blocked the unsigned uninstaller being created there, breaking updates.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
; Close a running Undercut rather than failing to replace its files.
CloseApplications=yes
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=LICENSE
OutputDir=releases
OutputBaseFilename=Undercut_v{#AppVersion}_setup
SetupIconFile=app\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The bundled ffmpeg is 64-bit only.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
  GroupDescription: "Additional shortcuts:"

[Files]
; The whole PyInstaller onedir tree, ffmpeg included.
Source: "dist\Undercut\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; \
  Flags: nowait postinstall skipifsilent

[Code]
// Up to 0.1.6 Undercut installed for all users into Program Files. A per-user
// install cannot remove that (it needs admin), so point the user at it.
function InitializeSetup(): Boolean;
begin
  Result := True;
  if RegKeyExists(HKLM64, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{7C2F1E64-3A9B-4C1D-8E52-1B6D9A4F2C08}_is1') then
    MsgBox('An older copy of Undercut is installed in Program Files.' + #13#10 + #13#10 +
      'This version installs just for you instead, so updates no longer need ' +
      'administrator rights. After setup, remove the old copy from ' +
      'Settings > Apps > Installed apps (the older "Undercut" entry).',
      mbInformation, MB_OK);
end;
