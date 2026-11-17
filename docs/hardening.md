# Hardening against physical access

KidCTL protects the **running** system. Anyone who boots the computer from a USB stick, changes kernel
parameters in the boot menu or removes the hard disk bypasses every software lock. This guide closes
these paths. Steps 1–3 are recommended for every child's computer, 4–5 are optional.

All steps are carried out as an admin (member of `wheel`). Store the passwords for firmware, boot loader
and recovery key in a password manager – don't write them down next to the computer.

| Bypass | Countermeasure | Step |
|---|---|---|
| Emergency/rescue mode (`systemd.unit=emergency.target`) | lock the root account | 1 |
| Changing kernel parameters in the boot menu (`init=/bin/bash`) | boot loader: editing only with a password | 3 |
| Booting from USB stick, CD or network | firmware: boot order + password | 2 |
| Firmware reset (CMOS battery/jumper), removing the disk | disk encryption | 4 |
| Tampering with the boot chain, decrypting without a password | Secure Boot + TPM | 5 |

If the child boots another system anyway, the agent is offline – after the configured time the
**offline alert** arrives on your phone.

## 1. Check accounts

```bash
groups <child>                                   # must contain neither wheel nor sudo/admin
sudo grep -rn NOPASSWD /etc/sudoers /etc/sudoers.d   # no rule that applies to children
sudo passwd -S root                              # second field: P = password, L = locked
```

**Lock root** – first make sure that `sudo` works with your own admin account, otherwise you lock
yourself out:

```bash
sudo -v && sudo passwd -l root       # undo: sudo passwd -u root
```

Effect: emergency mode asks for the root password. If root is locked there is no shell
("Cannot open access to console, the root account is locked"); after Enter the system continues to boot
normally. Repairs are then done from a live USB stick (which needs the firmware password from step 2).
Alternative: a long root password that the child doesn't know.

## 2. Firmware (UEFI/BIOS)

In the firmware setup (usually `F2`, `Del` or `F10` at power-on) – names vary by manufacturer:

1. Set an **administrator/supervisor password**. A plain "user" or "power-on" password is not
   enough.
2. **Boot order:** internal disk only. Disable USB, CD and network boot (PXE).
3. Disable the **boot menu** (`F12`, `F8`, `Esc`) or bind it to the password.

Test: plug in a USB stick, press the boot menu key → no USB entry, or a password prompt.

Limits: on desktop PCs, removing the CMOS battery or setting a jumper usually resets the password
(lock or seal the case, otherwise step 4). On notebooks the password can often only be reset by the
manufacturer – so don't forget it under any circumstances.

## 3. Boot loader

Which boot loader is installed?

```bash
[ -d /boot/grub ] && echo GRUB
bootctl is-installed 2>/dev/null && echo systemd-boot
ls /boot/limine.conf /boot/EFI/limine/limine.conf 2>/dev/null && echo Limine
```

### GRUB

Goal: the default entries boot without a password; editing (`e`), console (`c`) and
"Advanced options" require user and password.

1. Create a password hash:

   ```bash
   grub-mkpasswd-pbkdf2      # copy the output from "grub.pbkdf2.sha512…" on
   ```

2. Add the user to `/etc/grub.d/40_custom` (the file is kept on GRUB updates):

   ```bash
   sudo cp /etc/grub.d/40_custom /etc/grub.d/40_custom.bak
   sudoedit /etc/grub.d/40_custom
   ```

   Append at the end:

   ```
   set superusers="parents"
   password_pbkdf2 parents grub.pbkdf2.sha512.10000.…
   ```

