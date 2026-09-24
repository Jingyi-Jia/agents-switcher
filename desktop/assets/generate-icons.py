"""Rebuild the original monochrome switch mark with Python and Pillow."""

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent
SCALE = 4


def mark(size, *, template=False):
    image = Image.new("RGBA", (size * SCALE, size * SCALE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    unit = size * SCALE / 1024

    def polygon(points, fill):
        draw.polygon([(round(x * unit), round(y * unit)) for x, y in points], fill=fill)

    if not template:
        draw.rounded_rectangle(
            [round(48 * unit), round(48 * unit), round(976 * unit), round(976 * unit)],
            radius=round(224 * unit),
            fill="#111111",
        )
    color = "#000000" if template else "#ffffff"
    polygon([(236, 316), (622, 316), (622, 224), (794, 384), (622, 544), (622, 448), (236, 448)], color)
    polygon([(788, 576), (402, 576), (402, 480), (230, 640), (402, 800), (402, 708), (788, 708)], color)
    return image.resize((size, size), Image.Resampling.LANCZOS)


if __name__ == "__main__":
    icon = mark(1024)
    icon.save(ROOT / "icon.png")
    icon.save(ROOT / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    icon.save(ROOT / "icon.icns")
    mark(32).save(ROOT / "tray.png")
    mark(16).save(ROOT / "trayTemplate.png")
    mark(32).save(ROOT / "trayTemplate@2x.png")
