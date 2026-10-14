# Härtung gegen physischen Zugriff

KidCTL schützt das **laufende** System. Wer den Rechner von einem USB-Stick startet, im Bootmenü
Kernel-Parameter ändert oder die Festplatte ausbaut, umgeht jede Software-Sperre. Diese Anleitung
schließt diese Wege. Die Schritte 1–3 sind für jeden Kinderrechner empfohlen, 4–5 sind optional.

Alle Schritte werden als Admin (Mitglied von `wheel`) ausgeführt. Passwörter für Firmware, Bootloader
und Wiederherstellungsschlüssel im Passwortmanager ablegen – nicht am Rechner notieren.

| Umgehung | Gegenmaßnahme | Schritt |
|---|---|---|
| Notfall-/Rescue-Modus (`systemd.unit=emergency.target`) | root-Konto sperren | 1 |
| Kernel-Parameter im Bootmenü ändern (`init=/bin/bash`) | Bootloader: Bearbeiten nur mit Passwort | 3 |
| Start von USB-Stick, CD oder Netzwerk | Firmware: Bootreihenfolge + Passwort | 2 |
| Firmware-Reset (CMOS-Batterie/Jumper), Platte ausbauen | Festplattenverschlüsselung | 4 |
| Bootkette manipulieren, ohne Passwort entschlüsseln | Secure Boot + TPM | 5 |

Startet das Kind trotzdem ein anderes System, ist der Agent offline – nach der eingestellten Zeit
kommt der **Offline-Alarm** auf das Handy.

## 1. Konten prüfen

```bash
groups kind                                      # darf weder wheel noch sudo/admin enthalten
sudo grep -rn NOPASSWD /etc/sudoers /etc/sudoers.d   # keine Regel, die für Kinder gilt
sudo passwd -S root                              # zweites Feld: P = Passwort, L = gesperrt
```

**root sperren** – vorher sicherstellen, dass `sudo` mit dem eigenen Admin-Account funktioniert,
sonst sperrst du dich aus:

```bash
sudo -v && sudo passwd -l root       # rückgängig: sudo passwd -u root
```

Wirkung: Der Notfallmodus verlangt das root-Passwort. Ist root gesperrt, gibt es keine Shell
(„Cannot open access to console, the root account is locked“), nach Enter startet das System normal
weiter. Reparaturen erfolgen dann per Live-USB (dafür braucht es das Firmware-Passwort aus Schritt 2).
Alternative: ein langes root-Passwort, das das Kind nicht kennt.

## 2. Firmware (UEFI/BIOS)

Im Firmware-Setup (meist `F2`, `Entf` oder `F10` beim Start) – Bezeichnungen je nach Hersteller:

1. **Administrator-/Supervisor-Passwort** setzen. Ein reines „User“- oder „Power-On“-Passwort reicht
   nicht.
2. **Bootreihenfolge:** nur die interne Festplatte. USB-, CD- und Netzwerk-Boot (PXE) deaktivieren.
3. **Boot-Menü** (`F12`, `F8`, `Esc`) deaktivieren oder an das Passwort binden.

Test: USB-Stick einstecken, Boot-Menü-Taste drücken → kein USB-Eintrag oder Passwortabfrage.

Grenzen: Bei Desktop-PCs setzt das Entfernen der CMOS-Batterie oder ein Jumper das Passwort meist
zurück (Gehäuse abschließen oder versiegeln, sonst Schritt 4). Bei Notebooks ist das Passwort oft nur
über den Hersteller zurückzusetzen – also auf keinen Fall vergessen.

## 3. Bootloader

Welcher Bootloader ist installiert?

```bash
[ -d /boot/grub ] && echo GRUB
bootctl is-installed 2>/dev/null && echo systemd-boot
ls /boot/limine.conf /boot/EFI/limine/limine.conf 2>/dev/null && echo Limine
```

### GRUB

Ziel: Die Standardeinträge starten ohne Passwort, Bearbeiten (`e`), Konsole (`c`) und
„Erweiterte Optionen“ verlangen Benutzer und Passwort.

1. Passwort-Hash erzeugen:

   ```bash
   grub-mkpasswd-pbkdf2      # Ausgabe ab „grub.pbkdf2.sha512…“ kopieren
   ```

2. Benutzer in `/etc/grub.d/40_custom` eintragen (die Datei bleibt bei GRUB-Updates erhalten):

   ```bash
   sudo cp /etc/grub.d/40_custom /etc/grub.d/40_custom.bak
   sudoedit /etc/grub.d/40_custom
   ```

   Am Ende anfügen:

   ```
   set superusers="eltern"
   password_pbkdf2 eltern grub.pbkdf2.sha512.10000.…
   ```

