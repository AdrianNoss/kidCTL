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
Start-Service sshd
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force | Out-Null
if (-not (Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -DisplayName "OpenSSH Server (sshd)" -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
}
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
  $is = "$env:TEMP\innosetup.exe"
  Invoke-WebRequest -UseBasicParsing -Uri "https://jrsoftware.org/download.php/is.exe" -OutFile $is
  Start-Process -Wait -FilePath $is -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/ALLUSERS"
} catch { Write-Output "Inno Setup nicht installiert: $_" }

Set-Content -Path "C:\kidctl-provisioned.txt" -Value (Get-Date -Format o)
Stop-Transcript
