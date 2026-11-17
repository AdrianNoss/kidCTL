# Package repository (pacman and APT)

The packages are built and signed **on your own computer** (Arch) – the Arch packages and, without
`dpkg`, also the `.deb` packages including the APT repository under `repo/deb`; the private key never goes
to the server. Without `--apply`, all scripts only do a dry run. Script internals:
[`packaging/repo/README.md`](../packaging/repo/README.md).

## Signing key (one-time)

```bash
packaging/repo/kidctl-repo-key.sh create            # dry run
packaging/repo/kidctl-repo-key.sh create --apply    # asks for a passphrase
```

- Separate GPG folder `~/.local/share/kidctl-repo/gnupg` (kept apart from your personal keyring),
  Ed25519, valid for 3 years. Output: **fingerprint** (note it for the child computers) and `kidctl-repo.asc`.
- Choose a passphrase. At the end the script shows the **fingerprint** that every child's
  computer needs.
- The key's name/email can be changed with `KIDCTL_GPG_UID="Name <mail>"`.

> [!WARNING]
> **Back up the private key** (password manager). Without it, the child computers no longer accept
> updates:
>
> ```bash
> GNUPGHOME=~/.local/share/kidctl-repo/gnupg gpg --armor --export-secret-keys > kidctl-repo-secret.asc
> ```

## Build and publish

GitHub Actions builds the packages as soon as a commit with a new version lands on main (the workflow sets the
tag `v<version>`); signing and publishing happen locally with one command (needs the
[gh CLI](https://cli.github.com/), signed in):

1. Increase the version in `agent/kidctl_agent/__init__.py` and `packaging/arch/PKGBUILD` (`pkgver`) (a test checks
   that they match), commit, push to main. The workflow `.github/workflows/release.yml` creates the tag
   `v<version>` and builds Arch, Debian and Windows packages (without a version change it builds nothing).
2. Publish locally – the keys are never stored in GitHub. Set the target once (or set
   `KIDCTL_REPO_TARGET`):

```bash
echo "user@server:/srv/kidctl/data/repo/" > ~/.local/share/kidctl-repo/publish-target   # once
packaging/repo/kidctl-release.sh            # dry run: shows the run and all steps
packaging/repo/kidctl-release.sh --apply
```

The script waits for the build, fetches the Linux packages and the Windows installer, signs them (GPG, `latest.json`)
and transfers the repository.
Single steps if needed: `kidctl-repo-build.sh --apply --ci [RUN]`, `packaging/windows/build.py fetch
--apply [--run RUN]`, `build.py sign --apply`, `kidctl-repo-publish.sh --apply`.
Without GitHub: `kidctl-repo-build.sh --apply` builds the Linux packages locally, `packaging/windows/build.py all
--apply` the Windows installer in the Windows VM, then `kidctl-repo-publish.sh --apply`.

The SSH user needs write access to `data/repo`, and the files must be readable for the
container (e.g. `chown <user> data/repo`, modes `755`/`644`). The server serves `./data/repo` at
`https://kids.example.org/repo/`. Test:
`https://kids.example.org/repo/kidctl.db` and
`https://kids.example.org/repo/deb/dists/stable/InRelease` can be downloaded.

For the `.deb` packages, the first build downloads the bundled Python libraries for Ubuntu
24.04 (`pydantic`, `websockets`, list with checksums in `packaging/debian/vendor.txt`) to
`~/.cache/kidctl-vendor`.
