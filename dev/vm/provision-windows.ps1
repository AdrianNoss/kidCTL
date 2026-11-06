# kidctl Test-VM (Windows): beim ersten Login von admin ausgeführt (autounattend.xml).
# OpenSSH-Server mit Schlüssel der VM, PowerShell als SSH-Shell, kein Ruhezustand, Inno Setup zum Bauen.
$ErrorActionPreference = "Continue"
$log = "C:\kidctl-provision.log"
Start-Transcript -Path $log -Append

# Kinderkonten (Standardbenutzer), Gruppen per SID
foreach ($u in @(@{n="kind"; f="Kind"}, @{n="kind2"; f="Kind Zwei"})) {
  if (-not (Get-LocalUser -Name $u.n -ErrorAction SilentlyContinue)) {
    New-LocalUser -Name $u.n -FullName $u.f -Password (ConvertTo-SecureString $u.n -AsPlainText -Force) -PasswordNeverExpires | Out-Null
  }
  Add-LocalGroupMember -SID "S-1-5-32-545" -Member $u.n -ErrorAction SilentlyContinue
}
Add-LocalGroupMember -SID "S-1-5-32-544" -Member "admin" -ErrorAction SilentlyContinue

# OpenSSH-Server (Feature bei Bedarf, lädt aus Windows Update)
$cap = Get-WindowsCapability -Online -Name "OpenSSH.Server*" | Select-Object -First 1
if ($cap.State -ne "Installed") { Add-WindowsCapability -Online -Name $cap.Name }
Set-Service sshd -StartupType Automatic
Start-Service sshd -ErrorAction SilentlyContinue
Start-Sleep 5
if ((Get-Service sshd).Status -ne "Running") {
  # Windows 10 22H2: Der Feature-sshd (Stand 2019) startet nicht (beendet sich still, Neustartschleife) –
  # ersetzen durch Win32-OpenSSH als MSI (Version gepinnt). Host-Schlüssel erzeugt der Dienst selbst als
  # SYSTEM; mit „ssh-keygen -A“ als admin erzeugte Schlüssel lehnt er ab („Bad owner“).
  Remove-WindowsCapability -Online -Name $cap.Name | Out-Null
  $msi = "$env:TEMP\OpenSSH-Win64-v10.0.0.0.msi"
  Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/PowerShell/Win32-OpenSSH/releases/download/10.0.0.0p2-Preview/OpenSSH-Win64-v10.0.0.0.msi" -OutFile $msi
  if ((Get-FileHash -Algorithm SHA256 $msi).Hash -eq "DDEC9C53864280759CF9F74791CEFD387100E3946AA849A1C138A4ED1B96B7D9") {
    Start-Process -Wait msiexec -ArgumentList "/i", $msi, "/qn", "ADDLOCAL=Server"
    # sftp-server.exe (für scp) liegt nicht im PATH des Dienstes; der Pfad muss ohne Leerzeichen sein,
    # da sshd ihn über die Shell startet → 8.3-Kurzname
    $cfg = "C:\ProgramData\ssh\sshd_config"
    if (-not (Test-Path $cfg)) { Copy-Item "C:\Program Files\OpenSSH\sshd_config_default" $cfg }
    $sftp = (New-Object -ComObject Scripting.FileSystemObject).GetFile("C:\Program Files\OpenSSH\sftp-server.exe").ShortPath
    (Get-Content $cfg) -replace "^Subsystem\s+sftp\s+.*", "Subsystem sftp $sftp" | Set-Content $cfg -Encoding ascii
    Set-Service sshd -StartupType Automatic
    Start-Service sshd
  } else { Write-Output "OpenSSH-MSI: Prüfsumme falsch" }
}
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force | Out-Null
if (-not (Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -DisplayName "OpenSSH Server (sshd)" -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
}
# Die Regel gilt sonst nur im Profil „Privat“ – das VM-Netz ist „Öffentlich“
Set-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -Profile Any
# Admins: Schlüssel in administrators_authorized_keys, nur SYSTEM und Administratoren (SIDs – Gruppennamen sind lokalisiert)
$keys = "C:\ProgramData\ssh\administrators_authorized_keys"
Set-Content -Path $keys -Value "@SSH_KEY@" -Encoding ascii
icacls $keys /inheritance:r /grant "*S-1-5-32-544:F" /grant "*S-1-5-18:F" | Out-Null

# Kein Ruhezustand / Bildschirmschoner in der VM
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
powercfg /hibernate off

# Inno Setup (Installer bauen, siehe packaging/windows)
try {
  $is = "$env:TEMP\innosetup-6.7.4.exe"
  Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/jrsoftware/issrc/releases/download/is-6_7_4/innosetup-6.7.4.exe" -OutFile $is
  if ((Get-FileHash -Algorithm SHA256 $is).Hash -ne "A549929319F9120716677C685535CA4671E4B937B5FDC87E2585F32D8EB30E8C") { throw "Prüfsumme falsch" }
  Start-Process -Wait -FilePath $is -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/ALLUSERS"
} catch { Write-Output "Inno Setup nicht installiert: $_" }

Set-Content -Path "C:\kidctl-provisioned.txt" -Value (Get-Date -Format o)
Stop-Transcript