3. Ohne weiteren Schritt verlangt GRUB jetzt bei **jedem** Start das Passwort. Damit die
   Standardeinträge frei starten, bekommen sie `--unrestricted`. Das steht in `/etc/grub.d/10_linux`,
   das bei jedem GRUB-Update überschrieben wird – daher ein kleines Skript plus Pacman-Hook.

   `/usr/local/sbin/grub-unrestricted` (Rechte `755`):

   ```sh
   #!/bin/sh
   # Standard-Starteinträge von GRUB ohne Passwort startbar machen; Bearbeiten/Konsole bleiben geschützt.
   set -e
   f=/etc/grub.d/10_linux
   sed -i '/^CLASS=/{/--unrestricted/!s/"$/ --unrestricted"/}' "$f"
   grep -q '^CLASS=.*--unrestricted' "$f" || { echo "grub-unrestricted: $f hat ein unbekanntes Format" >&2; exit 1; }
   grub-mkconfig -o /boot/grub/grub.cfg
   ```

   `/etc/pacman.d/hooks/90-grub-unrestricted.hook`:

   ```ini
   [Trigger]
   Operation = Install
   Operation = Upgrade
   Type = Package
   Target = grub

   [Action]
   Description = GRUB: Starteinträge ohne Passwort, Bearbeiten nur mit Passwort
   When = PostTransaction
   Exec = /usr/local/sbin/grub-unrestricted
   ```

4. Einmal ausführen: `sudo /usr/local/sbin/grub-unrestricted`

5. Testen (Neustart): Standardeintrag startet ohne Eingabe; `e`, `c` und „Erweiterte Optionen“
   fragen nach Benutzer und Passwort. Falls der Hook einmal nicht greift, startet GRUB nur mit
   Passwort – das ist lästig, aber keine Sicherheitslücke.

### systemd-boot

In `loader.conf` auf der EFI-Partition (Pfad zeigt `bootctl status`, meist `/boot/loader/loader.conf`
oder `/efi/loader/loader.conf`) eintragen:

```
editor no
```

Standard ist `yes` – damit könnte jeder die Kernel-Parameter ändern. Test: Im Bootmenü `e` drücken,
es darf kein Editor erscheinen.

### Limine

In `limine.conf` eintragen:

```
editor_enabled: no
```

Wird `limine.conf` automatisch erzeugt (z. B. CachyOS mit `limine-entry-tool`), nach Kernel-Updates
prüfen, ob die Zeile erhalten bleibt.

## 4. Festplattenverschlüsselung (optional)

Sinnvoll, wenn das Firmware-Passwort zurückgesetzt werden kann (Desktop-PC) oder die Platte
ausgebaut werden könnte. Ohne Verschlüsselung kann ein anderes System die Platte lesen und z. B. die
KidCTL-Konfiguration löschen.

- **Einfachster Weg:** bei der Installation verschlüsseln (`archinstall` → „Disk encryption“, LUKS).
- **Nachträglich:** möglich (`cryptsetup reencrypt --encrypt`), aber nur mit vollständigem Backup –
  siehe ArchWiki „dm-crypt/Device encryption“.
- **Entsperren:** Eine Passphrase bei jedem Start müssten die Eltern eintippen – im Alltag
  unpraktisch. Komfortabel ist die automatische Entsperrung per TPM (Schritt 5).
- **Wiederherstellungsschlüssel** anlegen und im Passwortmanager sichern:

  ```bash
  sudo systemd-cryptenroll --recovery-key /dev/<luks-partition>
  ```

## 5. Secure Boot + TPM-Entsperrung (optional, fortgeschritten)

Ergebnis: Der Rechner startet ohne Passworteingabe, aber die Platte wird nur entschlüsselt, wenn
Secure Boot aktiv ist und der eigene, signierte Bootloader/Kernel startet. Ein fremdes System oder
eine manipulierte Bootkette bekommt den Schlüssel nicht.

Hinweis: Secure Boot allein verhindert keinen Live-USB-Start – mit den Microsoft-Schlüsseln starten
z. B. Ubuntu-Sticks weiterhin. Das verhindert die Firmware (Schritt 2); die Verschlüsselung sorgt dafür,
dass ein solches System die Platte nicht lesen kann.

