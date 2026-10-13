#!/usr/bin/env bash
# kidctl Test-VM (Arch Linux + KDE Plasma Wayland) unter QEMU/KVM.
#
#   vm.sh create   [--apply]   Image laden, Disk + Seed anlegen
#   vm.sh start    [--no-gl|--headless]
#   vm.sh stop                 ACPI-Shutdown
#   vm.sh status
#   vm.sh ssh      [befehl …]  als admin (sudo ohne Passwort)
#   vm.sh wait                 warten, bis die Erstinstallation fertig ist
#   vm.sh snapshot NAME [--apply]   (VM muss aus sein)
#   vm.sh restore  NAME [--apply]   legt vorher automatisch einen Sicherungs-Snapshot an
#   vm.sh snapshots
#
# Ändernde Befehle laufen ohne --apply nur als Dry-Run.
# Umgebung: KIDCTL_VM_DIR (Standard ~/.local/share/kidctl-vm), KIDCTL_VM_RAM (8G),
#           KIDCTL_VM_CPUS (4), KIDCTL_VM_DISK (40G), KIDCTL_VM_SSH_PORT (2222)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERE="$REPO/dev/vm"
VM_DIR="${KIDCTL_VM_DIR:-$HOME/.local/share/kidctl-vm}"
RAM="${KIDCTL_VM_RAM:-8G}"
CPUS="${KIDCTL_VM_CPUS:-4}"
DISK_SIZE="${KIDCTL_VM_DISK:-40G}"
SSH_PORT="${KIDCTL_VM_SSH_PORT:-2222}"
IMG_URL="https://geo.mirror.pkgbuild.com/images/latest/Arch-Linux-x86_64-cloudimg.qcow2"

BASE="$VM_DIR/base.qcow2"
DISK="$VM_DIR/disk.qcow2"
SEED="$VM_DIR/seed.iso"
KEY="$VM_DIR/id_ed25519"
PIDFILE="$VM_DIR/qemu.pid"
QMP="$VM_DIR/qmp.sock"
LOG="$VM_DIR/qemu.log"

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

# Führt einen Befehl aus (mit --apply) oder zeigt ihn nur an (Dry-Run).
run() {
  if (( APPLY )); then "$@"; else printf '[dry-run]'; printf ' %q' "$@"; echo; fi
}

