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
WelcomeLabel2=Cerebro is your assistant for technical support. It follows the case you are working on, remembers how you have solved things before, drafts your notes and replies, and talks with you.%n%nEverything it needs is inside this installer - the speech recognition, the offline voice and the 3D assistants - so it works even on a network that blocks downloads.%n%nThe next few pages set it up. Each choice is checked before you finish, and all of them can be changed later in Settings.
FinishedHeadingLabel=Cerebro is installed
FinishedLabel=Cerebro is ready. Your choices were saved and checked.
SelectTasksLabel2=Choose the extras you'd like, then click Next.

[Tasks]
Name: "desktopicon"; Description: "Put Cerebro on my desktop"; \
  GroupDescription: "Shortcuts:"
; Cerebro starts quietly in the system tray (its animated icon) and opens with
; a click, so the description says what that means.
Name: "widgetstartup"; \
  Description: "Start Cerebro in the system tray when I sign in"; \
  GroupDescription: "Startup:"

[InstallDelete]
; A clean upgrade: the previous version's bundled files go before the new ones
; are copied, so nothing stale is left behind. Your data lives in
; %LOCALAPPDATA%\Cerebro and is never touched here.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; Everything PyInstaller produced: the app, the bundled browser extension, the
; speech-recognition model, the offline voice and the built-in 3D models.
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
Name: "{userstartup}\Cerebro Widget"; Filename: "{app}\{#WidgetExeName}"; Parameters: "--background"; Tasks: widgetstartup

[Run]
; Setup normally happens on the installer's own pages (see [Code]): the choices
; are applied and tested by "CerebroSetupWizard.exe --apply" before the last
; page, so "installed" and "ready to use" are the same moment. The full setup
; wizard (--first-run) is offered instead when the person chose to set up later.
Filename: "{app}\{#SetupExeName}"; Parameters: "--first-run"; \
  Description: "Open the setup wizard now"; \
  Flags: postinstall skipifsilent; Check: WantsSetupWizard

Filename: "{app}\{#AppExeName}"; Description: "Open Cerebro now"; \
  Flags: nowait postinstall skipifsilent; Check: not WantsSetupWizard

[UninstallDelete]
; PyInstaller writes caches next to the executable; leave nothing behind.
Type: filesandordirs; Name: "{app}\_internal\__pycache__"

[Code]
var
  WipePage: TInputOptionWizardPage;
  ProviderPage: TInputOptionWizardPage;
  BedrockPage: TInputQueryWizardPage;
  OpenAIPage: TInputQueryWizardPage;
  OllamaPage: TInputQueryWizardPage;
  LookPage: TInputOptionWizardPage;
  Live2DBox: TNewCheckBox;
  PersonaPage: TInputOptionWizardPage;
  VoicePage: TInputOptionWizardPage;
  SetupSummary: String;
  SetupFailed: Boolean;

const
  PROVIDER_BEDROCK = 0;
  PROVIDER_OPENAI = 1;
  PROVIDER_OLLAMA = 2;
  PROVIDER_KEEP = 3;
  PROVIDER_LATER = 4;

function DataDir(): String;
begin
  Result := ExpandConstant('{localappdata}\Cerebro');
end;

{ An earlier Cerebro left data behind: this is an upgrade. }
function IsUpgrade(): Boolean;
begin
  Result := FileExists(DataDir() + '\cerebro.env') or FileExists(DataDir() + '\cerebro.db')
    or DirExists(DataDir() + '\logs');
end;

function Provider(): Integer;
begin
  Result := ProviderPage.SelectedValueIndex;
end;

function WantsSetupWizard(): Boolean;
begin
  Result := Provider() = PROVIDER_LATER;
end;

