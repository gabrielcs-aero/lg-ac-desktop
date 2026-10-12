# AUR package

`lg-ac-desktop-git/` holds the PKGBUILD for the [AUR](https://aur.archlinux.org/). It builds from
the latest `main` and installs:

- `lg-ac` and `lg-ac-cli` in `/usr/bin`
- the app's launcher entry and icon
- the KDE Plasma widget (`/usr/share/plasma/plasmoids`) and GNOME Shell extension
  (`/usr/share/gnome-shell/extensions`) for every user on the machine

LG's ThinQ Connect SDK and the AWS IoT libraries (for live updates) aren't packaged for Arch,
so the PKGBUILD downloads their exact PyPI files (pinned by SHA-256) and bundles them under
`/usr/lib/lg-ac-desktop`. To update one, change its version, URL and checksum together.

## Build and install it locally

```bash
cd packaging/aur/lg-ac-desktop-git
makepkg -si
```

## Publish to the AUR (maintainer)

1. Create an account at <https://aur.archlinux.org/register> and add your SSH public key
   under *My Account*.
2. Push the package:

   ```bash
   git clone ssh://aur@aur.archlinux.org/lg-ac-desktop-git.git /tmp/aur-lg-ac
   cp lg-ac-desktop-git/PKGBUILD lg-ac-desktop-git/.SRCINFO /tmp/aur-lg-ac/
   cd /tmp/aur-lg-ac
   git add PKGBUILD .SRCINFO
   git commit -m "Initial upload"
   git push
   ```

After any PKGBUILD change, regenerate `.SRCINFO` with `makepkg --printsrcinfo > .SRCINFO`
and push both files.
