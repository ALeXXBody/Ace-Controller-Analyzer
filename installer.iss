; CD3217B12 Analyzer — Inno Setup installer script
; Built on CI (Inno Setup 6 is preinstalled on windows-latest runners).
; Compiles the PyInstaller onedir output into a proper Windows installer:
; Program Files install, Start Menu + optional desktop shortcuts, uninstaller.

#define AppName "CD3217B12 Analyzer"
; CI passes the real release tag via ISCC /DAppVersion=..., this is the fallback.
#ifndef AppVersion
#define AppVersion "0.3.0"
#endif
#define AppPublisher "CD3217 Analyzer Project"
#define AppExeName "CD3217B12_Analyzer.exe"

[Setup]
AppId={{8E4C3D2A-9F6B-4E7A-B1C5-CD3217B12ANLZ}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
OutputDir=installer
OutputBaseFilename=CD3217B12_Analyzer_Setup
SetupIconFile=assets\icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Per-user install (like Chrome/VSCode user installs): no UAC prompt,
; {autopf} resolves to the user's Programs folder.
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked; OnlyBelowVersion: 6.1

[Files]
Source: "dist\CD3217B12_Analyzer\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#AppName}"; \
    Filename: "{app}\{#AppExeName}"; Tasks: quicklaunchicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove user-generated reports/logs created next to the app
Type: filesandordirs; Name: "{app}\reports"
