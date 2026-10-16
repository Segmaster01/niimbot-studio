; Niimbot Studio installer (Inno Setup 6).
; Build: iscc /DAppVersion=1.0.0 installer\NiimbotStudio.iss   (after PyInstaller has produced dist\NiimbotStudio)
;
; - Installs per-user by default (no admin prompt), so the in-app updater can upgrade silently.
; - Brand packs: put any *.nsbrand file next to Setup.exe and it is installed with the app.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Niimbot Studio"
#define AppExe "NiimbotStudio.exe"

[Setup]
AppId={{6C1D9F7A-3B52-4E8D-9A41-2F0E7C5B8D13}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Niimbot Studio contributors
AppPublisherURL=https://github.com/Segmaster01/niimbot-studio
AppUpdatesURL=https://github.com/Segmaster01/niimbot-studio/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
OutputDir=..\dist
OutputBaseFilename=NiimbotStudio-Setup-{#AppVersion}
SetupIconFile=..\niimbot_studio\resources\icon-blue.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
RestartApplications=no
ChangesAssociations=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "associate"; Description: "Open .nlbl label files with {#AppName}"; GroupDescription: "File types:"

[Files]
Source: "..\dist\NiimbotStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Remove files left by older builds before copying the new ones.
Type: filesandordirs; Name: "{app}\_internal"

[UninstallDelete]
Type: files; Name: "{app}\brand.nsbrand"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.nlbl"; ValueType: string; ValueName: ""; ValueData: "NiimbotStudio.Label"; Flags: uninsdeletevalue; Tasks: associate
Root: HKA; Subkey: "Software\Classes\NiimbotStudio.Label"; ValueType: string; ValueName: ""; ValueData: "Niimbot Studio label"; Flags: uninsdeletekey; Tasks: associate
Root: HKA; Subkey: "Software\Classes\NiimbotStudio.Label\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\NiimbotStudio.Label\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: associate

[Run]
; Interactive install: offer to launch. Silent install (in-app update): always relaunch.
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: WizardSilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  Rec: TFindRec;
begin
  if CurStep = ssPostInstall then
  begin
    { Install the first brand pack found beside Setup.exe, if any. }
    if FindFirst(ExpandConstant('{src}\*.nsbrand'), Rec) then
    begin
      try
        FileCopy(ExpandConstant('{src}\') + Rec.Name, ExpandConstant('{app}\brand.nsbrand'), False);
      finally
        FindClose(Rec);
      end;
    end;
  end;
end;