running() { [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

need() {
  local missing=() b
  for b in "$@"; do command -v "$b" >/dev/null || missing+=("$b"); done
  if (( ${#missing[@]} )); then
    echo "Fehlende Programme: ${missing[*]}" >&2
    echo "Installieren mit:  sudo pacman -S --needed qemu-desktop libisoburn openssl" >&2
    exit 1
  fi
}

ssh_opts() {
  echo -i "$KEY" -p "$SSH_PORT" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
       -o UserKnownHostsFile="$VM_DIR/known_hosts" -o ConnectTimeout=5
}

# ---------- create ----------

render_user_data() {
  local pub admin_hash kind_hash prov
  pub="$(cat "$KEY.pub")"
  admin_hash="$(openssl passwd -6 admin)"
  kind_hash="$(openssl passwd -6 kind)"
  prov="$(base64 -w0 "$HERE/provision.sh")"
  sed -e "s|@SSH_KEY@|$pub|" \
      -e "s|@ADMIN_HASH@|$admin_hash|" \
      -e "s|@KIND_HASH@|$kind_hash|" \
      -e "s|@PROVISION_B64@|$prov|" \
      "$HERE/user-data.tmpl"
}

cmd_create() {
  need qemu-img qemu-system-x86_64 xorriso openssl curl sha256sum ssh-keygen
  [[ -e "$DISK" ]] && die "$DISK existiert bereits (VM schon angelegt). Zum Neuaufsetzen Verzeichnis $VM_DIR selbst entfernen."
  [[ -w /dev/kvm ]] || die "/dev/kvm nicht beschreibbar (Gruppe kvm bzw. Virtualisierung im BIOS prüfen)."

  info "VM-Verzeichnis: $VM_DIR"
  run mkdir -p "$VM_DIR"

  if [[ ! -f "$BASE" ]]; then
    info "Arch-Cloud-Image laden (~550 MB) und Prüfsumme verifizieren"
    run curl -fL --progress-bar -o "$BASE.part" "$IMG_URL"
    run curl -fsSL -o "$BASE.SHA256" "$IMG_URL.SHA256"
    if (( APPLY )); then
      local want got
      want="$(awk '{print $1}' "$BASE.SHA256")"
      got="$(sha256sum "$BASE.part" | awk '{print $1}')"
      [[ "$want" == "$got" ]] || die "Prüfsumme stimmt nicht ($got != $want)"
      mv "$BASE.part" "$BASE"
      chmod a-w "$BASE"
    fi
  else
    info "Basis-Image vorhanden: $BASE"
  fi

  info "Overlay-Disk ($DISK_SIZE) auf Basis-Image anlegen"
  run qemu-img create -f qcow2 -F qcow2 -b "$BASE" "$DISK" "$DISK_SIZE"

  info "SSH-Schlüssel nur für diese VM erzeugen"
  [[ -f "$KEY" ]] || run ssh-keygen -q -t ed25519 -N "" -C "kidctl-vm" -f "$KEY"

  info "cloud-init-Seed (Benutzer admin/kind, Provisionierung) erzeugen"
  if (( APPLY )); then
    local tmp; tmp="$(mktemp -d)"
    render_user_data > "$tmp/user-data"
    printf 'instance-id: kidctl-vm-1\nlocal-hostname: kidctl-vm\n' > "$tmp/meta-data"
    xorriso -as genisoimage -quiet -output "$SEED" -volid CIDATA -joliet -rock \
      "$tmp/user-data" "$tmp/meta-data"
    rm -rf "$tmp"
  else
    echo "[dry-run] user-data aus $HERE/user-data.tmpl + provision.sh -> $SEED"
  fi

  (( APPLY )) && info "Fertig. Weiter mit: $0 start" || info "Dry-Run – mit --apply ausführen."
}

# ---------- start/stop/status ----------

cmd_start() {
  need qemu-system-x86_64
  [[ -f "$DISK" ]] || die "Keine VM angelegt – erst '$0 create --apply'."
  running && die "VM läuft bereits (PID $(cat "$PIDFILE"))."
  local display=(-device virtio-vga-gl -display gtk,gl=on,zoom-to-fit=on)
  case "${1:-}" in
    --no-gl) display=(-device virtio-vga -display gtk,zoom-to-fit=on) ;;
    --headless) display=(-device virtio-vga -display none) ;;
    "") ;;
    *) die "Unbekannte Option: $1" ;;
  esac
  rm -f "$QMP"
  info "Starte VM (RAM $RAM, $CPUS CPUs, SSH auf 127.0.0.1:$SSH_PORT, Log $LOG)"
  setsid qemu-system-x86_64 \
    -name kidctl-vm -machine q35,accel=kvm -cpu host -smp "$CPUS" -m "$RAM" \
    -drive file="$DISK",if=virtio,format=qcow2,discard=unmap \
    -drive file="$SEED",media=cdrom,readonly=on \
    -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:"$SSH_PORT"-:22 \
    -virtfs local,path="$REPO",mount_tag=kidctl,security_model=none,readonly=on \
    -device qemu-xhci -device usb-tablet \
    "${display[@]}" \
    -qmp unix:"$QMP",server=on,wait=off -pidfile "$PIDFILE" \
    >"$LOG" 2>&1 < /dev/null &
  sleep 2
  running || { cat "$LOG" >&2; die "QEMU ist nicht gestartet (siehe oben). Ohne 3D: '$0 start --no-gl'."; }
  info "Läuft. Erster Start: Installation dauert je nach Leitung 10–20 min ('$0 wait')."
}

qmp() {
  python3 - "$QMP" "$1" <<'PY'
import json, socket, sys
s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); f = s.makefile("rw")
f.readline()
for cmd in ({"execute": "qmp_capabilities"}, {"execute": sys.argv[2]}):
    f.write(json.dumps(cmd) + "\n"); f.flush(); f.readline()
PY
}

