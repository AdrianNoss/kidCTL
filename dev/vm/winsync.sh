#!/usr/bin/env bash
# kidctl-Code aus dem Arbeitsbaum in die installierte Windows-VM kopieren und den Dienst neu starten
# (schneller als Installer bauen; der Update-Schlüssel der Installation bleibt erhalten).
#   KIDCTL_WIN=win11 dev/vm/winsync.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
tar --exclude=__pycache__ --exclude=tests -cf "$TMP/lib.tar" -C "$REPO/agent" kidctl_agent -C "$REPO/common" kidctl_common \
    -C "$REPO/tray" kidctl_tray
"$REPO/dev/vm/winvm.sh" put "$TMP/lib.tar" C:/Users/admin/lib.tar
"$REPO/dev/vm/winvm.sh" ssh '$k = Get-Content "C:\Program Files\KidCTL\lib\kidctl_agent\win\update-key.pub";
  Stop-Service KidCTL -ErrorAction SilentlyContinue;
  Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like "C:\Program Files\KidCTL\*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force };
  tar -xf C:\Users\admin\lib.tar -C "C:\Program Files\KidCTL\lib";
  Set-Content "C:\Program Files\KidCTL\lib\kidctl_agent\win\update-key.pub" $k;
  Start-Service KidCTL; Start-Sleep 3; "Dienst: " + (Get-Service KidCTL).Status'
