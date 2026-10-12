# Sourced by the install scripts: installs the app icon as "lg-ac-desktop" in the user's
# hicolor theme (SVG plus PNG sizes for launchers that prefer them) and refreshes caches.
install_app_icon() {
    local repo="$1" python="$2"
    local base="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
    mkdir -p "$base/scalable/apps"
    cp "$repo/lg_ac/icons/lg-ac.svg" "$base/scalable/apps/lg-ac-desktop.svg"
    "$python" - "$repo/lg_ac/icons/lg-ac.svg" "$base" <<'PY'
import sys
from pathlib import Path
from PySide6.QtCore import QRectF
from PySide6.QtGui import QGuiApplication, QImage, QPainter, Qt
from PySide6.QtSvg import QSvgRenderer
app = QGuiApplication(["icon", "-platform", "offscreen"])
renderer = QSvgRenderer(sys.argv[1])
for size in (16, 24, 32, 48, 64, 128, 256):
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    target = Path(sys.argv[2]) / f"{size}x{size}" / "apps" / "lg-ac-desktop.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(target))
PY
    touch "$base"
    gtk-update-icon-cache -q -f -t "$base" >/dev/null 2>&1 || true
    rm -f "${XDG_CACHE_HOME:-$HOME/.cache}/icon-cache.kcache"   # KDE rebuilds this on demand
    kbuildsycoca6 >/dev/null 2>&1 || true
}