procedure InitializeWizard();
begin
  { Upgrades: clear parts of the old memory, if wanted. Nothing is ticked. }
  WipePage := CreateInputOptionPage(wpSelectTasks,
    'Your existing Cerebro data',
    'Cerebro found data from an earlier version. Keep it all, or clear some of it.',
    'Everything is kept unless you tick it. Anything you clear is backed up first (to the ' +
    'backups folder in ' + DataDir() + ') and is cleared the next time Cerebro starts.',
    False, False);
  WipePage.Add('Chats and their history');
  WipePage.Add('Learned memory and writing style');
  WipePage.Add('Indexed documents and sources');
  WipePage.Add('Activity recordings and screenshots');
  WipePage.Add('Mail and Teams messages Cerebro read, and follow-ups');
  WipePage.Add('Agents, jobs, watches and ideas');
  WipePage.Add('Settings (back to the defaults)');
  WipePage.Add('Sign-ins in Cerebro''s hidden browser');

  ProviderPage := CreateInputOptionPage(WipePage.ID,
    'AI provider', 'Which AI should Cerebro think with?',
    'Amazon Bedrock is usually allowed on work networks. You can change this any time in Settings.',
    True, False);
  ProviderPage.Add('Amazon Bedrock (AWS)');
  ProviderPage.Add('OpenAI, or an OpenAI-compatible service');
  ProviderPage.Add('Ollama (a model on this PC or your network)');
  ProviderPage.Add('Keep my current settings');
  ProviderPage.Add('Set it up later');
  if IsUpgrade() then
    ProviderPage.SelectedValueIndex := PROVIDER_KEEP
  else
    ProviderPage.SelectedValueIndex := PROVIDER_BEDROCK;

  BedrockPage := CreateInputQueryPage(ProviderPage.ID,
    'Amazon Bedrock', 'How Cerebro signs in to AWS',
    'Enter access keys, or an AWS profile name, or leave both empty to use the AWS sign-in ' +
    'already on this PC. The same sign-in is used for the Nova Sonic voice.');
  BedrockPage.Add('Region:', False);
  BedrockPage.Add('Model ID (leave empty for the default):', False);
  BedrockPage.Add('Access key ID:', False);
  BedrockPage.Add('Secret access key:', True);
  BedrockPage.Add('Or an AWS profile name:', False);
  BedrockPage.Values[0] := 'us-east-1';

  OpenAIPage := CreateInputQueryPage(BedrockPage.ID,
    'OpenAI-compatible service', 'Where Cerebro sends its questions',
    'Leave the address empty for OpenAI itself.');
  OpenAIPage.Add('Service address (optional):', False);
  OpenAIPage.Add('API key:', True);
  OpenAIPage.Add('Model:', False);

  OllamaPage := CreateInputQueryPage(OpenAIPage.ID,
    'Ollama', 'Where Ollama runs', 'Ollama runs models on this PC or on a server you control.');
  OllamaPage.Add('Ollama address:', False);
  OllamaPage.Add('Model:', False);
  OllamaPage.Values[0] := 'http://localhost:11434';

  LookPage := CreateInputOptionPage(OllamaPage.ID,
    'Your assistant', 'How should your assistant look?',
    'Each agent can have its own look later (the gear above its chat).', True, False);
  LookPage.Add('A 3D character (built in)');
  LookPage.Add('A drawn anime face');
  LookPage.Add('Cerebro''s brain');
  LookPage.SelectedValueIndex := 0;
  { Live2D's runtime can't ship with Cerebro (its licence), but it and Live2D's
    free sample models can be downloaded from Live2D now, if you agree. }
  LookPage.CheckListBox.Height := ScaleY(84);
  Live2DBox := TNewCheckBox.Create(LookPage);
  Live2DBox.Parent := LookPage.Surface;
  Live2DBox.Top := LookPage.CheckListBox.Top + LookPage.CheckListBox.Height + ScaleY(14);
  Live2DBox.Left := LookPage.CheckListBox.Left;
  Live2DBox.Width := LookPage.SurfaceWidth;
  Live2DBox.Height := ScaleY(36);
  Live2DBox.Caption := 'Also get Live2D models: download Live2D''s runtime and 6 free sample models' + #13#10 +
    'from Live2D (about 20 MB). You accept Live2D''s licences.';
  Live2DBox.Checked := False;

  PersonaPage := CreateInputOptionPage(LookPage.ID,
    'Personality', 'How should your assistant come across?',
    'It always stays kind and work-appropriate, and replies it drafts for other people keep your own style.',
    True, False);
  PersonaPage.Add('Sweetheart - cute, cheerful, a little teasing');
  PersonaPage.Add('Warm and natural');
  PersonaPage.Add('Calm and soft');
  PersonaPage.Add('Professional');
  PersonaPage.SelectedValueIndex := 0;

  VoicePage := CreateInputOptionPage(PersonaPage.ID,
    'Voice', 'Talking to Cerebro',
    'Speech is recognised on this PC, and the built-in voice works offline.', False, False);
  VoicePage.Add('Talk to Cerebro (push-to-talk and the mic button)');
  VoicePage.Add('Speak replies aloud');
  VoicePage.Add('Use Amazon Nova Sonic, the most expressive voice (needs Amazon Bedrock)');
  VoicePage.Values[0] := True;
  VoicePage.Values[1] := True;
  VoicePage.Values[2] := False;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if PageID = WipePage.ID then
    Result := not IsUpgrade()
  else if PageID = BedrockPage.ID then
    Result := Provider() <> PROVIDER_BEDROCK
  else if PageID = OpenAIPage.ID then
    Result := Provider() <> PROVIDER_OPENAI
  else if PageID = OllamaPage.ID then
    Result := Provider() <> PROVIDER_OLLAMA
  else if (PageID = LookPage.ID) or (PageID = PersonaPage.ID) or (PageID = VoicePage.ID) then
    Result := (Provider() = PROVIDER_KEEP) or (Provider() = PROVIDER_LATER);
