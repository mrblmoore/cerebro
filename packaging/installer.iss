; Cerebro Windows installer.
;
; Build with Inno Setup 6:
;   iscc packaging\installer.iss
;
; Produces dist\CerebroSetup.exe - a single file the user double-clicks. It needs
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
#define SetupExeName   "CerebroSetupWizard.exe"

[Setup]
AppId={{8F2C4E1A-9D3B-4A57-B6E0-1C7A5D9E3B21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#VersionInfoVersion}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#VersionInfoVersion}

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
SetupIconFile=cerebro.ico

; Branding. Inno picks the size matching the user's display scaling, so each
; image ships at 1x and 2x rather than being upscaled into a blurry mess.
WizardImageFile=images\wizard-image.bmp,images\wizard-image@2x.bmp
WizardSmallImageFile=images\wizard-small.bmp,images\wizard-small@2x.bmp
WizardImageStretch=yes
WizardImageAlphaFormat=none
SetupLogging=yes
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel1=Welcome to Cerebro
WelcomeLabel2=Cerebro is a second brain for technical support. It follows the case you are working on, remembers how you have solved things before, and drafts your notes and replies.%n%nEverything stays on this computer - your cases, documents and recordings never leave it unless you connect a cloud AI model yourself.%n%nThis takes about a minute. Setup runs at the end and will get you working.
FinishedHeadingLabel=Cerebro is installed
FinishedLabel=Leave the box below ticked and setup will open next. It checks everything installed correctly, prepares your database and connects a model - testing each one - so Cerebro is ready to use when it closes.

[Tasks]
Name: "desktopicon"; Description: "Put Cerebro on my desktop"; \
  GroupDescription: "Shortcuts:"
; The widget is a small always-on-top panel showing the current case, so the
; description says what it is rather than just naming it.
Name: "widgetstartup"; \
  Description: "Start the Cerebro widget when I sign in (a small always-on-top panel)"; \
  GroupDescription: "Startup:"

[Files]
; Everything PyInstaller produced, including the bundled browser extension.
Source: "..\dist\Cerebro\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Cerebro";            Filename: "{app}\{#AppExeName}"
Name: "{group}\Cerebro Widget";     Filename: "{app}\{#WidgetExeName}"
Name: "{group}\Cerebro Setup";      Filename: "{app}\{#SetupExeName}"
Name: "{group}\Cerebro Dashboard";  Filename: "http://localhost:8000"
Name: "{group}\Install Browser Extension"; Filename: "{app}\_internal\browser-extension"
Name: "{group}\Uninstall Cerebro";  Filename: "{uninstallexe}"
Name: "{autodesktop}\Cerebro";      Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{autodesktop}\Cerebro Widget"; Filename: "{app}\{#WidgetExeName}"; Tasks: desktopicon
Name: "{userstartup}\Cerebro Widget"; Filename: "{app}\{#WidgetExeName}"; Tasks: widgetstartup

[Run]
; Configuration is part of installing, not a separate errand afterwards. The
; wizard checks dependencies, sets up the database, connects an AI provider and
; tests each one before it closes - so "installed" and "ready to use" are the
; same moment. It offers to start Cerebro itself at the end, which is why the
; widget is not launched separately here.
;
; Not "nowait": the installer should stay up until configuration is done,
; otherwise the progress window vanishes and the wizard looks like a stray
; program that opened by itself.
Filename: "{app}\{#SetupExeName}"; Parameters: "--first-run"; \
  Description: "Set up Cerebro now (recommended)"; \
  Flags: postinstall skipifsilent

; For anyone who unticks the box above and just wants it running.
Filename: "{app}\{#WidgetExeName}"; Description: "Start Cerebro without setting it up"; \
  Flags: nowait postinstall skipifsilent unchecked

[UninstallDelete]
; PyInstaller writes caches next to the executable; leave nothing behind.
Type: filesandordirs; Name: "{app}\_internal\__pycache__"

[Code]
{ Cerebro keeps its database, logs and settings in %LOCALAPPDATA%\Cerebro,
  deliberately outside the install directory so an upgrade never touches them.
  On uninstall the user chooses whether that goes too - losing an entire case
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
