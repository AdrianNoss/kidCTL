---
title: "Der erste vollständige Stand für Arch Linux"
date: 2026-10-14
version: "0.8.0"
summary: "Bildschirmzeit, Programm-Whitelist, Website-Filter und ein signiertes Paket-Repository – KidCTL läuft auf Arch Linux mit KDE Plasma."
---

Mit Version 0.8 ist KidCTL auf Arch Linux mit KDE Plasma (Wayland) komplett nutzbar. Ein eigener Server mit
Eltern-WebUI steuert einen Agenten auf jedem Kinderrechner.

- **Bildschirmzeit:** Tageskontingent und Zeitfenster je Wochentag, Vorwarnungen und danach Abmeldung mit
  Login-Sperre. Das funktioniert auch, wenn der Rechner gerade offline ist.
- **Tray-App:** Das Kind sieht seine Restzeit und kann um mehr Zeit bitten oder eine Nachricht schreiben.
- **Programm-Whitelist:** nur freigegebene Programme, selbst heruntergeladene Programme laufen nicht.
- **Website-Filter:** per DNS, mit fertigen Bündeln wie Wikipedia oder YouTube.
- **Updates** kommen aus einem signierten Pacman-Repository, das dein Server selbst ausliefert.
