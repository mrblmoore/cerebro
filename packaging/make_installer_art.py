#!/usr/bin/env python3
"""
Draw the installer's artwork (packaging/images/*.bmp) from Cerebro's brand
colours and the assistant itself.

    python packaging/make_installer_art.py

The tall side image shows the assistant: a built-in 3D model's preview
(``assets/faces/vrm/*/preview.png``) when there is one, else the drawn anime
face. Inno Setup takes 24-bit BMPs at 1x and 2x (164x314 and 55x55 at 1x).
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "packaging" / "images"
FONT = ROOT / "assets" / "fonts" / "InterVariable.ttf"
STOPS = ((0.0, (139, 124, 246)), (0.55, (99, 102, 241)), (1.0, (34, 211, 238)))


def _mix(t: float):
    for (a, ca), (b, cb) in zip(STOPS, STOPS[1:]):
        if a <= t <= b:
            k = (t - a) / (b - a)
            return tuple(round(ca[i] + (cb[i] - ca[i]) * k) for i in range(3))
    return STOPS[-1][1]


def _gradient(w: int, h: int) -> Image.Image:
    image = Image.new("RGB", (w, h))
    px = image.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = _mix(min(1.0, (0.75 * y / h) + (0.25 * x / w)))
    return image


def _font(size: int):
    try:
        return ImageFont.truetype(str(FONT), size)
    except OSError:
        return ImageFont.load_default()


def _assistant() -> Image.Image:
    import json

    def order(preview):
        try:
            return json.loads((preview.parent / "meta.json").read_text(encoding="utf-8")).get("order", 99)
        except (OSError, ValueError):
            return 99

    # The first built-in model (the one new agents start with).
    previews = sorted((ROOT / "assets" / "faces" / "vrm").glob("*/preview.png"), key=lambda p: (order(p), p.name))
    if previews:
        return Image.open(previews[0]).convert("RGBA")
    strip = ROOT / "assets" / "faces" / "anime" / "pink" / "twintails" / "happy.png"
    if not strip.is_file():
        strip = next((ROOT / "assets" / "faces" / "anime").rglob("calm.png"))
    image = Image.open(strip).convert("RGBA")
    side = image.height
    return image.crop((0, 0, side, side))


def side_image(scale: int) -> Image.Image:
    w, h = 164 * scale, 314 * scale
    base = _gradient(w, h).convert("RGBA")
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    for cx, cy, r, a in ((0.8, 0.12, 0.55, 70), (0.1, 0.55, 0.45, 50), (0.7, 0.95, 0.5, 60)):
        draw.ellipse((cx * w - r * w, cy * h - r * w, cx * w + r * w, cy * h + r * w), fill=(255, 255, 255, a))
    base.alpha_composite(glow.filter(ImageFilter.GaussianBlur(18 * scale)))
    # The assistant, on a soft disc.
    size = int(130 * scale)
    disc = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse((w / 2 - size * 0.56, 70 * scale - size * 0.06, w / 2 + size * 0.56,
                                  70 * scale + size * 1.06), fill=(255, 255, 255, 70))
    base.alpha_composite(disc.filter(ImageFilter.GaussianBlur(6 * scale)))
    face = _assistant().resize((size, size), Image.LANCZOS)
    base.alpha_composite(face, (int(w / 2 - size / 2), int(70 * scale)))
    draw = ImageDraw.Draw(base)
    title, tag = _font(26 * scale), _font(11 * scale)
    draw.text((w / 2, 232 * scale), "Cerebro", font=title, fill=(255, 255, 255, 255), anchor="mm")
    draw.text((w / 2, 258 * scale), "your support assistant", font=tag, fill=(255, 255, 255, 220), anchor="mm")
    draw.text((w / 2, 274 * scale), "works offline", font=tag, fill=(255, 255, 255, 190), anchor="mm")
    return base.convert("RGB")


def small_image(scale: int) -> Image.Image:
    side = 55 * scale
    image = _gradient(side, side).convert("RGBA")
    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, side - 1, side - 1), radius=12 * scale, fill=255)
    face = _assistant().resize((side - 6 * scale, side - 6 * scale), Image.LANCZOS)
    image.alpha_composite(face, (3 * scale, 3 * scale))
    out = Image.new("RGB", (side, side), (255, 255, 255))
    out.paste(image.convert("RGB"), (0, 0), mask)
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    side_image(1).save(OUT / "wizard-image.bmp")
    side_image(2).save(OUT / "wizard-image@2x.bmp")
    small_image(1).save(OUT / "wizard-small.bmp")
    small_image(2).save(OUT / "wizard-small@2x.bmp")
    print("Installer artwork written to", OUT)


if __name__ == "__main__":
    main()