end;

{ ---------------------------------------------------------------- JSON }
function JsonEscape(S: String): String;
var
  I: Integer;
  C: String;
begin
  Result := '';
  for I := 1 to Length(S) do
  begin
    C := Copy(S, I, 1);
    if C = '\' then
      Result := Result + '\\'
    else if C = '"' then
      Result := Result + '\"'
    else if Ord(S[I]) < 32 then
      Result := Result + ' '
    else
      Result := Result + C;
  end;
end;

procedure AddPair(var Json: String; Key, Value: String);
begin
  if Value = '' then
    Exit;
  if Json <> '' then
    Json := Json + ', ';
  Json := Json + '"' + Key + '": "' + JsonEscape(Value) + '"';
end;

function BoolText(Value: Boolean): String;
begin
  if Value then
    Result := 'true'
  else
    Result := 'false';
end;

function ChoicesJson(): String;
var
  Settings, Agent, Face, Persona: String;
begin
  Settings := '';
  case Provider() of
    PROVIDER_BEDROCK:
      begin
        AddPair(Settings, 'LLM_PROVIDER', 'bedrock');
        AddPair(Settings, 'BEDROCK_REGION', Trim(BedrockPage.Values[0]));
        AddPair(Settings, 'BEDROCK_MODEL_ID', Trim(BedrockPage.Values[1]));
        if Trim(BedrockPage.Values[2]) <> '' then
        begin
          AddPair(Settings, 'BEDROCK_AUTH_MODE', 'keys');
          AddPair(Settings, 'BEDROCK_AWS_ACCESS_KEY_ID', Trim(BedrockPage.Values[2]));
          AddPair(Settings, 'BEDROCK_AWS_SECRET_ACCESS_KEY', Trim(BedrockPage.Values[3]));
        end
        else if Trim(BedrockPage.Values[4]) <> '' then
        begin
          AddPair(Settings, 'BEDROCK_AUTH_MODE', 'profile');
          AddPair(Settings, 'BEDROCK_AWS_PROFILE', Trim(BedrockPage.Values[4]));
        end
        else
          AddPair(Settings, 'BEDROCK_AUTH_MODE', 'default');
      end;
    PROVIDER_OPENAI:
      begin
        AddPair(Settings, 'LLM_PROVIDER', 'openai');
        AddPair(Settings, 'OPENAI_BASE_URL', Trim(OpenAIPage.Values[0]));
        AddPair(Settings, 'OPENAI_API_KEY', Trim(OpenAIPage.Values[1]));
        AddPair(Settings, 'OPENAI_MODEL', Trim(OpenAIPage.Values[2]));
      end;
    PROVIDER_OLLAMA:
      begin
        AddPair(Settings, 'LLM_PROVIDER', 'ollama');
        AddPair(Settings, 'OLLAMA_URL', Trim(OllamaPage.Values[0]));
        AddPair(Settings, 'OLLAMA_MODEL', Trim(OllamaPage.Values[1]));
      end;
  end;
  case PersonaPage.SelectedValueIndex of
    1: Persona := 'warm';
    2: Persona := 'calm';
    3: Persona := 'professional';
  else
    Persona := 'sweetheart';
  end;
  case LookPage.SelectedValueIndex of
    1: Face := 'anime';
    2: Face := 'brain';
  else
    Face := 'builtin';
  end;
  AddPair(Settings, 'VOICE_PERSONALITY', Persona);
  AddPair(Settings, 'VOICE_ENABLED', BoolText(VoicePage.Values[0]));
  AddPair(Settings, 'VOICE_SPEAK_REPLIES', BoolText(VoicePage.Values[1]));
  if VoicePage.Values[2] then
    AddPair(Settings, 'VOICE_PROVIDER', 'sonic')
  else
    AddPair(Settings, 'VOICE_PROVIDER', 'auto');
  Agent := '';
  AddPair(Agent, 'face', Face);
  AddPair(Agent, 'personality', Persona);
  Result := '{"settings": {' + Settings + '}, "agent": {' + Agent + '}, "live2d": ' +
    BoolText(Live2DBox.Checked) + '}';