Voraussetzungen: UEFI, TPM 2.0 (`systemd-cryptenroll --tpm2-device=list`), verschlüsselte Platte
(Schritt 4). Empfohlen: systemd-boot mit Unified Kernel Image (UKI). GRUB mit Secure Boot ist deutlich
aufwendiger (ArchWiki „GRUB#Secure Boot support“).

1. **Firmware:** Secure Boot in den „Setup Mode“ versetzen (vorhandene Schlüssel löschen bzw.
   „Reset to Setup Mode“).
2. **Schlüssel anlegen und einspielen:**

   ```bash
   sudo pacman -S sbctl
   sbctl status                       # Setup Mode: Enabled
   sudo sbctl create-keys
   sudo sbctl enroll-keys -m          # -m: Microsoft-Schlüssel behalten (Firmware von Grafikkarten u. Ä.)
   ```

   Ohne `-m` startet manche Hardware nicht mehr oder zeigt kein Bild.
3. **UKI erzeugen:** In `/etc/mkinitcpio.d/linux.preset` `default_image` auskommentieren und
   `default_uki="<ESP>/EFI/Linux/arch-linux.efi"` setzen; Kernel-Parameter nach `/etc/kernel/cmdline`.
   Für die TPM-Entsperrung in `/etc/mkinitcpio.conf` die systemd-Hooks verwenden (`systemd` statt
   `udev`, `sd-encrypt` statt `encrypt`). Dann `sudo mkinitcpio -P`.
4. **Signieren** (`-s` merkt die Datei vor; der Pacman-Hook von sbctl signiert sie nach Updates neu):

   ```bash
   sudo sbctl sign -s <ESP>/EFI/Linux/arch-linux.efi
   sudo sbctl sign -s -o /usr/lib/systemd/boot/efi/systemd-bootx64.efi.signed \
        /usr/lib/systemd/boot/efi/systemd-bootx64.efi
   sudo bootctl install
   sudo sbctl verify                  # alle Einträge „signed“
   ```

5. **Secure Boot aktivieren:** Neustart, in der Firmware einschalten, danach `sbctl status` →
   „Secure Boot: Enabled“. Mit aktivem Secure Boot ignoriert ein UKI geänderte Kernel-Parameter.
6. **TPM-Entsperrung** an den Secure-Boot-Zustand (PCR 7) binden:

   ```bash
   sudo systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=7 /dev/<luks-partition>
   ```

   In `/etc/kernel/cmdline` muss die Platte per `rd.luks.name=<LUKS-UUID>=root root=/dev/mapper/root`
   angegeben sein (statt `cryptdevice=…` des alten `encrypt`-Hooks), danach
   `sudo mkinitcpio -P`.
7. **Testen:** Neustart ohne Passwortabfrage. Gegenprobe: Secure Boot in der Firmware abschalten →
   Passphrase bzw. Wiederherstellungsschlüssel wird verlangt. Danach wieder einschalten.

Nach Firmware-Updates oder geänderten Secure-Boot-Schlüsseln kann die TPM-Entsperrung fehlschlagen.
Dann mit dem Wiederherstellungsschlüssel starten und neu binden:

```bash
sudo systemd-cryptenroll --wipe-slot=tpm2 --tpm2-device=auto --tpm2-pcrs=7 /dev/<luks-partition>
```

Weiterführend (ArchWiki): „Unified Extensible Firmware Interface/Secure Boot“, „Unified kernel image“,
„Trusted Platform Module#systemd-cryptenroll“.

## Was danach noch bleibt

- Ohne Schritt 4: Platte ausbauen und in einem anderen Rechner verändern.
- Hardware-Angriffe (Keylogger, Firmware-Flash) – für den Familienalltag nicht relevant.
- Mitgelesene Passwörter: Eltern-Passwörter nicht tippen, während das Kind zusieht.

## Getestet

In der Test-VM (Arch, GRUB 2.16, BIOS-Boot), 2026-10-14:

- GRUB-Passwort mit `--unrestricted`: Standardeintrag startet ohne Eingabe; `e`, `c` und
  „Erweiterte Optionen“ fragen nach Benutzer/Passwort; falsches Passwort → „Authentication error“.
- Pacman-Hook: Neuinstallation von `grub` setzt `10_linux` zurück, der Hook stellt `--unrestricted`
  wieder her (Skript idempotent).
- Gesperrtes root + `systemd.unit=emergency.target`: keine Shell, System startet nach Enter normal.

Nicht in der VM getestet (UEFI/TPM nötig): systemd-boot, Limine, Secure Boot, TPM-Entsperrung –
vor dem Einsatz auf echter Hardware mit Backup und Wiederherstellungsschlüssel ausprobieren.
