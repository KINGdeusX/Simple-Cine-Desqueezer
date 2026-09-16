; Inno Setup script for Desqueeze.
;
; Installs the application together with ExifTool, FFmpeg and DNGLab, which is
; what lets the app resolve its tools at startup instead of asking the user
; where ExifTool is on every launch.
;
; Build with:  iscc PC\tools\desqueeze.iss
; It expects PyInstaller to have produced PC\dist\Desqueeze\ first.

#define AppName       "Desqueeze"
#define AppVersion    "2.0"
#define AppPublisher  "KINGdeusX"
#define AppURL        "https://github.com/KINGdeusX/Simple-Cine-Desqueezer"
#define AppExeName    "Desqueeze.exe"

[Setup]
AppId={{7C3F5A21-4E8B-4C1D-9A6E-DE5Q2E3Z1000}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=..\..\LICENSE
OutputDir=..\dist
OutputBaseFilename=Desqueeze-{#AppVersion}-Setup
SetupIconFile=..\..\icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 64-bit only: the bundled FFmpeg and ExifTool builds are x64.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Installing per-user needs no administrator rights; the installer asks.
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The whole PyInstaller folder, including vendor\ with the helper binaries.
Source: "..\dist\Desqueeze\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; \
    Flags: ignoreversion
Source: "..\..\docs\LICENSING.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\README.md"; DestDir: "{app}"; DestName: "README.txt"; \
    Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; \
    Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Settings live in the user's AppData and are deliberately left behind, so a
; reinstall remembers the output folders; only our own logs go.
Type: filesandordirs; Name: "{app}\logs"
