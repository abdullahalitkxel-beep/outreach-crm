; Inno Setup script for the direct-download installer (BSO-Setup.exe).
; Installs per user (no administrator rights needed), shows the licence agreement,
; and registers BlacSwanken as the publisher in Windows "Installed apps".

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef Publisher
  #define Publisher "BlacSwanken"
#endif

[Setup]
AppId={{6F0B5A3C-2E7D-4C1B-9A55-8B2E7D4C1B9A}
AppName=BSO – BlacSwanken Outreach
AppVersion={#AppVersion}
AppVerName=BSO – BlacSwanken Outreach {#AppVersion}
AppPublisher={#Publisher}
AppCopyright=Copyright (C) 2026 {#Publisher}
VersionInfoCompany={#Publisher}
VersionInfoProductName=BSO – BlacSwanken Outreach
VersionInfoDescription=BSO – BlacSwanken Outreach Setup
VersionInfoVersion={#AppVersion}.0
DefaultDirName={localappdata}\Programs\BSO
DefaultGroupName=BSO
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
LicenseFile=..\build\EULA.txt
OutputDir=..\dist
OutputBaseFilename=BSO-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\BSO.exe
UninstallDisplayName=BSO – BlacSwanken Outreach
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Files]
Source: "..\dist\BSO\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\BSO"; Filename: "{app}\BSO.exe"
Name: "{autodesktop}\BSO"; Filename: "{app}\BSO.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Run]
Filename: "{app}\BSO.exe"; Description: "Open BSO – BlacSwanken Outreach"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM BSO.exe /F"; Flags: runhidden; RunOnceId: "StopApp"

[Messages]
FinishedLabel=BSO – BlacSwanken Outreach is installed. Your data is kept in %APPDATA%\OutreachCRM and is not removed when you uninstall.
