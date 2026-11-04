#!/usr/bin/env bash
# kidctl Test-VM Windows 11 Home / Windows 10 Pro unter QEMU/KVM (UEFI, TPM 2.0 per swtpm).
#
#   winvm.sh create   [--apply]   Disk, UEFI-Variablen, TPM, Antwortdatei-ISO anlegen
#   winvm.sh start    [--headless]  beim ersten Start: Installation (unbeaufsichtigt, 20–40 min)
#   winvm.sh wait                 warten, bis Installation + Einrichtung fertig sind (SSH)
#   winvm.sh stop                 herunterfahren
#   winvm.sh status
#   winvm.sh ssh      [befehl …]  als admin (PowerShell)
#   winvm.sh put      DATEI… ZIEL  Dateien in die VM kopieren (scp, Ziel z. B. C:/kidctl/)
#   winvm.sh get      QUELLE ZIEL  Datei aus der VM holen
#   winvm.sh snapshot NAME [--apply]   (VM muss aus sein)
#   winvm.sh restore  NAME [--apply]
#   winvm.sh snapshots
#
# Ändernde Befehle laufen ohne --apply nur als Dry-Run.
# Umgebung: KIDCTL_WIN (win11 | win10, Standard win11),
#           KIDCTL_VM_DIR (Standard ~/.local/share/kidctl-vm-<win>), darin install.iso (vom Nutzer geladen),
#           KIDCTL_VM_ISO (anderer Pfad zum ISO), KIDCTL_VM_ISO_SHA256 (optional: Prüfsumme des ISO),
#           KIDCTL_VM_RAM (6G), KIDCTL_VM_CPUS (4), KIDCTL_VM_DISK (80G), KIDCTL_VM_SSH_PORT (2226 win11, 2227 win10)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERE="$REPO/dev/vm"
WIN="${KIDCTL_WIN:-win11}"
# Allgemeine Installationsschlüssel von Microsoft (wählen nur die Edition, aktivieren nichts)
case "$WIN" in
  win11) EDITION="Windows 11 Home"; PRODUCT_KEY="YTMG3-N6DKC-DKB77-7M9GH-8HVX7"; DEF_PORT=2226 ;;
  win10) EDITION="Windows 10 Pro";  PRODUCT_KEY="VK7JG-NPHTM-C97JM-9MPGT-3V66T"; DEF_PORT=2227 ;;
  *) echo "Unbekannt: KIDCTL_WIN=$WIN (win11 | win10)" >&2; exit 1 ;;
esac
VM_DIR="${KIDCTL_VM_DIR:-$HOME/.local/share/kidctl-vm-$WIN}"
ISO="${KIDCTL_VM_ISO:-$VM_DIR/install.iso}"
SSH_PORT="${KIDCTL_VM_SSH_PORT:-$DEF_PORT}"
RAM="${KIDCTL_VM_RAM:-6G}"
CPUS="${KIDCTL_VM_CPUS:-4}"
DISK_SIZE="${KIDCTL_VM_DISK:-80G}"

OVMF_CODE=/usr/share/edk2/x64/OVMF_CODE.secboot.4m.fd
OVMF_VARS_TEMPLATE=/usr/share/edk2/x64/OVMF_VARS.4m.fd
DISK="$VM_DIR/disk.qcow2"
VARS="$VM_DIR/OVMF_VARS.4m.fd"
TPM_DIR="$VM_DIR/tpm"
UNATTEND="$VM_DIR/unattend.iso"
KEY="$VM_DIR/id_ed25519"
PIDFILE="$VM_DIR/qemu.pid"
QMP="$VM_DIR/qmp.sock"
LOG="$VM_DIR/qemu.log"
INSTALLED="$VM_DIR/.installed"

APPLY=0
ARGS=()
for a in "$@"; do
  case "$a" in
    --apply) APPLY=1 ;;
    *) ARGS+=("$a") ;;
  esac
done
set -- "${ARGS[@]+"${ARGS[@]}"}"

