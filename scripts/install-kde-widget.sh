#!/usr/bin/env bash
# Install (or update) the LG Air Conditioner widget for KDE Plasma 6.
# Usage: scripts/install-kde-widget.sh [--demo]
#   --demo  point the widget at the simulated air conditioner instead of the real one
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
bin="${LG_AC_BIN:-$repo/.venv/bin}"
id="io.github.gabrielcs-aero.lgac"
build="$repo/build/kde-widget"

if [[ ! -x "$bin/lg-ac-cli" ]]; then
    echo "Can't find $bin/lg-ac-cli. Install the app first (see README), or set LG_AC_BIN." >&2
    exit 1
fi

command="$bin/lg-ac-cli"
[[ "${1:-}" == "--demo" ]] && command="$command --demo"

rm -rf "$build"
mkdir -p "$build"
cp -r "$repo/widgets/kde/package/." "$build/"
mkdir -p "$build/contents/icons"
cp "$repo"/lg_ac/icons/*.svg "$build/contents/icons/"
# Point the widget's settings at this install.
sed -i \
    -e "s|<default>lg-ac-cli</default>|<default>$command</default>|" \
    -e "s|<default>lg-ac</default>|<default>$bin/lg-ac</default>|" \
    "$build/contents/config/main.xml"

if kpackagetool6 -t Plasma/Applet -s "$id" >/dev/null 2>&1; then
    kpackagetool6 -t Plasma/Applet -u "$build"
else
    kpackagetool6 -t Plasma/Applet -i "$build"
fi

echo
echo "Installed. Right-click your panel → Add or Manage Widgets → search for \"LG Air Conditioner\"."
echo "If the widget was already on your panel, restart Plasma to load the new version:"
echo "    systemctl --user restart plasma-plasmashell"
