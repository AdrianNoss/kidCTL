# Games (Steam, Heroic)

## Steam

A child's Steam library lives under `/srv/steam/<child>`; `~/.local/share/Steam` is only
a link to it. Reason: in "Block" mode, the home folder is `noexec`, and Steam doesn't start from the
home folder then. The agent creates the link for every assigned child. It moves an existing
Steam folder in the home folder automatically after the child signs out.

This only works if `/home` and `/srv` are on the same file system (with btrfs: on the same
subvolume, so **not** on CachyOS with `@home`) and app control is currently off for the child
(the noexec home is a separate mount). Otherwise the web interface reports "…please move manually" under "Integrity".
Then move it by hand, with the child signed out and Steam closed:

```bash
sudo rmdir /srv/steam/<child>                         # must be empty
sudo mv /home/<child>/.local/share/Steam /srv/steam/<child>
sudo chown -R <child>: /srv/steam/<child> && sudo chmod 700 /srv/steam/<child>
sudo systemctl restart kidctl-agent                  # creates the link
```

The folder can be very large; copying between partitions takes correspondingly long.

## Heroic (GOG, Epic, Amazon)

Only "Heroic" is allowed. This also allows Electron, the helper programs (`gogdl`, `legendary`) and everything
Heroic starts (Wine, Proton, games). As with Steam, the folders from which something is started
live outside the home folder under `/srv/heroic/<child>`:

| In the home folder (link) | Target |
|---|---|
| `~/Games/Heroic` | `/srv/heroic/<child>/Games` (games, Wine prefixes) |
| `~/.config/heroic/tools` | `/srv/heroic/<child>/tools` (Wine, Proton) |
| `~/.local/share/umu` | `/srv/heroic/<child>/umu` (runtime for Proton) |
| `~/.cache/heroic_gogdl`, `~/.cache/legendary` | `/srv/heroic/<child>/cache/…` (native modules of the GOG and Epic integration) |

The agent moves existing folders after sign-out where possible (same limits as with
Steam), otherwise move them by hand as with Steam (`/srv/heroic/<child>/Games` or `tools` as target). The
cache folders of `gogdl`/`legendary` are simply deleted and recreated.

If Steam or Heroic is **not** allowed for a child in "Block" mode, the agent mounts its
game folders `noexec`. Otherwise the child could put their own libraries there and load them into allowed
programs. Once allowed, this is lifted immediately. Don't change the default installation folder in Heroic: games outside
`~/Games/Heroic` don't start with app control active. Amazon games (`nile`) don't work
with app control active.

## Lutris and Bottles

KidCTL detects Windows games that run via Lutris or Bottles (Wine) as games: they count towards game time and the
game limit and appear with their name under "playing now" and in the statistics.

- **Lutris:** name as shown in Lutris. Linux games from Lutris are not detected.
- **Bottles:** only programs from a bottle's program list, with the name from the list. Programs started only via
  "Run executable…" and Wine helper processes don't count.
- Lutris and Bottles themselves don't count as games.

Unlike Steam and Heroic, Lutris and Bottles have no game folders of their own outside the home. With app control
active in "Block" mode, their games therefore usually don't start (not specifically tested); in "Monitor only" mode and
without app control, detection works.