end;

{ The parts of the old memory to clear, for Cerebro to apply (after a backup) on its next start. }
procedure WriteResetRequest();
var
  Names: TArrayOfString;
  Parts: String;
  I: Integer;
begin
  SetArrayLength(Names, 8);
  Names[0] := 'chats'; Names[1] := 'memory'; Names[2] := 'documents'; Names[3] := 'activity';
  Names[4] := 'inbox'; Names[5] := 'agents'; Names[6] := 'settings'; Names[7] := 'signins';
  Parts := '';
  for I := 0 to 7 do
    if WipePage.Values[I] then
    begin
      if Parts <> '' then
        Parts := Parts + ', ';
      Parts := Parts + '"' + Names[I] + '"';
    end;
  if Parts <> '' then
  begin
    ForceDirectories(DataDir());
    SaveStringToFile(DataDir() + '\reset.json', '{"parts": [' + Parts + ']}', False);
  end;
end;

{ Apply the setup choices and test them, with the setup wizard running headless. }
procedure ApplySetup();
var
  ChoicesFile, ResultFile, Lines: String;
  Code: Integer;
  Text: AnsiString;
begin
  SetupSummary := '';
  SetupFailed := False;
  if (Provider() = PROVIDER_KEEP) or (Provider() = PROVIDER_LATER) then
    Exit;
  ChoicesFile := ExpandConstant('{tmp}\cerebro-choices.json');
  ResultFile := ExpandConstant('{tmp}\cerebro-setup-result.json');
  SaveStringToFile(ChoicesFile, ChoicesJson(), False);
  WizardForm.StatusLabel.Caption := 'Checking your setup: the database, the AI provider and the voice...';
  if not Exec(ExpandConstant('{app}\{#SetupExeName}'),
              '--apply "' + ChoicesFile + '" --result "' + ResultFile + '"',
              '', SW_HIDE, ewWaitUntilTerminated, Code) then
  begin
    SetupFailed := True;
    SetupSummary := 'The setup check could not run. Open Cerebro Setup from the Start menu to finish.';
    Exit;
  end;
  DeleteFile(ChoicesFile);
  if LoadStringFromFile(ChangeFileExt(ResultFile, '.txt'), Text) then
    Lines := String(Text)
  else
    Lines := '';
  SetupFailed := Code <> 0;
  SetupSummary := Lines;
end;

{ Close Cerebro before files are replaced: politely first, then firmly. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM Cerebro.exe /IM CerebroWidget.exe /IM CerebroSetupWizard.exe',
       '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(2500);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM Cerebro.exe /IM CerebroWidget.exe /IM CerebroSetupWizard.exe',
       '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if IsUpgrade() then
      WriteResetRequest();
    ApplySetup();
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
  begin
    if Provider() = PROVIDER_LATER then
      WizardForm.FinishedLabel.Caption :=
        'Cerebro is installed. Leave the box below ticked to open the setup wizard, which connects an AI ' +
        'provider and tests it.'
    else if Provider() = PROVIDER_KEEP then
      WizardForm.FinishedLabel.Caption := 'Cerebro is upgraded, with your settings as they were.'
    else if SetupFailed then
      WizardForm.FinishedLabel.Caption :=
        'Cerebro is installed. Some checks need attention - you can fix them any time in Settings:' + #13#10#13#10 +
        SetupSummary
    else
      WizardForm.FinishedLabel.Caption := 'Cerebro is ready. Everything was checked:' + #13#10#13#10 + SetupSummary;
  end;
end;

{ Cerebro keeps its database, logs and settings in %LOCALAPPDATA%\Cerebro,
  deliberately outside the install directory so an upgrade never touches them.
  On uninstall the user chooses whether that goes too - losing an entire case
  history to a routine uninstall would be a nasty surprise. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Folder: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Folder := ExpandConstant('{localappdata}\Cerebro');
    if DirExists(Folder) then
    begin
      if MsgBox('Also delete your Cerebro data?' + #13#10#13#10 +
                'This removes your case history, indexed documents, settings ' +
                'and logs from:' + #13#10 + Folder + #13#10#13#10 +
                'Choose No to keep them for a future reinstall.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Folder, True, True, True);
    end;
  end;
end;
