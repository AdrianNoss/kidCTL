# Gemeinsame Einstellungen der Repo-Skripte (wird per „source“ geladen).
REPO_HOME="${KIDCTL_REPO_HOME:-$HOME/.local/share/kidctl-repo}"
export GNUPGHOME="$REPO_HOME/gnupg"
REPO_DIR="$REPO_HOME/repo"
REPO_NAME=kidctl
KEY_UID="${KIDCTL_GPG_UID:-kidctl Paketsignatur <pakete@kids.nossco.de>}"
PUBKEY_NAME=kidctl-repo.asc

APPLY=0
for a in "$@"; do [[ "$a" == "--apply" ]] && APPLY=1; done
run() { if (( APPLY )); then "$@"; else printf '[dry-run]'; printf ' %q' "$@"; echo; fi; }
die() { echo "Fehler: $*" >&2; exit 1; }

fingerprint() {
  gpg --batch --with-colons --list-secret-keys 2>/dev/null | awk -F: '/^fpr:/ {print $10; exit}'
}
