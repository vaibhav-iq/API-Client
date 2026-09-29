"""Generate assets/app.ico from the same logo the app paints at runtime.

Run:  python make_icon.py
The build scripts call this automatically when assets/app.ico is missing.
"""
import struct
import sys
from pathlib import Path

from PyQt5.QtCore import QBuffer, QByteArray, QIODevice
from PyQt5.QtGui import QGuiApplication

ROOT = Path(__file__).resolve().parent
ICON_PATH = ROOT / "assets" / "app.ico"
_app = None  # keeps the Qt application alive while painting


def _png_bytes(pixmap) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    pixmap.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def build_icon(path: Path = ICON_PATH) -> Path:
    global _app
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    sys.path.insert(0, str(ROOT))
    from api_client.theme import APP_ICON_SIZES, render_app_icon

    images = [(size, _png_bytes(render_app_icon(size))) for size in APP_ICON_SIZES]

    # ICO container with PNG-compressed entries (supported by Windows Vista and later).
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size  # 0 means 256 in the ICO format
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + blobs)
    return path


if __name__ == "__main__":
    print(f"Icon written: {build_icon()}")
