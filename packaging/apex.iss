; ApexSetup.exe (product, 30 Sep 2026). Build: packaging/make_installer.py
; Per-user install into %LOCALAPPDATA%\Apex: Apex writes its database, profile and radio button
; next to itself, which Program Files would refuse, and no admin prompt stands between a driver
; and his first race. Uninstalling removes the program and keeps the driver's own files
; (apex.db, profile.json, ptt_button.json, server.json), so a reinstall remembers them.

#ifndef SourceDir
  #error Pass /DSourceDir=<the built dist\Apex folder>
#endif
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6C2E5D8B-6B4B-4E0E-9E54-2B8F0A1D7A41}
AppName=Apex
AppVersion={#AppVersion}
AppVerName=Apex {#AppVersion} (early access)
AppPublisher=Gourav Tiwari
DefaultDirName={localappdata}\Apex
DefaultGroupName=Apex
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputBaseFilename=ApexSetup-{#AppVersion}
SetupIconFile=apex.ico
UninstallDisplayIcon={app}\Apex.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Put Apex on the desktop"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Apex"; Filename: "{app}\Apex.exe"; WorkingDir: "{app}"
Name: "{group}\Apex setup (name, radio button, mode)"; Filename: "{app}\Apex.exe"; Parameters: "--setup"; WorkingDir: "{app}"
Name: "{group}\Uninstall Apex"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Apex"; Filename: "{app}\Apex.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Apex.exe"; Description: "Set up Apex now"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
