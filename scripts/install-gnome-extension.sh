#!/usr/bin/env bash
# Install (or update) the LG Air Conditioner extension for GNOME Shell 45+.
# Usage: scripts/install-gnome-extension.sh [--demo]
#   --demo  point the extension at the simulated air conditioner instead of the real one
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
bin="${LG_AC_BIN:-$repo/.venv/bin}"
uuid="lg-ac@gabrielcs-aero.github.io"
src="$repo/widgets/gnome/$uuid"
dest="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/$uuid"

if [[ ! -x "$bin/lg-ac-cli" ]]; then
    echo "Can't find $bin/lg-ac-cli. Install the app first (see README), or set LG_AC_BIN." >&2
    exit 1
fi

command="$bin/lg-ac-cli"
[[ "${1:-}" == "--demo" ]] && command="$command --demo"

rm -rf "$dest"
mkdir -p "$dest/icons"
cp -r "$src/." "$dest/"
# GNOME only recolours icons whose file name ends in -symbolic.
for icon in "$repo"/lg_ac/icons/*.svg; do
    [[ "$(basename "$icon")" == "lg-ac.svg" ]] && continue   # the colour app icon isn't symbolic
    cp "$icon" "$dest/icons/$(basename "$icon" .svg)-symbolic.svg"
done
# Point the extension's settings at this install.
sed -i \
    -e "s|<default>'lg-ac-cli'</default>|<default>'$command'</default>|" \
    -e "s|<default>'lg-ac'</default>|<default>'$bin/lg-ac'</default>|" \
    "$dest/schemas/org.gnome.shell.extensions.lg-ac.gschema.xml"
glib-compile-schemas "$dest/schemas"

echo "Installed to $dest"
echo
echo "Next:"
echo "  1. Log out and back in (on Wayland GNOME only picks up new extensions after a new login)."
echo "  2. Enable it:  gnome-extensions enable $uuid"
