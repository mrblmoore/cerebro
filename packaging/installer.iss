; Cerebro Windows installer.
;
; Build with Inno Setup 6:
;   iscc packaging\installer.iss
;
; Produces dist\CerebroSetup.exe — a single file the user double-clicks. It needs
; no Python, no admin rights and no prerequisites: PyInstaller has already bundled
; the runtime, and the install goes to the user's own profile.

#define AppName        "Cerebro"
#ifndef AppVersion
  #define AppVersion   "0.0.0-dev"
#endif
#ifndef VersionInfoVersion
  #define VersionInfoVersion "0.0.0.0"
#endif
#define AppPublisher   "Cerebro"
#define AppExeName     "Cerebro.exe"
#define WidgetExeName  "CerebroWidget.exe"

[Setup]
AppId={{8F2C4E1A-9D3B-4A57-B6E0-1C7A5D9E3B21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#VersionInfoVersion}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}

; Per-user install: no UAC prompt, no admin rights, works on a locked-down
; machine where users cannot write to Program Files.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Cerebro
DefaultGroupName=Cerebro
DisableProgramGroupPage=yes
DisableDirPage=auto
AllowNoIcons=yes

OutputDir=..\dist
OutputBaseFilename=CerebroSetup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
SetupLogging=yes
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; \
  GroupDescription: "Shortcuts:"
Name: "widgetstartup"; Description: "Start the Cerebro widget when I sign in"; \
  GroupDescription: "Startup:"; Flags: unchecked

[Files]
; Everything PyInstaller produced, including the bundled browser extension.
Source: "..\dist\Cerebro\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Cerebro";            Filename: "{app}\{#AppExeName}"
Name: "{group}\Cerebro Widget";     Filename: "{app}\{#WidgetExeName}"
Name: "{group}\Cerebro Dashboard";  Filename: "http://localhost:8000"
Name: "{group}\Uninstall Cerebro";  Filename: "{uninstallexe}"
Name: "{autodesktop}\Cerebro";      Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userstartup}\Cerebro Widget"; Filename: "{app}\{#WidgetExeName}"; Tasks: widgetstartup

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Start Cerebro now"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; PyInstaller writes caches next to the executable; leave nothing behind.
Type: filesandordirs; Name: "{app}\_internal\__pycache__"

[Code]
{ Cerebro keeps its database, logs and settings in %LOCALAPPDATA%\Cerebro,
  deliberately outside the install directory so an upgrade never touches them.
  On uninstall the user chooses whether that goes too — losing an entire case
  history to a routine uninstall would be a nasty surprise. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\Cerebro');
    if DirExists(DataDir) then
    begin
      if MsgBox('Also delete your Cerebro data?' + #13#10#13#10 +
                'This removes your case history, indexed documents, settings ' +
                'and logs from:' + #13#10 + DataDir + #13#10#13#10 +
                'Choose No to keep them for a future reinstall.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;

{ Warn rather than fail if Cerebro is running: the exe cannot be replaced while
  it is in use, and "access denied" mid-install is a confusing way to find out. }
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