3. Without a further step, GRUB now asks for the password at **every** boot. So that the default
   entries boot freely, they get `--unrestricted`. That is set in `/etc/grub.d/10_linux`, which is
   overwritten on every GRUB update – hence a small script plus a pacman hook.

   `/usr/local/sbin/grub-unrestricted` (mode `755`):

   ```sh
   #!/bin/sh
   # Make GRUB's default boot entries bootable without a password; editing/console stay protected.
   set -e
   f=/etc/grub.d/10_linux
   sed -i '/^CLASS=/{/--unrestricted/!s/"$/ --unrestricted"/}' "$f"
   grep -q '^CLASS=.*--unrestricted' "$f" || { echo "grub-unrestricted: $f has an unknown format" >&2; exit 1; }
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
   Description = GRUB: boot entries without password, editing only with password
   When = PostTransaction
   Exec = /usr/local/sbin/grub-unrestricted
   ```

4. Run it once: `sudo /usr/local/sbin/grub-unrestricted`

5. Test (reboot): the default entry boots without input; `e`, `c` and "Advanced options"
   ask for user and password. If the hook ever fails, GRUB only boots with the
   password – annoying, but not a security hole.

### systemd-boot

Add to `loader.conf` on the EFI partition (`bootctl status` shows the path, usually `/boot/loader/loader.conf`
or `/efi/loader/loader.conf`):

```
editor no
```

The default is `yes` – then anyone could change the kernel parameters. Test: press `e` in the boot menu;
no editor may appear.

### Limine

Add to `limine.conf`:

```
editor_enabled: no
```

If `limine.conf` is generated automatically (e.g. CachyOS with `limine-entry-tool`), check after kernel updates
that the line is still there.

## 4. Disk encryption (optional)

Useful if the firmware password can be reset (desktop PC) or the disk could be removed. Without
encryption another system can read the disk and, for example, delete the KidCTL configuration.

- **Easiest way:** encrypt during installation (`archinstall` → "Disk encryption", LUKS).
- **Afterwards:** possible (`cryptsetup reencrypt --encrypt`), but only with a complete backup –
  see ArchWiki "dm-crypt/Device encryption".
- **Unlocking:** the parents would have to type a passphrase at every boot – impractical in everyday
  life. Automatic unlocking via TPM (step 5) is convenient.
- Create a **recovery key** and store it in the password manager:

  ```bash
  sudo systemd-cryptenroll --recovery-key /dev/<luks-partition>
  ```

## 5. Secure Boot + TPM unlocking (optional, advanced)

Result: the computer boots without entering a password, but the disk is only decrypted if Secure Boot
is active and your own signed boot loader/kernel starts. A foreign system or a tampered boot chain
doesn't get the key.

Note: Secure Boot alone doesn't prevent booting from a live USB stick – with the Microsoft keys, Ubuntu
sticks for example still boot. The firmware (step 2) prevents that; encryption makes sure that such a
system can't read the disk.

Requirements: UEFI, TPM 2.0 (`systemd-cryptenroll --tpm2-device=list`), encrypted disk (step 4).
Recommended: systemd-boot with a unified kernel image (UKI). GRUB with Secure Boot is considerably
more work (ArchWiki "GRUB#Secure Boot support").

1. **Firmware:** put Secure Boot into "Setup Mode" (delete existing keys or
   "Reset to Setup Mode").
2. **Create and enroll keys:**

   ```bash
   sudo pacman -S sbctl
   sbctl status                       # Setup Mode: Enabled
   sudo sbctl create-keys
   sudo sbctl enroll-keys -m          # -m: keep Microsoft keys (firmware of graphics cards etc.)
   ```

   Without `-m`, some hardware no longer boots or shows no picture.
3. **Create the UKI:** in `/etc/mkinitcpio.d/linux.preset`, comment out `default_image` and set
   `default_uki="<ESP>/EFI/Linux/arch-linux.efi"`; kernel parameters go to `/etc/kernel/cmdline`.
   For TPM unlocking, use the systemd hooks in `/etc/mkinitcpio.conf` (`systemd` instead of
   `udev`, `sd-encrypt` instead of `encrypt`). Then `sudo mkinitcpio -P`.
