"""Render the app icon from the Lucide 'underline' mark.

Lucide (ISC licence) ships icons as 24x24 stroked SVG. Rather than depend on
the SVG at runtime, this bakes it once into app/icon.ico + icon.png.

Run:  python tools/make_icon.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(HERE), "app")

# The app's own palette, so the icon matches the window it opens.
TILE = QColor("#232a30")
STROKE = QColor("#9ec4dc")

# Lucide sizes strokes for a 24x24 box; everything below scales from that.
VIEWBOX = 24.0
STROKE_WIDTH = 2.0


def draw_icon(size):
    """Render the underline mark on a rounded tile at `size` px."""
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)

    # Rounded-square tile, inset slightly so the corners are not clipped.
    inset = size * 0.02
    radius = size * 0.22
    tile = QRectF(inset, inset, size - inset * 2, size - inset * 2)
    painter.setPen(Qt.NoPen)
    painter.setBrush(TILE)
    painter.drawRoundedRect(tile, radius, radius)

    # Scale the 24x24 artwork into the middle ~62% of the tile.
    art = size * 0.62
    scale = art / VIEWBOX
    painter.translate((size - art) / 2, (size - art) / 2)
    painter.scale(scale, scale)

    pen = QPen(STROKE)
    pen.setWidthF(STROKE_WIDTH)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    # Lucide "underline": M6 4v6a6 6 0 0 0 12 0V4  +  line 4,20 -> 20,20
    path = QPainterPath()
    path.moveTo(6, 4)
    path.lineTo(6, 10)
    path.arcTo(QRectF(6, 4, 12, 12), 180, 180)   # the U bowl
    path.lineTo(18, 4)
    painter.drawPath(path)
    painter.drawLine(4, 20, 20, 20)

    painter.end()
    return image


def main():
    app = QApplication(sys.argv[:1])  # noqa: F841 - QImage needs an app

    os.makedirs(OUT_DIR, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = {s: draw_icon(s) for s in sizes}

    png_path = os.path.join(OUT_DIR, "icon.png")
    images[256].save(png_path, "PNG")
    print(f"wrote {png_path}")

    # Windows .ico wants every size in one file; Pillow writes multi-size.
    from PIL import Image
    frames = []
    for size in sizes:
        raw = images[size].convertToFormat(QImage.Format_RGBA8888)
        ptr = raw.constBits().tobytes()
        frames.append(
            Image.frombytes("RGBA", (size, size), ptr, "raw", "RGBA")
        )

    ico_path = os.path.join(OUT_DIR, "icon.ico")
    frames[-1].save(ico_path, format="ICO",
                    sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    print(f"wrote {ico_path} ({', '.join(str(s) for s in sizes)})")


if __name__ == "__main__":
    main()
