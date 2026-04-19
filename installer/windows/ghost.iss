; ============================================================
;  Ghost — Windows Installer (Inno Setup 6)
;
;  Build: iscc.exe installer\windows\ghost.iss
;         iscc.exe /DAppVersion=X.Y.Z installer\windows\ghost.iss   ; override version
;
;  Produces: installer\windows\Output\GhostSetup-<version>.exe
;
;  Publisher:  Jesus Oliveira
;  LinkedIn:   https://www.linkedin.com/in/ojesus
;  GitHub:     https://github.com/userJesus
;  Repo:       https://github.com/userJesus/ghost
;  License:    Non-Commercial Source-Available (NCSAL v1.0). See ../LICENSE
; ============================================================

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

#define AppName       "Ghost"
#define AppPublisher  "Jesus Oliveira"
#define AppURL        "https://github.com/userJesus/ghost"
#define AppSupport    "https://github.com/userJesus/ghost/issues"
#define AppUpdates    "https://github.com/userJesus/ghost/releases"
#define AppExeName    "Ghost.exe"

[Setup]
; Unique Windows App ID — do NOT change once released (upgrades rely on it).
AppId={{B9E9B3A4-2F0F-4D6C-9A3B-GHOSTAPP00001}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppSupport}
AppUpdatesURL={#AppUpdates}
AppContact=contato.jesusoliveira@gmail.com
AppComments=Ghost — Assistente desktop de IA. Licenciado para uso NÃO-COMERCIAL.
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription=Ghost Installer
VersionInfoCopyright=Copyright © 2026 Jesus Oliveira
; Default install path: %LocalAppData%\Programs\Ghost (no admin required)
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
; Per-user install — no UAC prompt.
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=GhostSetup-{#AppVersion}
SetupIconFile=..\..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
LicenseFile=..\..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "portuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon"; Description: "Iniciar o Ghost com o Windows"; GroupDescription: "Opções:"; Flags: unchecked

; ------------------------------------------------------------
;  InstallDelete — wipe old install artifacts BEFORE extracting new ones.
;  Why: auto-updates in silent mode sometimes leave orphaned / mismatched
;  DLLs (e.g. libsndfile) when a file was locked at replace time. Those
;  stale bytes caused 'Ghost.exe started but froze / not responding'
;  reports from users on v1.0.10 and earlier. Nuking the _internal tree
;  guarantees a clean slate every install.
; ------------------------------------------------------------
[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: files;          Name: "{app}\*.exe"
Type: files;          Name: "{app}\*.dll"
Type: files;          Name: "{app}\*.pyd"
Type: files;          Name: "{app}\*.manifest"

[Files]
; Pull the entire PyInstaller onedir output.
Source: "..\..\dist\Ghost\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Include the LICENSE alongside the app so users can always find the terms.
Source: "..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Licença (NCSAL)"; Filename: "{app}\LICENSE"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: startupicon

[Run]
; Normal install: the "Launch Ghost" checkbox appears on the Finished page.
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; Silent install (used by the in-app auto-updater): always relaunch Ghost so
; the user isn't left staring at a closed app after an update.
Filename: "{app}\{#AppExeName}"; Flags: nowait runasoriginaluser; Check: WizardSilent

; ------------------------------------------------------------
;  Uninstall: ask whether to purge user data (logs, config, cache).
;  Data path (Windows):  %USERPROFILE%\.ghost
;  e.g. C:\Users\<seu-usuario>\.ghost
; ------------------------------------------------------------
[Code]
// Forcefully terminate any running Ghost / WebView2 processes BEFORE Inno
// Setup tries to replace files. /CLOSEAPPLICATIONS alone isn't enough when
// msedgewebview2.exe children hold DLL handles — those linger a moment
// after the main Ghost.exe exits, so we wipe the tree ourselves.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  NeedsRestart := False;
  // /T = tree, /F = force. Errors are ignored (ResultCode unused).
  Exec('taskkill.exe', '/F /T /IM Ghost.exe',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/F /T /IM msedgewebview2.exe',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  // Give Windows a beat to release any lingering file handles.
  Sleep(500);
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataPath: string;
  Msg: string;
begin
  if CurUninstallStep = usUninstall then
  begin
    DataPath := GetEnv('USERPROFILE') + '\.ghost';
    if DirExists(DataPath) then
    begin
      Msg := 'Deseja também excluir os dados do Ghost (logs, configurações, histórico de conversas, chave da OpenAI)?'
        + #13#10 + #13#10 + 'Local dos dados:' + #13#10 + DataPath
        + #13#10 + #13#10 + 'Escolha:'
        + #13#10 + '  • Sim — remove tudo (limpeza completa).'
        + #13#10 + '  • Não — mantém os dados (reinstalar o Ghost depois restaura tudo).';
      if MsgBox(Msg, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DelTree(DataPath, True, True, True);
      end;
    end;
  end;
end;

// Append the user-data path and developer info to the "Finished" wizard page.
procedure CurPageChanged(CurPageID: Integer);
var
  DataPath: string;
begin
  if CurPageID = wpFinished then
  begin
    DataPath := GetEnv('USERPROFILE') + '\.ghost';
    WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption
      + #13#10 + #13#10 + 'Seus dados (logs, configurações, histórico, chave da OpenAI) ficam em:'
      + #13#10 + DataPath
      + #13#10 + #13#10 + 'Desenvolvido por Jesus Oliveira'
      + #13#10 + 'LinkedIn: linkedin.com/in/ojesus'
      + #13#10 + 'GitHub:   github.com/userJesus';
  end;
end;
