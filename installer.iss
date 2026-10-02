; Inno Setup script for API Client — per-user (NON-ADMIN) installer.
; Installs under the user's profile, no UAC / administrator rights required.
;
; Build with:  iscc installer.iss
; (Install Inno Setup from https://jrsoftware.org/isdl.php first.)
;
; Packages the one-dir PyInstaller build, so run the build first:
;   python build_onefile.py --app-name APIClient --mode onedir

#define MyAppName "API Client"
#define MyAppVersion "2.0"
#define MyAppPublisher "Vaibhav Patil"
#define MyAppExeName "APIClient.exe"
#define MyAppId "{{8F3C2A41-7B6D-4E2A-9C1F-2A9E51D0C3AA}}"
; Folder produced by: build_onefile.py --mode onedir
#define SourceDir "dist\onedir\APIClient"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Per-user install location (no admin rights needed)
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=assets\app.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Force a non-admin, per-user install — never prompt for elevation
PrivilegesRequired=lowest
OutputDir=dist\installer
OutputBaseFilename=API-Client-Setup-{#MyAppVersion}-user

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{userprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{userprograms}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
