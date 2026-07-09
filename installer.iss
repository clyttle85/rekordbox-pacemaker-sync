[Setup]
AppName=Rekordbox Pacemaker Sync
AppVersion=1.0
AppPublisher=Chris Lyttle
AppPublisherURL=
DefaultDirName={autopf}\RekordboxPacemakerSync
DefaultGroupName=Rekordbox Pacemaker Sync
OutputBaseFilename=RekordboxPacemakerSync_Setup
OutputDir=dist
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\RekordboxPacemakerSync.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: checked

[Files]
Source: "dist\RekordboxPacemakerSync\RekordboxPacemakerSync.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\RekordboxPacemakerSync\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Rekordbox Pacemaker Sync"; Filename: "{app}\RekordboxPacemakerSync.exe"; IconFilename: "{app}\RekordboxPacemakerSync.exe"
Name: "{group}\Uninstall Rekordbox Pacemaker Sync"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Rekordbox Pacemaker Sync"; Filename: "{app}\RekordboxPacemakerSync.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RekordboxPacemakerSync.exe"; Description: "Launch Rekordbox Pacemaker Sync"; Flags: nowait postinstall skipifsilent
