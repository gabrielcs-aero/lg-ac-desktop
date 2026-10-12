#!/usr/bin/env bash
# Add "LG AC Desktop" to your application launcher (KDE, GNOME and most other desktops).
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
bin="${LG_AC_BIN:-$repo/.venv/bin}"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
# shellcheck source=scripts/_install-icon.sh
source "$repo/scripts/_install-icon.sh"

if [[ ! -x "$bin/lg-ac" ]]; then
    echo "Can't find $bin/lg-ac. Install the app first (see README), or set LG_AC_BIN." >&2
    exit 1
fi

mkdir -p "$apps"
cat > "$apps/lg-ac-desktop.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=LG AC Desktop
Comment=Control your LG ThinQ air conditioner
Exec=$bin/lg-ac
Icon=lg-ac-desktop
Categories=Utility;
Keywords=air;conditioner;ac;lg;thinq;temperature;
StartupWMClass=lg-ac-desktop
DESKTOP
update-desktop-database "$apps" >/dev/null 2>&1 || true
install_app_icon "$repo" "$bin/python"
echo "Added LG AC Desktop to your application launcher."
