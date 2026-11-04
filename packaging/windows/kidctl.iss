; KidCTL für Windows 10 22H2 / 11 (x64) – Inno Setup 6.
; Gebaut von build.py (Staging-Ordner mit eingebettetem Python, Bibliotheken und kidctl-Code):
;   ISCC /DAppVersion=0.29.0 /DStaging=C:\kidctl-build\staging /DOutputDir=C:\kidctl-build\out kidctl.iss
;
; Installiert nach "C:\Program Files\KidCTL", Zustand unter "C:\ProgramData\KidCTL" (nur SYSTEM und
; Administratoren). Dienst "KidCTL" (LocalSystem, automatischer Start, Neustart bei Absturz, auch im
; abgesicherten Modus), Tray-App im Autostart aller Benutzer, Protokoll kidctl:// für Toast-Knöpfe.
; Stille Installation/Update: /VERYSILENT [/SERVER=https://… /CODE=ABCD-EFGH]

#ifndef AppVersion
  #error AppVersion fehlt (/DAppVersion=…)
#endif
#ifndef Staging
  #define Staging "staging"
#endif
#ifndef OutputDir
  #define OutputDir "out"
#endif

#define Svc "KidCTL"
#define AppUserModelID "KidCTL.Tray"

[Setup]
AppId={{5D7B5D0B-3C0E-4A43-9E3F-6B1C0B8F2A11}
AppName=KidCTL
AppVersion={#AppVersion}
AppVerName=KidCTL {#AppVersion}
AppPublisher=KidCTL
DefaultDirName={autopf}\KidCTL
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19045
OutputDir={#OutputDir}
OutputBaseFilename=KidCTL-Setup-{#AppVersion}
SetupIconFile=kidctl.ico
UninstallDisplayIcon={app}\kidctl.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
SetupLogging=yes
ShowLanguageDialog=auto

[Languages]
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
de.PairTitle=Mit dem KidCTL-Server koppeln
de.PairDesc=Kopplungscode aus dem Eltern-WebUI (Geräte → Koppeln)
de.PairHint=Adresse des Servers (https://…) und Kopplungscode eingeben. Leer lassen, um später als Administrator zu koppeln: "C:\Program Files\KidCTL\kidctl.cmd" enroll https://… CODE
de.PairServer=Server-Adresse:
de.PairCode=Kopplungscode:
de.PairHttps=Die Server-Adresse muss mit https:// beginnen.
de.PairFailed=Kopplung fehlgeschlagen:%n%n%1%n%nSpäter als Administrator erneut koppeln:%n"C:\Program Files\KidCTL\kidctl.cmd" enroll <Server> <Code>
de.TrayName=KidCTL
en.PairTitle=Pair with the KidCTL server
en.PairDesc=Pairing code from the parents' web UI (Devices → Pair)
en.PairHint=Enter the server address (https://…) and the pairing code. Leave empty to pair later as administrator: "C:\Program Files\KidCTL\kidctl.cmd" enroll https://… CODE
en.PairServer=Server address:
en.PairCode=Pairing code:
en.PairHttps=The server address must start with https://.
en.PairFailed=Pairing failed:%n%n%1%n%nPair later as administrator:%n"C:\Program Files\KidCTL\kidctl.cmd" enroll <server> <code>
en.TrayName=KidCTL

[InstallDelete]
; alte Programmdateien vor einem Update entfernen (Zustand liegt unter ProgramData)
Type: filesandordirs; Name: "{app}\lib"
Type: filesandordirs; Name: "{app}\python"

[UninstallDelete]
; zur Laufzeit entstandene Dateien (__pycache__) mit entfernen
Type: filesandordirs; Name: "{app}\lib"
Type: filesandordirs; Name: "{app}\python"

[Files]
Source: "{#Staging}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "kidctl.ico"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{commonappdata}\KidCTL"

[Icons]
Name: "{autoprograms}\KidCTL"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m kidctl_tray --show"; \
  WorkingDir: "{app}\python"; IconFilename: "{app}\kidctl.ico"; AppUserModelID: "{#AppUserModelID}"

[Registry]
; Tray-App im Autostart aller Benutzer (zeigt sich nur bei zugeordneten Kindern)
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "KidCTL"; \
  ValueData: """{app}\python\pythonw.exe"" -m kidctl_tray"; Flags: uninsdeletevalue
; Protokoll kidctl:// (Knöpfe in Toasts)
Root: HKLM; Subkey: "Software\Classes\kidctl"; ValueType: string; ValueName: ""; ValueData: "URL:KidCTL"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Classes\kidctl"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKLM; Subkey: "Software\Classes\kidctl\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\kidctl.ico"
Root: HKLM; Subkey: "Software\Classes\kidctl\shell\open\command"; ValueType: string; ValueName: ""; \
  ValueData: """{app}\python\pythonw.exe"" -m kidctl_tray ""%1"""
; Dienst auch im abgesicherten Modus
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\SafeBoot\Minimal\{#Svc}"; ValueType: string; ValueName: ""; ValueData: "Service"; Flags: uninsdeletekey
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\SafeBoot\Network\{#Svc}"; ValueType: string; ValueName: ""; ValueData: "Service"; Flags: uninsdeletekey

[Code]
var
  PairPage: TInputQueryWizardPage;

function StateDir(): String;
begin
  Result := ExpandConstant('{commonappdata}\KidCTL');
end;

function Paired(): Boolean;
begin
  Result := FileExists(StateDir() + '\state\device.json');
end;

function Python(): String;
begin
  Result := ExpandConstant('{app}\python\python.exe');
end;

function Run(const FileName, Params: String): Integer;
var
  Code: Integer;
begin
  if not Exec(FileName, Params, '', SW_HIDE, ewWaitUntilTerminated, Code) then
    Code := -1;
  Log(Format('%s %s -> %d', [FileName, Params, Code]));
  Result := Code;
end;

procedure StopKidCTL();
begin
  { Dienst stoppen und Tray-Apps aus dem Installationsordner beenden (Dateien werden ersetzt) }
  Run(ExpandConstant('{sys}\sc.exe'), 'stop {#Svc}');
  Run(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      '-NoProfile -Command "$d = ''' + ExpandConstant('{app}') + '\''; for ($i = 0; $i -lt 30; $i++) { ' +
      '$p = Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($d, ''OrdinalIgnoreCase'') }; ' +
      'if (-not $p) { break }; $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep -Milliseconds 500 }"');
end;

procedure InitializeWizard();
begin
  PairPage := CreateInputQueryPage(wpWelcome, CustomMessage('PairTitle'), CustomMessage('PairDesc'), CustomMessage('PairHint'));
  PairPage.Add(CustomMessage('PairServer'), False);
  PairPage.Add(CustomMessage('PairCode'), False);
  PairPage.Values[0] := ExpandConstant('{param:SERVER|}');
  PairPage.Values[1] := ExpandConstant('{param:CODE|}');
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = PairPage.ID) and Paired();
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = PairPage.ID) and (Trim(PairPage.Values[1]) <> '') and (Pos('https://', Lowercase(Trim(PairPage.Values[0]))) <> 1) then
  begin
    MsgBox(CustomMessage('PairHttps'), mbError, MB_OK);
    Result := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopKidCTL();
  Result := '';
end;

procedure Enroll();
var
  Server, Code, OutFile: String;
  Output: AnsiString;
  Rc: Integer;
begin
  Server := Trim(PairPage.Values[0]);
  Code := Trim(PairPage.Values[1]);
  if (Code = '') or (Server = '') or Paired() then
    exit;
  OutFile := ExpandConstant('{tmp}\enroll.txt');
  Rc := Run(ExpandConstant('{cmd}'), '/C ""' + Python() + '" -m kidctl_agent enroll "' + Server + '" "' + Code + '" > "' + OutFile + '" 2>&1"');
  if Rc <> 0 then
  begin
    if not LoadStringFromFile(OutFile, Output) then
      Output := '';
    if not WizardSilent() then
      MsgBox(FmtMessage(CustomMessage('PairFailed'), [String(Output)]), mbError, MB_OK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Bin: String;
begin
  if CurStep = ssPostInstall then
  begin
    { Zustand nur für SYSTEM und Administratoren (sonst könnten Benutzer z. B. Updates austauschen) }
    ForceDirectories(StateDir());
    { Nur der Ordner bekommt eigene Rechte; alles darin erbt sie (/reset). Kein /T mit (OI)(CI) – das ließe
      vorhandene Dateien ohne jeden Eintrag zurück (beim Update beobachtet). }
    Run(ExpandConstant('{sys}\icacls.exe'), '"' + StateDir() + '" /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F /C /Q');
    Run(ExpandConstant('{sys}\icacls.exe'), '"' + StateDir() + '\*" /reset /T /C /Q');
    Bin := '"\"' + Python() + '\" -m kidctl_agent service"';
    if Run(ExpandConstant('{sys}\sc.exe'), 'create {#Svc} binPath= ' + Bin + ' start= auto DisplayName= "KidCTL"') <> 0 then
      Run(ExpandConstant('{sys}\sc.exe'), 'config {#Svc} binPath= ' + Bin + ' start= auto');
    Run(ExpandConstant('{sys}\sc.exe'), 'description {#Svc} "KidCTL: Bildschirmzeit und Kinderschutz"');
    Run(ExpandConstant('{sys}\sc.exe'), 'failure {#Svc} reset= 86400 actions= restart/5000/restart/5000/restart/30000');
    Run(ExpandConstant('{sys}\sc.exe'), 'failureflag {#Svc} 1');
    Enroll();
    Run(ExpandConstant('{sys}\sc.exe'), 'start {#Svc}');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    { von KidCTL deaktivierte Konten wieder aktivieren, dann Dienst entfernen }
    Run(Python(), '-m kidctl_agent unlock-all');
    StopKidCTL();
    Run(ExpandConstant('{sys}\sc.exe'), 'delete {#Svc}');
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    DelTree(StateDir(), True, True, True);
    RemoveDir(ExpandConstant('{app}'));   { bleibt sonst leer zurück }
  end;
end;
