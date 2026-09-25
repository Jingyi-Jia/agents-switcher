"""Rebuild Agent Switch's pixel mark and native icons with Python and Pillow."""

from io import BytesIO
from math import ceil
from pathlib import Path
from struct import pack

from PIL import Image, ImageDraw, PngImagePlugin


ROOT = Path(__file__).resolve().parent
GRID = 16
CYAN = "#78dce8"
VIOLET = "#b49bf4"
TRAY_GRAY = "#7b879d"
UPPER_ARROW = (
    (3, 10, 11),
    (4, 3, 12),
    (5, 2, 13),
    (6, 2, 4),
    (6, 10, 12),
    (7, 2, 4),
    (7, 10, 11),
)
LOWER_ARROW = tuple(
    (GRID - 1 - y, GRID - right, GRID - left) for y, left, right in UPPER_ARROW
)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_SIZES = {
    b"icp4": 16,
    b"icp5": 32,
    b"icp6": 64,
    b"ic07": 128,
    b"ic08": 256,
    b"ic09": 512,
    b"ic10": 1024,
    b"ic11": 32,
    b"ic12": 64,
    b"ic13": 256,
    b"ic14": 512,
}


def mark(size, *, template=False):
    """Render the transparent glyph on its native grid, with no antialiasing."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def edge(value):
        return ceil(value * size / GRID - 0.5)

    for spans, color in ((UPPER_ARROW, CYAN), (LOWER_ARROW, VIOLET)):
        for y, left, right in spans:
            draw.rectangle(
                (edge(left), edge(y), edge(right) - 1, edge(y + 1) - 1),
                fill="#000000" if template else color,
            )
    return image


def dock_icon(size):
    """Keep the tile softly rounded while leaving the pixel mark sharp."""
    scale = 4
    mask = Image.new("L", (size * scale, size * scale))
    draw = ImageDraw.Draw(mask)
    inset = round(size * scale * 3 / 64)
    bounds = (inset, inset, size * scale - inset - 1, size * scale - inset - 1)
    draw.rounded_rectangle(bounds, radius=round(size * scale * 7 / 32), fill=255)
    mask = mask.resize((size, size), Image.Resampling.BOX)

    tile = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(tile)
    for y in range(size):
        position = y / (size - 1)
        color = tuple(round(top + (bottom - top) * position) for top, bottom in (
            (43, 24), (53, 30), (70, 42),
        ))
        draw.line((0, y, size - 1, y), fill=(*color, 255))
    tile.putalpha(mask)

    edge = Image.new("RGBA", (size * scale, size * scale))
    ImageDraw.Draw(edge).rounded_rectangle(
        bounds,
        radius=round(size * scale * 7 / 32),
        outline=(143, 166, 199, 52),
        width=max(1, round(size * scale / 256)),
    )
    tile.alpha_composite(edge.resize((size, size), Image.Resampling.BOX))
    glyph_size = round(size * 7 / 8) if size >= 32 else size
    offset = (size - glyph_size) // 2
    tile.alpha_composite(mark(glyph_size), (offset, offset))
    return tile


def save_png(image, path, *, dpi=72):
    metadata = PngImagePlugin.PngInfo()
    metadata.add(b"sRGB", b"\0")
    image.save(path, format="PNG", pnginfo=metadata, dpi=(dpi, dpi))


def save_icns(icons, path):
    """Include native 16px and 32px entries, which Pillow's ICNS writer omits."""
    chunks = []
    for kind, size in ICNS_SIZES.items():
        stream = BytesIO()
        save_png(icons[size], stream)
        data = stream.getvalue()
        chunks.append(kind + pack(">I", len(data) + 8) + data)
    table = b"".join(chunk[:8] for chunk in chunks)
    content = b"TOC " + pack(">I", len(table) + 8) + table + b"".join(chunks)
    path.write_bytes(b"icns" + pack(">I", len(content) + 8) + content)


def save_svg(path):
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" '
        'fill="none" shape-rendering="crispEdges">',
    ]
    for spans, color in ((UPPER_ARROW, CYAN), (LOWER_ARROW, VIOLET)):
        data = "".join(f"M{left} {y}h{right - left}v1H{left}z" for y, left, right in spans)
        lines.append(f'  <path fill="{color}" d="{data}"/>')
    lines.append("</svg>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate(root=ROOT):
    icons = {size: dock_icon(size) for size in sorted(set(ICO_SIZES) | set(ICNS_SIZES.values()))}
    save_png(icons[1024], root / "icon.png")
    icons[256].save(
        root / "icon.ico",
        sizes=[(size, size) for size in ICO_SIZES],
        append_images=[icons[size] for size in ICO_SIZES if size != 256],
    )
    save_icns(icons, root / "icon.icns")
    tray = Image.new("RGBA", (32, 32), TRAY_GRAY)
    tray.putalpha(mark(32, template=True).getchannel("A"))
    save_png(tray, root / "tray.png", dpi=144)
    save_png(mark(16, template=True), root / "trayTemplate.png")
    save_png(mark(32, template=True), root / "trayTemplate@2x.png", dpi=144)
    save_svg(root / "mark.svg")


if __name__ == "__main__":
    generate()
