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
; Normal (visible) install: the "Launch Ghost" checkbox appears on the
; Finished page — user decides whether to launch.
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
;
; NOTE: silent install launch is NOT here. Pre-v1.1.21 used
;   Filename: "{app}\..."; Flags: nowait runasoriginaluser; Check: WizardSilent
; but in practice that entry silently skipped on at least one machine.
; Launch during silent install is now done programmatically in the
; [Code] section via `CurStepChanged(ssDone)` — see the block at the
; bottom of this file. Pascal's `Exec` is unambiguous and works
; regardless of Inno Setup's internal `[Run]` gating logic.

; ============================================================
;  [Code] — Pascal Script for robust process cleanup + UX messaging.
;
;  Centralized `KillAllGhostProcesses` kills every Ghost-related image
;  name with retry + verification. Called from three points:
;    (1) PrepareToInstall  — before file extraction starts
;    (2) CurStepChanged(ssPostInstall) — after extraction, before [Run]
;    (3) CurUninstallStepChanged(usUninstall) — before purging {app}
;
;  UX: status messages shown via WizardForm.StatusLabel (no console
;  windows, no PowerShell visible). If the user is updating (an existing
;  install is detected) we say "Atualizando para versão X.Y.Z"; fresh
;  install says "Instalando".
;
;  All taskkill calls use SW_HIDE so their console window never appears.
;  Orphan tempdir sweep is Pascal-native (Inno's TFindRec API) — no
;  PowerShell shell-out, nothing visible to the user.
; ============================================================
[Code]
const
  // taskkill exit codes used as signals.
  //   0   = success (one or more processes terminated)
  //   128 = no matching processes found — also treat as SUCCESS, our goal is
  //         "nothing is running"
  TASKKILL_OK        = 0;
  TASKKILL_NOT_FOUND = 128;
  // Up to this many kill+verify rounds per image name before we give up.
  // Each round costs ~300ms on a "nothing to kill" case, ~800ms on a real
  // kill (process teardown + file-handle release). Total worst case ~5s.
  MAX_KILL_ROUNDS = 6;

// Cached detection of "is this a fresh install or an upgrade".
// IMPORTANT: the `{app}` constant is NOT available during InitializeSetup
// or InitializeWizard — it only becomes valid after Inno resolves the
// destination directory (right before PrepareToInstall). So we must
// capture the "was it there before extraction" state INSIDE
// PrepareToInstall, not earlier. Setting it earlier crashes the installer
// with "Runtime error: An attempt was made to expand the 'app' constant
// before it was initialized." (Pascal Script initializes Booleans to
// False, so the default is "fresh install" until PrepareToInstall
// says otherwise.)
var
  ExistingInstallDetected: Boolean;


// ------------------------------------------------------------------
// Write a timestamped line to ~/.ghost/install-launch.log so
// post-mortem ("installer ran but Ghost never opened") has evidence.
// Uses Win32 API via a temp shell command because Inno Pascal's file
// I/O is limited. Best-effort — any failure is swallowed silently.
// ------------------------------------------------------------------
procedure WriteDebugLog(const Msg: String);
var
  LogPath: String;
  Line: AnsiString;
  F: Integer;
begin
  try
    LogPath := ExpandConstant('{userprofile}') + '\.ghost\install-launch.log';
    // Use Inno's FileAppend via `SaveStringToFile` (append=True)
    Line := AnsiString('[' + GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':') +
                       '] ' + Msg + #13#10);
    SaveStringToFile(LogPath, Line, True);
  except
    // logging failure is non-fatal, don't surface it
  end;
end;

// ------------------------------------------------------------------
// Tries to terminate every process matching ImageName (force + tree).
// Returns True only when taskkill reports "no process found" — i.e. the
// image is confirmed gone from the system. Retries up to MAX_KILL_ROUNDS
// times to absorb fast respawns and slow OS cleanup.
// ------------------------------------------------------------------
function KillProcessByName(const ImageName: String): Boolean;
var
  ResultCode: Integer;
  Round: Integer;
begin
  Result := False;
  for Round := 1 to MAX_KILL_ROUNDS do
  begin
    ResultCode := 0;
    // /F = force, /T = kill tree, /IM = by image name.
    // SW_HIDE prevents the console window of taskkill.exe from flashing.
    Exec('taskkill.exe', '/F /T /IM ' + ImageName,
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

    if ResultCode = TASKKILL_NOT_FOUND then
    begin
      // Verified clean — no process of this name is running.
      Result := True;
      Exit;
    end;

    // Either we just killed something (ResultCode=0) or taskkill errored
    // for another reason. Either way, wait a beat and re-check.
    // 300ms is empirically enough for Windows to release file handles
    // from a killed child process on modern hardware.
    Sleep(300);
  end;
  // Fell through all rounds with processes still matching — treat as
  // failure, caller will log and retry the whole sweep.
end;

// ------------------------------------------------------------------
// Kill EVERY Ghost-related image name with retry + verification.
// Returns True when ALL four images are confirmed absent.
//
// StatusCaption: optional string shown in WizardForm.StatusLabel so the
// user sees progress ("Encerrando processos..." rather than silence).
// Pass '' to skip the UI update (useful during wizard phases where
// StatusLabel is not the visible label).
// ------------------------------------------------------------------
function KillAllGhostProcesses(const StatusCaption: String): Boolean;
var
  Attempt: Integer;
  AllGone: Boolean;
begin
  if (StatusCaption <> '') and (WizardForm <> nil) and
     (WizardForm.StatusLabel <> nil) then
    WizardForm.StatusLabel.Caption := StatusCaption;

  // We loop the WHOLE sweep up to 3 times. Reason: msedgewebview2 children
  // can be respawned by the WebView2 runtime if we kill Ghost.exe first
  // and WebView2 had queued a child-spawn on its own message pump. The
  // second full pass catches those late arrivals.
  for Attempt := 1 to 3 do
  begin
    AllGone := True;
    if not KillProcessByName('Ghost.exe')                   then AllGone := False;
    if not KillProcessByName('msedgewebview2.exe')          then AllGone := False;
    if not KillProcessByName('WebView2Host.exe')            then AllGone := False;
    if not KillProcessByName('CefSharp.BrowserSubprocess.exe') then AllGone := False;

    if AllGone then
    begin
      // Final 500ms so the OS can finish releasing file-locks on
      // Ghost.exe + _internal/*.dll + the webview-cache folder.
      Sleep(500);
      Result := True;
      Exit;
    end;

    // At least one image still had live processes after a full pass.
    // Give Windows an extra 500ms and try the whole sweep again.
    Sleep(500);
  end;

  // Three full passes and something is still alive. Return False so the
  // caller can decide whether to abort or proceed cautiously.
  Result := False;
end;

// ------------------------------------------------------------------
// Pascal-native replacement for the old PowerShell orphan-tmp sweep.
//
// pywebview's default behavior before 1.1.x was to create a fresh
// `%TEMP%\tmp<random>\EBWebView\` folder per session and never clean it
// up. Ghost 1.1.x pins its cache to `~/.ghost/webview-cache` instead, BUT
// orphan tmp dirs from prior versions still exist on users' machines and
// can cause WebView2 to walk them at init. We sweep them here.
//
// Conservative: only touch dirs that (a) match the tempfile mkdtemp
// pattern `tmp*` AND (b) contain an `EBWebView` subdir (pywebview's
// fingerprint). Any other tempdir is ignored.
//
// Uses Inno's built-in TFindRec API — no external processes, no
// PowerShell window, nothing user-visible.
// ------------------------------------------------------------------
procedure SweepOrphanWebViewCaches;
var
  FindRec: TFindRec;
  TmpRoot, Candidate: String;
begin
  TmpRoot := GetEnv('TEMP');
  if TmpRoot = '' then
    TmpRoot := GetEnv('TMP');
  if (TmpRoot = '') or not DirExists(TmpRoot) then
    Exit;

  if not FindFirst(TmpRoot + '\tmp*', FindRec) then
    Exit;

  try
    repeat
      // FILE_ATTRIBUTE_DIRECTORY = $10 — directories only.
      if (FindRec.Attributes and $10) <> 0 then
      begin
        Candidate := TmpRoot + '\' + FindRec.Name;
        // pywebview signature: the tmp dir contains an `EBWebView` subdir.
        if DirExists(Candidate + '\EBWebView') then
        begin
          // DelTree(Dir, ContentsOnly, DeleteFiles, DeleteDirs) — all True
          // means fully recursive + remove the root dir itself. Swallows
          // errors (returns False) for files still locked; we'll try again
          // on the next install.
          DelTree(Candidate, True, True, True);
        end;
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

// ------------------------------------------------------------------
// True if a previous Ghost is already installed at {app}. Used to pick
// between "Instalando..." and "Atualizando para versão X.Y.Z..."
// wording for the wizard caption.
//
// SAFE to call only from PrepareToInstall onwards — the `{app}` constant
// is not valid earlier in the Inno Setup lifecycle.
// ------------------------------------------------------------------
function IsExistingInstall: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\{#AppExeName}'));
end;

// ------------------------------------------------------------------
// Context-aware caption for the install-progress page.
// Reads the ExistingInstallDetected flag that PrepareToInstall sets.
// ------------------------------------------------------------------
function InstallActionCaption: String;
begin
  if ExistingInstallDetected then
    Result := 'Atualizando para versão {#AppVersion}...'
  else
    Result := 'Instalando...';
end;

// ------------------------------------------------------------------
// Phase 1: before extraction starts.
//
// This is the first callback in the Inno Setup lifecycle where the
// `{app}` constant is guaranteed to be valid. We do three things:
//   (a) snapshot "was Ghost already installed" for later UX decisions
//   (b) kill any currently-running Ghost + webview2 helpers, with
//       retry + verification
//   (c) show the user a friendly status so they know what's happening
//
// Any non-empty return value aborts setup with that message as an
// error dialog. We return '' unconditionally — even if a stubborn
// helper survives the kill, the post-extract pass tries again, and
// Inno's [InstallDelete] + built-in file-locked retry logic will
// handle stragglers.
// ------------------------------------------------------------------
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  NeedsRestart := False;
  // (a) Snapshot upgrade-vs-fresh. Must be done BEFORE [InstallDelete]
  //     wipes the old Ghost.exe or the flag would always read False.
  ExistingInstallDetected := IsExistingInstall;
  // (b)+(c) Robust kill with UI feedback.
  KillAllGhostProcesses('Encerrando processos da versão anterior...');
  Result := '';
end;

// ------------------------------------------------------------------
// Phase 2 & 3: mid-install wizard status + post-extract cleanup.
// ------------------------------------------------------------------
procedure CurStepChanged(CurStep: TSetupStep);
var
  GhostExe: String;
  ResultCode: Integer;
begin
  if CurStep = ssInstall then
  begin
    // Show "Instalando..." or "Atualizando..." over the file-extraction
    // progress. This is what the user actually reads during the 1-2s
    // extraction window.
    if WizardForm <> nil then
      WizardForm.StatusLabel.Caption := InstallActionCaption;
  end
  else if CurStep = ssPostInstall then
  begin
    // Belt-and-suspenders: extraction is done, but msedgewebview2 could
    // have respawned if another WebView2 host (Teams, Outlook, an orphan
    // from a previous user session) kept its runtime warm. Re-sweep.
    KillAllGhostProcesses('Preparando o ambiente...');

    // Silently sweep orphan pywebview WebView2 UserData caches. Pascal-
    // native, no PowerShell window.
    if WizardForm <> nil then
      WizardForm.StatusLabel.Caption := 'Preparando o ambiente...';
    SweepOrphanWebViewCaches;

    // Final validation: if Ghost.exe is somehow still alive now, do one
    // more kill pass. The ssDone launch below is about to spawn the NEW
    // Ghost, and we must not race an old instance holding the
    // single-instance mutex.
    KillAllGhostProcesses('');
  end
  else if CurStep = ssDone then
  begin
    // ── Silent-install auto-launch ────────────────────────────────
    // Pre-v1.1.21 this was handled by an [Run] entry gated on
    // `Check: WizardSilent`. In practice that entry silently SKIPPED
    // on at least one user's machine (investigated v1.1.20 install →
    // Ghost.exe never started after installer exited). Rather than
    // debug Inno Setup's internal launch gating, we do it ourselves
    // here.
    //
    // v1.1.21 tried `Exec(...)` (CreateProcess path) but on the same
    // user's box that ALSO didn't launch Ghost. v1.1.22 uses
    // `ShellExec` (ShellExecuteEx path) as the primary, falling back
    // to `Exec` — different Win32 API paths have different handling
    // for UAC inheritance, working dirs, and process-group association,
    // so having both available covers edge cases.
    //
    // Also writes a launch-attempt marker to ~/.ghost/install-launch.log
    // so POST-MORTEM of "Ghost didn't open after install" has evidence.
    //
    // Visible (non-silent) installs already use the [Run] entry with
    // `postinstall skipifsilent` flags, which surfaces a "Launch Ghost"
    // checkbox on the Finished page. We don't duplicate that here.
    if WizardSilent then
    begin
      GhostExe := ExpandConstant('{app}\{#AppExeName}');
      WriteDebugLog('ssDone silent launch | exe=' + GhostExe +
                    ' | exists=' + IntToStr(Ord(FileExists(GhostExe))));
      if FileExists(GhostExe) then
      begin
        // Primary attempt: ShellExec ("open" verb). This is what happens
        // when user double-clicks in Explorer — most compatible path.
        if ShellExec('open', GhostExe, '', ExpandConstant('{app}'),
                     SW_SHOWNORMAL, ewNoWait, ResultCode) then
        begin
          WriteDebugLog('ssDone launched via ShellExec, rc=' +
                        IntToStr(ResultCode));
        end
        else
        begin
          WriteDebugLog('ssDone ShellExec FAILED (rc=' +
                        IntToStr(ResultCode) + '); falling back to Exec');
          // Fallback: Exec (CreateProcess path).
          Exec(GhostExe, '', ExpandConstant('{app}'),
               SW_SHOWNORMAL, ewNoWait, ResultCode);
          WriteDebugLog('ssDone Exec fallback rc=' + IntToStr(ResultCode));
        end;
      end
      else
      begin
        WriteDebugLog('ssDone SKIPPED: Ghost.exe not at ' + GhostExe);
      end;
    end
    else
    begin
      WriteDebugLog('ssDone: WizardSilent=False, not auto-launching');
    end;
  end;
end;

// ------------------------------------------------------------------
// Uninstall: close user data prompt (unchanged behavior) + ensure
// Ghost is fully terminated before we start deleting {app}.
// ------------------------------------------------------------------
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataPath: string;
  Msg: string;
  DummyResultCode: Integer;
  Round: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    // Inno's uninstaller doesn't have WizardForm.StatusLabel, so we use
    // bare taskkill with SW_HIDE (no UI interruption). Same retry pattern
    // as KillAllGhostProcesses but inlined because WizardForm is nil here.
    for Round := 1 to 3 do
    begin
      Exec('taskkill.exe', '/F /T /IM Ghost.exe',                   '', SW_HIDE, ewWaitUntilTerminated, DummyResultCode);
      Exec('taskkill.exe', '/F /T /IM msedgewebview2.exe',          '', SW_HIDE, ewWaitUntilTerminated, DummyResultCode);
      Exec('taskkill.exe', '/F /T /IM WebView2Host.exe',            '', SW_HIDE, ewWaitUntilTerminated, DummyResultCode);
      Exec('taskkill.exe', '/F /T /IM CefSharp.BrowserSubprocess.exe', '', SW_HIDE, ewWaitUntilTerminated, DummyResultCode);
      Sleep(400);
    end;

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

// ------------------------------------------------------------------
// "Finished" wizard page: show data path + developer info + context-
// aware success headline.
// ------------------------------------------------------------------
procedure CurPageChanged(CurPageID: Integer);
var
  DataPath, Headline: string;
begin
  if CurPageID = wpFinished then
  begin
    if ExistingInstallDetected then
      Headline := 'Atualização concluída — Ghost {#AppVersion}'
    else
      Headline := 'Instalação concluída — Ghost {#AppVersion}';

    DataPath := GetEnv('USERPROFILE') + '\.ghost';
    WizardForm.FinishedHeadingLabel.Caption := Headline;
    WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption
      + #13#10 + #13#10 + 'Seus dados (logs, configurações, histórico, chave da OpenAI) ficam em:'
      + #13#10 + DataPath
      + #13#10 + #13#10 + 'Desenvolvido por Jesus Oliveira'
      + #13#10 + 'LinkedIn: linkedin.com/in/ojesus'
      + #13#10 + 'GitHub:   github.com/userJesus';
  end;
end;
