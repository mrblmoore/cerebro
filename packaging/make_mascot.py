#!/usr/bin/env python3
"""
Build Cerebro's pixel-art mascot animations from the two source drawings.

    python packaging/make_mascot.py            # writes assets/mascot/*.png + mascot.json

The sources (``assets/mascot/source/``) are high-resolution renders of pixel
art: a brain typing on a laptop (working) and a brain in a graduation cap
reading a book (studying). This script

1. recovers the art's native pixel grid (box-downsample at the grid pitch,
   snap to a small palette, harden the alpha), so every frame stays crisp;
2. animates small regions on that grid — the hands type, the screen glows,
   the tassel swings, the pages shimmer;
3. derives the remaining poses from the same art — dozing (idle), an alert
   badge (waiting for approval), dizzy (error), asleep (offline);
4. writes one horizontal strip per state plus ``mascot.json`` (frame size,
   count and timing), used by the app (CSS steps), the tray and the desktop
   buddy alike.

Run it again only when the source art changes; its output is committed.
"""

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "mascot" / "source"
OUT = ROOT / "assets" / "mascot"

#: Grid pitch (source pixels per art pixel), measured from the renders' edge
#: periodicity. The art is not perfectly regular, so these are averages.
PITCH = {"working": 8.5, "studying": 9.75}
CANVAS = (148, 128)                 # every frame, every state: one size
PALETTE_COLOURS = 56

INK = (27, 22, 64, 255)
WHITE = (255, 255, 255, 255)
AMBER = (245, 158, 11, 255)
LILAC = (165, 180, 252, 255)
STAR = (253, 224, 71, 255)


# ------------------------------------------------------------------- source
def native(name: str) -> Image.Image:
    image = Image.open(SOURCE / f"{name}.webp").convert("RGBA")
    size = round(image.width / PITCH[name])
    small = np.asarray(image.resize((size, size), Image.BOX)).copy()
    small[..., 3] = np.where(small[..., 3] >= 110, 255, 0)
    rgb = Image.fromarray(small[..., :3]).quantize(
        colors=PALETTE_COLOURS, method=Image.MEDIANCUT, dither=Image.NONE).convert("RGB")
    out = Image.fromarray(np.dstack([np.asarray(rgb), small[..., 3]]).astype(np.uint8), "RGBA")
    return out.crop(out.getbbox())


def place(sprite: Image.Image, dx: int = 0, dy: int = 0) -> Image.Image:
    """The sprite on the shared canvas: centred, sitting on the bottom edge."""
    frame = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    x = (CANVAS[0] - sprite.width) // 2 + dx
    y = CANVAS[1] - sprite.height - 2 + dy
    frame.alpha_composite(sprite, (x, y))
    return frame


def offset_of(sprite: Image.Image):
    return (CANVAS[0] - sprite.width) // 2, CANVAS[1] - sprite.height - 2


# ----------------------------------------------------------------- helpers
def lift(sprite: Image.Image, box, pixels: int) -> Image.Image:
    """Raise a rectangular region (a hand) by a few pixels."""
    out = sprite.copy()
    region = sprite.crop(box)
    out.alpha_composite(region, (box[0], box[1] - pixels))
    return out


def polygon_mask(size, points) -> np.ndarray:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon(points, fill=255)
    return np.asarray(mask) > 0


def brighten(sprite: Image.Image, where: np.ndarray, amount: float) -> Image.Image:
    arr = np.asarray(sprite).astype(np.float32).copy()
    sel = where & (arr[..., 3] > 0)
    arr[..., :3][sel] = np.clip(arr[..., :3][sel] + (255 - arr[..., :3][sel]) * amount, 0, 255)
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def darken(sprite: Image.Image, where: np.ndarray, factor: float) -> Image.Image:
    arr = np.asarray(sprite).astype(np.float32).copy()
    sel = where & (arr[..., 3] > 0)
    arr[..., :3][sel] *= factor
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def tint(sprite: Image.Image, colour, amount: float) -> Image.Image:
    arr = np.asarray(sprite).astype(np.float32).copy()
    arr[..., :3] = arr[..., :3] * (1 - amount) + np.array(colour[:3]) * amount
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")


