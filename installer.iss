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
DefaultDirName={autopf}\{#AppName}
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
