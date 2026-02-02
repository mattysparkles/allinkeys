#define AppName "AllInKeys"
#ifndef AppVersion
#define AppVersion "0.0.0"
#endif
#define AppPublisher "MattySparkles"
#define AppExeName "AllInKeys.exe"

[Setup]
AppId={{C6F02E1A-3D78-4A89-9D97-46A7A51F1B10}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
OutputDir=dist\installer
OutputBaseFilename=AllInKeys-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExeName}

[Files]
Source: "dist\AllInKeys\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