def grey(sprite: Image.Image) -> Image.Image:
    alpha = sprite.getchannel("A")
    out = sprite.convert("LA").convert("RGBA")
    out.putalpha(alpha)
    return brighten(out, np.ones(out.size[::-1], bool), -0.25)


def put(frame: Image.Image, x: int, y: int, colour) -> None:
    if 0 <= x < frame.width and 0 <= y < frame.height:
        frame.putpixel((x, y), colour)


def glyph(frame: Image.Image, rows, x: int, y: int, colour) -> None:
    for dy, row in enumerate(rows):
        for dx, char in enumerate(row):
            if char == "#":
                put(frame, x + dx, y + dy, colour)


Z = ["####", "  # ", " #  ", "####"]
BANG = [" ### ", "#####", "##.##", "##.##", "##.##", "#####", "##.##", " ### "]
SPARK = [" # ", "###", " # "]


def zs(frame: Image.Image, phase: float, x: int, y: int, colour=LILAC) -> None:
    for index in range(2):
        t = (phase + index * 0.5) % 1.0
        if t < 0.85:
            glyph(frame, Z, int(x + 6 * t + index * 6), int(y - 12 * t - index * 8), colour)


def badge(frame: Image.Image, cx: int, cy: int) -> None:
    draw = ImageDraw.Draw(frame)
    draw.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], fill=AMBER, outline=INK)
    for dy, row in enumerate(BANG):
        for dx, char in enumerate(row):
            if char == ".":
                put(frame, cx - 2 + dx, cy - 4 + dy, WHITE)


# ------------------------------------------------------------- animations
# Regions are in native-sprite coordinates, read off the downsampled art.
LEFT_HAND = (60, 63, 81, 81)
RIGHT_HAND = (81, 68, 113, 87)
SCREEN = [(3, 45), (55, 53), (66, 96), (15, 88)]
TASSEL = (90, 26, 103, 52)
LEFT_PAGE = [(8, 74), (55, 80), (55, 96), (8, 90)]
RIGHT_PAGE = [(56, 80), (102, 74), (102, 90), (56, 96)]


def screen_band(sprite: Image.Image, phase: float) -> np.ndarray:
    """A diagonal highlight sweeping across the laptop screen."""
    h, w = sprite.size[1], sprite.size[0]
    yy, xx = np.mgrid[0:h, 0:w]
    centre = -20 + phase * 110
    band = np.abs((xx + yy * 0.5) - centre) < 5
    return band & polygon_mask(sprite.size, SCREEN)


def working(sprite: Image.Image, frames: int = 8):
    out = []
    for index in range(frames):
        frame = sprite
        if index % 2 == 0:
            frame = lift(frame, LEFT_HAND, 2)
        else:
            frame = lift(frame, RIGHT_HAND, 2)
        frame = brighten(frame, screen_band(frame, index / frames), 0.35)
        bob = -1 if index in (2, 3, 6, 7) else 0
        canvas = place(frame, dy=bob)
        # a key-press spark above the keyboard
        ox, oy = offset_of(sprite)
        spark_x = 74 + (index * 7) % 26
        glyph(canvas, SPARK if index % 2 else [" # "], ox + spark_x, oy + 74 + bob, WHITE)
        out.append(canvas)
    return out


