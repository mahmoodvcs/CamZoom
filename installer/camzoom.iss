; Inno Setup script for CamZoom. Build with tools\build.ps1 (needs the driver and app built first).

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6F1B2C3D-8E4A-4B5C-9D7E-2A1F0C3B4D5E}
AppName=CamZoom
AppVersion={#AppVersion}
AppPublisher=CamZoom
AppPublisherURL=https://github.com/mahmoodvcs/CamZoom
DefaultDirName={autopf}\CamZoom
DefaultGroupName=CamZoom
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=CamZoom-{#AppVersion}-setup
SetupIconFile=..\assets\camzoom.ico
UninstallDisplayIcon={app}\CamZoom.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Registering the camera driver needs admin rights.
PrivilegesRequired=admin
; Closes a running CamZoom before upgrading/uninstalling (it holds the files open).
AppMutex=CamZoomSingleInstance
CloseApplications=yes

[Tasks]
Name: "startup"; Description: "Start CamZoom when Windows starts"; GroupDescription: "Options:"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\CamZoom\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; The virtual camera driver: 64-bit for 64-bit apps (e.g. Teams), 32-bit for older 32-bit apps.
Source: "..\build\driver-x64\Release\camzoom-camera64.dll"; DestDir: "{app}\driver"; Flags: ignoreversion regserver 64bit
Source: "..\build\driver-x86\Release\camzoom-camera32.dll"; DestDir: "{app}\driver"; Flags: ignoreversion regserver 32bit
Source: "..\driver\placeholder.png"; DestDir: "{app}\driver"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\CamZoom"; Filename: "{app}\CamZoom.exe"
Name: "{autodesktop}\CamZoom"; Filename: "{app}\CamZoom.exe"; Tasks: desktopicon

[Registry]
; Same value CamZoom's own "Start with Windows" menu item writes, so the two stay in sync.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "CamZoom"; \
  ValueData: """{app}\CamZoom.exe"" --autostart"; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\CamZoom.exe"; Description: "Start CamZoom now"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im CamZoom.exe"; Flags: runhidden; RunOnceId: "StopCamZoom"