die() { echo "Fehler: $*" >&2; exit 1; }
info() { echo "==> $*"; }
run() {
  if (( APPLY )); then "$@"; else printf '[dry-run]'; printf ' %q' "$@"; echo; fi
}
running() { [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

need() {
  local missing=() b
  for b in "$@"; do command -v "$b" >/dev/null || missing+=("$b"); done
  if (( ${#missing[@]} )); then
    echo "Fehlende Programme: ${missing[*]}" >&2
    echo "Installieren mit:  sudo pacman -S --needed qemu-desktop edk2-ovmf swtpm libisoburn openssh" >&2
    exit 1
  fi
}

ssh_opts() {
  echo -i "$KEY" -p "$SSH_PORT" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
       -o UserKnownHostsFile="$VM_DIR/known_hosts" -o ConnectTimeout=5
}

qmp_cmd() {   # qmp_cmd '<json>' …
  python3 - "$QMP" "$@" <<'PY'
import json, socket, sys
s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); f = s.makefile("rw")
f.readline()
for cmd in [json.dumps({"execute": "qmp_capabilities"})] + sys.argv[2:]:
    f.write(cmd + "\n"); f.flush(); f.readline()
PY
}

# ---------- create ----------

cmd_create() {
  need qemu-img qemu-system-x86_64 swtpm xorriso ssh-keygen sha256sum
  [[ -e "$DISK" ]] && die "$DISK existiert bereits. Zum Neuaufsetzen $VM_DIR selbst leeren (install.iso behalten)."
  [[ -w /dev/kvm ]] || die "/dev/kvm nicht beschreibbar."
  [[ -f "$OVMF_CODE" && -f "$OVMF_VARS_TEMPLATE" ]] || die "OVMF fehlt (Paket edk2-ovmf)."
  [[ -f "$ISO" ]] || die "Windows-ISO fehlt: $ISO (bei Microsoft laden, $EDITION ist darin enthalten)."
  if [[ -n "${KIDCTL_VM_ISO_SHA256:-}" ]]; then
    info "Prüfsumme des ISO prüfen"
    [[ "$(sha256sum "$ISO" | awk '{print $1}')" == "${KIDCTL_VM_ISO_SHA256,,}" ]] || die "Prüfsumme des ISO stimmt nicht."
  fi

  info "VM-Verzeichnis: $VM_DIR ($EDITION)"
  run mkdir -p "$VM_DIR" "$TPM_DIR"
  info "Disk ($DISK_SIZE), UEFI-Variablen"
  run qemu-img create -f qcow2 "$DISK" "$DISK_SIZE"
  run cp "$OVMF_VARS_TEMPLATE" "$VARS"
  info "SSH-Schlüssel nur für diese VM"
  [[ -f "$KEY" ]] || run ssh-keygen -q -t ed25519 -N "" -C "kidctl-$WIN" -f "$KEY"

  info "Antwortdatei-ISO (autounattend.xml + provision.ps1)"
  if (( APPLY )); then
    local tmp; tmp="$(mktemp -d)"
    mkdir -p "$tmp/kidctl"
    sed -e "s|@PRODUCT_KEY@|$PRODUCT_KEY|" -e "s|@HOSTNAME@|KIDCTL-${WIN^^}|" \
      "$HERE/autounattend.xml.tmpl" > "$tmp/autounattend.xml"
    sed -e "s|@SSH_KEY@|$(cat "$KEY.pub")|" "$HERE/provision-windows.ps1" > "$tmp/kidctl/provision.ps1"
    xorriso -as genisoimage -quiet -output "$UNATTEND" -volid KIDCTL -joliet -rock "$tmp"
    rm -rf "$tmp"
  else
    echo "[dry-run] $HERE/autounattend.xml.tmpl + provision-windows.ps1 -> $UNATTEND"
  fi
  (( APPLY )) && info "Fertig. Weiter mit: KIDCTL_WIN=$WIN $0 start" || info "Dry-Run – mit --apply ausführen."
}

# ---------- start/stop/status ----------

start_tpm() {
  swtpm socket --tpmstate dir="$TPM_DIR" --ctrl type=unixio,path="$TPM_DIR/sock" --tpm2 \
    --log file="$TPM_DIR/swtpm.log" --terminate --daemon
  for _ in $(seq 20); do [[ -S "$TPM_DIR/sock" ]] && return; sleep 0.2; done
  die "swtpm ist nicht gestartet (siehe $TPM_DIR/swtpm.log)."
}

cmd_start() {
  need qemu-system-x86_64 swtpm
  [[ -f "$DISK" ]] || die "Keine VM angelegt – erst 'KIDCTL_WIN=$WIN $0 create --apply'."
  running && die "VM läuft bereits (PID $(cat "$PIDFILE"))."
  local display=(-vga std -display gtk,zoom-to-fit=on)
  case "${1:-}" in
    --headless) display=(-vga std -display none) ;;
    "") ;;
    *) die "Unbekannte Option: $1" ;;
  esac
  local cdroms=()
  if [[ ! -f "$INSTALLED" ]]; then
    [[ -f "$ISO" ]] || die "Windows-ISO fehlt: $ISO"
    cdroms=(-drive file="$ISO",media=cdrom,readonly=on -drive file="$UNATTEND",media=cdrom,readonly=on)
  fi
  rm -f "$QMP" "$TPM_DIR/sock"
  start_tpm
  info "Starte $EDITION (RAM $RAM, $CPUS CPUs, SSH auf 127.0.0.1:$SSH_PORT, Log $LOG)"
  setsid qemu-system-x86_64 \
    -name "kidctl-$WIN" -machine q35,accel=kvm,smm=on -global driver=cfi.pflash01,property=secure,value=on \
    -cpu host,hv_relaxed,hv_spinlocks=0x1fff,hv_vapic,hv_time -smp "$CPUS" -m "$RAM" -rtc base=localtime \
    -drive if=pflash,format=raw,readonly=on,file="$OVMF_CODE" -drive if=pflash,format=raw,file="$VARS" \
    -chardev socket,id=chrtpm,path="$TPM_DIR/sock" -tpmdev emulator,id=tpm0,chardev=chrtpm -device tpm-crb,tpmdev=tpm0 \
    -device ahci,id=ahci -drive file="$DISK",if=none,id=disk0,format=qcow2,discard=unmap \
    -device ide-hd,drive=disk0,bus=ahci.0,bootindex=1 \
    "${cdroms[@]}" \
    -nic user,model=e1000e,hostfwd=tcp:127.0.0.1:"$SSH_PORT"-:22 \
    -device qemu-xhci -device usb-tablet \
    "${display[@]}" \
    -qmp unix:"$QMP",server=on,wait=off -pidfile "$PIDFILE" \
    >"$LOG" 2>&1 < /dev/null &
  sleep 2
  running || { cat "$LOG" >&2; die "QEMU ist nicht gestartet (siehe oben)."; }
  if [[ ! -f "$INSTALLED" ]]; then
    # „Drücken Sie eine beliebige Taste, um von CD zu starten“ – ein paar Sekunden lang Enter senden
    info "Erster Start: Installation von CD (Taste wird automatisch gedrückt)"
    for _ in $(seq 12); do
      qmp_cmd '{"execute":"send-key","arguments":{"keys":[{"type":"qcode","data":"ret"}]}}' 2>/dev/null || true
      sleep 1
    done
    info "Installation läuft unbeaufsichtigt (20–40 min). Danach: KIDCTL_WIN=$WIN $0 wait"
  else
    info "Läuft."
  fi
}

cmd_stop() {
  running || { info "VM läuft nicht."; return; }
  if [[ -f "$KEY" ]] && ssh $(ssh_opts) -o BatchMode=yes admin@127.0.0.1 "Stop-Computer -Force" 2>/dev/null; then
    info "Herunterfahren per SSH angestoßen"
  else
    info "Sende ACPI-Shutdown"
    qmp_cmd '{"execute":"system_powerdown"}'
  fi
  for _ in $(seq 120); do running || { info "VM aus."; return; }; sleep 1; done
  die "VM reagiert nicht – ggf. 'kill $(cat "$PIDFILE")'."
}

ssh_ok() { ssh $(ssh_opts) -o BatchMode=yes admin@127.0.0.1 "exit 0" 2>/dev/null; }

cmd_status() {
  if running; then
    echo "VM läuft (PID $(cat "$PIDFILE"))"
    if ssh_ok; then
      echo "SSH erreichbar"
      ssh $(ssh_opts) admin@127.0.0.1 'if (Test-Path C:\kidctl-provisioned.txt) { "Einrichtung: fertig" } else { "Einrichtung: läuft (C:\kidctl-provision.log)" }'
    else
      echo "SSH noch nicht erreichbar (Installation läuft?)"
    fi
  else
    echo "VM läuft nicht"
  fi
}

cmd_wait() {
  running || die "VM läuft nicht."
  info "Warte auf SSH (Installation + Einrichtung) …"
  until ssh_ok; do sleep 15; done
  info "Warte auf Abschluss der Einrichtung …"
  until ssh $(ssh_opts) -o BatchMode=yes admin@127.0.0.1 'if (Test-Path C:\kidctl-provisioned.txt) { exit 0 } else { exit 1 }' 2>/dev/null; do
    sleep 15
  done
  touch "$INSTALLED"
  info "Fertig. Installations-ISOs werden ab dem nächsten Start nicht mehr eingehängt."
  info "Empfehlung: '$0 stop' und '$0 snapshot basis --apply'."
}

cmd_ssh() {
  [[ -f "$KEY" ]] || die "Kein SSH-Schlüssel – VM angelegt?"
  exec ssh $(ssh_opts) admin@127.0.0.1 "$@"
}

scp_opts() { echo -i "$KEY" -P "$SSH_PORT" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
                  -o UserKnownHostsFile="$VM_DIR/known_hosts"; }

cmd_put() {
  (( $# >= 2 )) || die "Aufruf: $0 put DATEI… ZIEL"
  local dest="${!#}"
  scp $(scp_opts) -r "${@:1:$#-1}" "admin@127.0.0.1:$dest"
}

cmd_get() {
  (( $# == 2 )) || die "Aufruf: $0 get QUELLE ZIEL"
  scp $(scp_opts) -r "admin@127.0.0.1:$1" "$2"
}

# ---------- Snapshots ----------

cmd_snapshot() {
  local name="${1:-}"; [[ -n "$name" ]] || die "Name fehlt."
  running && die "VM erst stoppen ('$0 stop')."
  run qemu-img snapshot -c "$name" "$DISK"
  # UEFI-Variablen und TPM-Zustand gehören dazu (BitLocker/Windows Hello hängen am TPM)
  run cp "$VARS" "$VARS.$name"
  run cp -a "$TPM_DIR" "$TPM_DIR.$name"
}

cmd_restore() {
  local name="${1:-}"; [[ -n "$name" ]] || die "Name fehlt."
  running && die "VM erst stoppen ('$0 stop')."
  qemu-img snapshot -l "$DISK" | awk 'NR>2{print $2}' | grep -qxF "$name" || die "Snapshot '$name' unbekannt."
  local backup="vor-restore-$(date +%Y%m%d-%H%M%S)"
  info "Aktuellen Zustand sichern als '$backup', dann '$name' zurückspielen"
  cmd_snapshot "$backup"
  run qemu-img snapshot -a "$name" "$DISK"
  if [[ -f "$VARS.$name" ]]; then run cp "$VARS.$name" "$VARS"; fi
  if [[ -d "$TPM_DIR.$name" ]]; then run rm -rf "$TPM_DIR"; run cp -a "$TPM_DIR.$name" "$TPM_DIR"; fi
}

cmd_snapshots() { qemu-img snapshot -l "$DISK"; }

case "${1:-}" in
  create) shift; cmd_create "$@" ;;
  start) shift; cmd_start "$@" ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  wait) cmd_wait ;;
  ssh) shift; cmd_ssh "$@" ;;
  put) shift; cmd_put "$@" ;;
  get) shift; cmd_get "$@" ;;
  snapshot) shift; cmd_snapshot "$@" ;;
  restore) shift; cmd_restore "$@" ;;
  snapshots) cmd_snapshots ;;
  *) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
