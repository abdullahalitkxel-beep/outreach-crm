; Inno Setup script for the direct-download installer (OutreachCRM-Setup.exe).
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
AppName=Outreach CRM
AppVersion={#AppVersion}
AppVerName=Outreach CRM {#AppVersion}
AppPublisher={#Publisher}
AppCopyright=Copyright (C) 2026 {#Publisher}
VersionInfoCompany={#Publisher}
VersionInfoProductName=Outreach CRM
VersionInfoDescription=Outreach CRM Setup
VersionInfoVersion={#AppVersion}.0
DefaultDirName={localappdata}\Programs\Outreach CRM
DefaultGroupName=Outreach CRM
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
LicenseFile=..\build\EULA.txt
OutputDir=..\dist
OutputBaseFilename=OutreachCRM-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\OutreachCRM.exe
UninstallDisplayName=Outreach CRM
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Files]
Source: "..\dist\OutreachCRM\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Outreach CRM"; Filename: "{app}\OutreachCRM.exe"
Name: "{autodesktop}\Outreach CRM"; Filename: "{app}\OutreachCRM.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Run]
Filename: "{app}\OutreachCRM.exe"; Description: "Open Outreach CRM"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM OutreachCRM.exe /F"; Flags: runhidden; RunOnceId: "StopApp"

[Messages]
FinishedLabel=Outreach CRM is installed. Your data is kept in %APPDATA%\OutreachCRM and is not removed when you uninstall.