cmd_stop() {
  running || { info "VM läuft nicht."; return; }
  info "Sende ACPI-Shutdown"
  qmp system_powerdown
  for _ in $(seq 60); do running || { info "VM aus."; return; }; sleep 1; done
  die "VM reagiert nicht – ggf. 'kill $(cat "$PIDFILE")'."
}

cmd_status() {
  if running; then
    echo "VM läuft (PID $(cat "$PIDFILE"))"
    if ssh $(ssh_opts) -o BatchMode=yes admin@127.0.0.1 true 2>/dev/null; then
      echo "SSH erreichbar; cloud-init: $(ssh $(ssh_opts) admin@127.0.0.1 cloud-init status 2>/dev/null | tail -1)"
      ssh $(ssh_opts) admin@127.0.0.1 'test -f /var/lib/kidctl-vm/provisioned && echo "Provisionierung: fertig" || echo "Provisionierung: läuft/fehlgeschlagen (sudo tail /var/log/kidctl-provision.log)"'
    else
      echo "SSH noch nicht erreichbar"
    fi
  else
    echo "VM läuft nicht"
  fi
}

cmd_ssh() {
  [[ -f "$KEY" ]] || die "Kein SSH-Schlüssel – VM angelegt?"
  exec ssh $(ssh_opts) admin@127.0.0.1 "$@"
}

cmd_wait() {
  running || die "VM läuft nicht."
  info "Warte auf SSH …"
  until ssh $(ssh_opts) -o BatchMode=yes admin@127.0.0.1 true 2>/dev/null; do sleep 5; done
  info "Warte auf Abschluss von cloud-init/Provisionierung …"
  ssh $(ssh_opts) admin@127.0.0.1 'sudo cloud-init status --wait >/dev/null; test -f /var/lib/kidctl-vm/provisioned' \
    || die "Provisionierung fehlgeschlagen: '$0 ssh sudo tail -50 /var/log/kidctl-provision.log'"
  info "Fertig. Ein Neustart aktiviert die grafische Anmeldung: '$0 ssh sudo reboot'"
}

# ---------- Snapshots ----------

cmd_snapshot() {
  local name="${1:-}"; [[ -n "$name" ]] || die "Name fehlt."
  running && die "VM erst stoppen ('$0 stop')."
  run qemu-img snapshot -c "$name" "$DISK"
}

cmd_restore() {
  local name="${1:-}"; [[ -n "$name" ]] || die "Name fehlt."
  running && die "VM erst stoppen ('$0 stop')."
  qemu-img snapshot -l "$DISK" | awk 'NR>2{print $2}' | grep -qxF "$name" || die "Snapshot '$name' unbekannt."
  local backup="vor-restore-$(date +%Y%m%d-%H%M%S)"
  info "Aktuellen Zustand sichern als Snapshot '$backup', dann '$name' zurückspielen"
  run qemu-img snapshot -c "$backup" "$DISK"
  run qemu-img snapshot -a "$name" "$DISK"
}

cmd_snapshots() { qemu-img snapshot -l "$DISK"; }

case "${1:-}" in
  create) shift; cmd_create "$@" ;;
  start) shift; cmd_start "$@" ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  ssh) shift; cmd_ssh "$@" ;;
  wait) cmd_wait ;;
  snapshot) shift; cmd_snapshot "$@" ;;
  restore) shift; cmd_restore "$@" ;;
  snapshots) cmd_snapshots ;;
  *) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