4. **Sign** (`-s` registers the file; sbctl's pacman hook re-signs it after updates):

   ```bash
   sudo sbctl sign -s <ESP>/EFI/Linux/arch-linux.efi
   sudo sbctl sign -s -o /usr/lib/systemd/boot/efi/systemd-bootx64.efi.signed \
        /usr/lib/systemd/boot/efi/systemd-bootx64.efi
   sudo bootctl install
   sudo sbctl verify                  # all entries "signed"
   ```

5. **Enable Secure Boot:** reboot, switch it on in the firmware, then `sbctl status` →
   "Secure Boot: Enabled". With Secure Boot active, a UKI ignores changed kernel parameters.
6. Bind **TPM unlocking** to the Secure Boot state (PCR 7):

   ```bash
   sudo systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=7 /dev/<luks-partition>
   ```

   In `/etc/kernel/cmdline` the disk must be specified as `rd.luks.name=<LUKS-UUID>=root root=/dev/mapper/root`
   (instead of `cryptdevice=…` of the old `encrypt` hook), then
   `sudo mkinitcpio -P`.
7. **Test:** reboot without a password prompt. Cross-check: switch off Secure Boot in the firmware →
   the passphrase or recovery key is required. Then switch it back on.

After firmware updates or changed Secure Boot keys, TPM unlocking can fail. Then boot with the
recovery key and bind again:

```bash
sudo systemd-cryptenroll --wipe-slot=tpm2 --tpm2-device=auto --tpm2-pcrs=7 /dev/<luks-partition>
```

Further reading (ArchWiki): "Unified Extensible Firmware Interface/Secure Boot", "Unified kernel image",
"Trusted Platform Module#systemd-cryptenroll".

## Windows child's computer

The same principles apply on Windows; the tools have different names:

| Bypass | Countermeasure |
|---|---|
| Child has administrator rights (can stop/uninstall KidCTL) | **always** create children as "Standard user"; the web interface reports assigned children with administrator rights under "Integrity" |
| User Account Control only asks "Yes/No" | for standard users UAC asks for an admin password anyway – never type the parent password in front of the child |
| Booting from a USB stick or another system | UEFI firmware: disable booting from USB, set a firmware password |
| Removing the disk, offline access, resetting the password with a rescue medium | switch on **BitLocker** (Pro) or **device encryption** (Home, with the admin's Microsoft account and TPM); store the recovery key safely |
| Safe mode (reachable from the sign-in screen) | KidCTL starts a guard there (service under `SafeBoot`): assigned child accounts are disabled, signed-in children are signed out; the policy applies again at the next normal start |
| Windows Recovery Environment (command prompt) | asks for an administrator account; with BitLocker no access to the disk without the recovery key |

Note on the sign-in lock: KidCTL disables the child's account as long as no time is available.
If KidCTL is removed without the uninstaller (e.g. files deleted), an account that is currently locked stays
disabled – re-enable it as administrator: `net user <name> /active:yes` or
`"C:\Program Files\KidCTL\kidctl.cmd" unlock-all`.

## What remains afterwards

- Without step 4: removing the disk and modifying it in another computer.
- Hardware attacks (keyloggers, firmware flashing) – not relevant for everyday family life.
- Shoulder-surfed passwords: don't type parent passwords while the child is watching.

## Tested

In the test VM (Arch, GRUB 2.16, BIOS boot), 2026-10-14:

- GRUB password with `--unrestricted`: the default entry boots without input; `e`, `c` and
  "Advanced options" ask for user/password; wrong password → "Authentication error".
- Pacman hook: reinstalling `grub` resets `10_linux`, the hook restores `--unrestricted`
  (script is idempotent).
- Locked root + `systemd.unit=emergency.target`: no shell, the system boots normally after Enter.

Not tested in the VM (needs UEFI/TPM): systemd-boot, Limine, Secure Boot, TPM unlocking –
try them on real hardware with a backup and recovery key before relying on them.