def studying(sprite: Image.Image, frames: int = 8):
    out = []
    pages = polygon_mask(sprite.size, LEFT_PAGE) | polygon_mask(sprite.size, RIGHT_PAGE)
    art = np.asarray(sprite).astype(int)
    paper = (art[..., 0] > 175) & (art[..., 1] > 160) & (art[..., 2] > 120)
    pages &= paper                     # only the paper, never the cover
    h, w = sprite.size[1], sprite.size[0]
    yy, xx = np.mgrid[0:h, 0:w]
    for index in range(frames):
        phase = index / frames
        frame = sprite.copy()
        # tassel swing: shear the tassel rows sideways, more at the bottom
        tassel = sprite.crop(TASSEL)
        cleared = np.asarray(frame).copy()
        cleared[TASSEL[1] + 4:TASSEL[3], TASSEL[0]:TASSEL[2]] = 0
        frame = Image.fromarray(cleared, "RGBA")
        swing = math.sin(phase * 2 * math.pi) * 2.6
        for row in range(4, tassel.height):
            shift = round(swing * (row - 4) / (tassel.height - 4))
            line = tassel.crop((0, row, tassel.width, row + 1))
            frame.alpha_composite(line, (TASSEL[0] + shift, TASSEL[1] + row))
        # a reading highlight running along the page lines
        sweep = np.abs(xx - (8 + phase * 100)) < 3
        frame = brighten(frame, pages & sweep, 0.3)
        bob = -1 if index in (3, 4) else 0
        canvas = place(frame, dy=bob)
        ox, oy = offset_of(sprite)
        for k, (sx, sy) in enumerate(((ox - 6, oy + 30), (ox + sprite.width + 2, oy + 58),
                                      (ox + 8, oy + 4))):
            if (index + k * 3) % 8 in (0, 1):
                glyph(canvas, SPARK, sx, sy, STAR)
        out.append(canvas)
    return out


def dozing(sprite: Image.Image, frames: int = 12):
    dim = darken(sprite, polygon_mask(sprite.size, SCREEN), 0.55)
    out = []
    for index in range(frames):
        phase = index / frames
        breathe = round(math.sin(phase * 2 * math.pi))
        canvas = place(dim, dy=-1 if breathe > 0 else 0)
        zs(canvas, phase, 16, 34)
        out.append(canvas)
    return out


def alert(sprite: Image.Image, frames: int = 8):
    out = []
    for index in range(frames):
        hop = [0, -2, -4, -5, -4, -2, 0, 0][index]
        canvas = place(sprite)
        badge(canvas, CANVAS[0] - 12, 14 + hop)
        out.append(canvas)
    return out


def dizzy(sprite: Image.Image, frames: int = 8):
    red = tint(sprite, (251, 90, 110), 0.28)
    out = []
    for index in range(frames):
        phase = index / frames
        shake = [0, 1, 0, -1][index % 4]
        canvas = place(red, dx=shake)
        cx, cy = CANVAS[0] // 2 + 10, 14
        for k in range(3):
            angle = phase * 2 * math.pi + k * 2 * math.pi / 3
            glyph(canvas, SPARK, int(cx + 22 * math.cos(angle)) - 1,
                  int(cy + 6 * math.sin(angle)) - 1, STAR)
        out.append(canvas)
    return out


def asleep(sprite: Image.Image, frames: int = 12):
    still = grey(darken(sprite, polygon_mask(sprite.size, SCREEN), 0.35))
    out = []
    for index in range(frames):
        canvas = place(still)
        zs(canvas, index / frames, 16, 34, (148, 163, 184, 255))
        out.append(canvas)
    return out


#: state -> (builder, source art, milliseconds per frame)
STATES = {
    "working": (working, "working", 110),
    "studying": (studying, "studying", 150),
    "dozing": (dozing, "working", 200),
    "alert": (alert, "working", 95),
    "dizzy": (dizzy, "working", 90),
    "asleep": (asleep, "working", 260),
}


def build() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    sources = {name: native(name) for name in PITCH}
    manifest = {"frame": {"width": CANVAS[0], "height": CANVAS[1]}, "states": {}}
    for state, (builder, source, ms) in STATES.items():
        frames = builder(sources[source])
        strip = Image.new("RGBA", (CANVAS[0] * len(frames), CANVAS[1]), (0, 0, 0, 0))
        for index, frame in enumerate(frames):
            strip.paste(frame, (index * CANVAS[0], 0))
        strip.save(OUT / f"{state}.png", optimize=True)
        manifest["states"][state] = {"frames": len(frames), "ms": ms, "file": f"{state}.png"}
    (OUT / "mascot.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    for state, info in build()["states"].items():
        print(f"{state:9} {info['frames']:2} frames @ {info['ms']} ms")
