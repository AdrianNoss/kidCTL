"""Integritätsprüfungen unter Windows (Schritt 1): Dienst auch im abgesicherten Modus, Tray-Autostart,
Tray-Symbol, Kinder ohne Administratorrechte. Die Registry-Einträge legt der Installer an; der Agent prüft nur
(Reparatur durch Neuinstallation)."""

from __future__ import annotations

from kidctl_common.protocol import IntegrityCheck

SERVICE = "KidCTL"
SAFEBOOT = r"SYSTEM\CurrentControlSet\Control\SafeBoot"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "KidCTL"


def _read(key: str, value: str = "") -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            data, _typ = winreg.QueryValueEx(k, value)
            return str(data)
    except OSError:
        return None


def check_safeboot(read=_read) -> IntegrityCheck:
    missing = [mode for mode in ("Minimal", "Network") if read(rf"{SAFEBOOT}\{mode}\{SERVICE}") != "Service"]
    return IntegrityCheck(id="safeboot", label="Dienst im abgesicherten Modus", ok=not missing,
                          detail="" if not missing else
                          f"Eintrag fehlt ({', '.join(missing)}) – KidCTL neu installieren")


def check_tray_autostart(read=_read) -> IntegrityCheck:
    ok = bool(read(RUN_KEY, RUN_VALUE))
    return IntegrityCheck(id="tray-autostart", label="Autostart der Tray-App", ok=ok,
                          detail="" if ok else "Eintrag fehlt – KidCTL neu installieren")


def check_tray(icons: dict[str, bool], kid_users: set[str]) -> list[IntegrityCheck]:
    return [IntegrityCheck(id=f"tray:{user}", label=f"Tray-Symbol von {user}", ok=visible,
                           detail="" if visible else "nicht sichtbar")
            for user, visible in sorted(icons.items()) if user in kid_users]


def check_acl(users: dict[str, str], problems: dict[str, str]) -> list[IntegrityCheck]:
    """Ausführverbot im Profil der Kinder mit Programmkontrolle (``users``: SID -> User)."""
    return [IntegrityCheck(id=f"acl:{user}", label=f"Ausführverbot im Profil von {user}", ok=sid not in problems,
                           detail="" if sid not in problems else f"fehlt in {problems[sid]}")
            for sid, user in sorted(users.items(), key=lambda x: x[1])]


def check_admins(assigned: set[str], is_admin) -> list[IntegrityCheck]:
    """Zugeordnete Kinder mit Administratorrechten werden nie eingeschränkt (Grundregel) – sichtbar machen."""
    return [IntegrityCheck(id=f"admin:{user}", label=f"Konto {user} ohne Administratorrechte", ok=False,
                           detail="ist Administrator – wird nicht eingeschränkt; in den Kontoeinstellungen auf "
                                  "„Standardbenutzer“ ändern")
            for user in sorted(assigned) if is_admin(user)]
