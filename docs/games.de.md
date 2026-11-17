# Spiele (Steam, Heroic)

## Steam

Die Steam-Bibliothek eines Kindes liegt unter `/srv/steam/<child>`, `~/.local/share/Steam` ist nur
ein Verweis dorthin. Grund: Bei aktiver Programmkontrolle ist das Home `noexec`, aus dem Home startet
Steam dann nicht. Den Verweis legt der Agent für jedes zugeordnete Kind an. Einen schon vorhandenen
Steam-Ordner im Home verschiebt er nach der Abmeldung des Kindes automatisch.

Das klappt nur, wenn `/home` und `/srv` auf demselben Dateisystem liegen (bei btrfs: auf demselben
Subvolume, bei CachyOS mit `@home` also **nicht**) und die Programmkontrolle für das Kind gerade aus
ist (das noexec-Home ist ein eigener Mount). Sonst meldet das WebUI unter „Integrität“ „…bitte von Hand
verschieben“. Dann von Hand verschieben, bei abgemeldetem Kind und mit beendetem Steam:

```bash
sudo rmdir /srv/steam/<child>                         # muss leer sein
sudo mv /home/<child>/.local/share/Steam /srv/steam/<child>
sudo chown -R <child>: /srv/steam/<child> && sudo chmod 700 /srv/steam/<child>
sudo systemctl restart kidctl-agent                  # legt den Verweis an
```

Der Ordner kann sehr groß sein, das Kopieren zwischen Partitionen dauert entsprechend.

## Heroic (GOG, Epic, Amazon)

Freigegeben wird nur „Heroic“. Electron, die Hilfsprogramme (`gogdl`, `legendary`) und alles, was
Heroic startet (Wine, Proton, Spiele), sind damit erlaubt. Wie bei Steam liegen die Ordner, aus
denen etwas gestartet wird, außerhalb des Homes unter `/srv/heroic/<child>`:

| Im Home (Verweis) | Ziel |
|---|---|
| `~/Games/Heroic` | `/srv/heroic/<child>/Games` (Spiele, Wine-Prefixe) |
| `~/.config/heroic/tools` | `/srv/heroic/<child>/tools` (Wine, Proton) |
| `~/.local/share/umu` | `/srv/heroic/<child>/umu` (Laufzeit für Proton) |
| `~/.cache/heroic_gogdl`, `~/.cache/legendary` | `/srv/heroic/<child>/cache/…` (native Module von GOG- und Epic-Anbindung) |

Vorhandene Ordner verschiebt der Agent nach der Abmeldung, soweit möglich (Einschränkungen wie bei
Steam), sonst von Hand wie bei Steam (`/srv/heroic/<child>/Games` bzw. `tools` als Ziel). Die
Cache-Ordner von `gogdl`/`legendary` werden einfach gelöscht und neu angelegt.

Ist Steam bzw. Heroic für ein Kind mit Programmkontrolle **nicht** freigegeben, hängt der Agent dessen
Spieleordner `noexec` ein. Sonst könnte das Kind dort eigene Bibliotheken ablegen und in erlaubte
Programme laden. Nach der Freigabe wird das sofort aufgehoben. In Heroic den Standard-Installationsordner nicht ändern: Spiele außerhalb von
`~/Games/Heroic` starten bei aktiver Programmkontrolle nicht. Amazon-Spiele (`nile`) funktionieren
bei aktiver Programmkontrolle nicht.
