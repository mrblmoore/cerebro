"""
The tray brain: Cerebro's mascot drawn with Pillow, one short loop per state.

The same character as the app header (``static/ui/brain.js``): the logo's two
hemispheres in the brand gradient, with a face. Each activity state has its
own little animation so a glance at the tray says what Cerebro is doing:

idle               slow breathing glow, the odd blink
thinking           neurons sparkling, eyes up
searching          magnifying glass sweeping, eyes following
browsing           eyes scanning, a spinning globe
writing            a wiggling pencil, an "o" mouth
awaiting_approval  a bouncing "!" badge
syncing            an orbiting ring
listening          sound waves
error              red, dizzy spirals for eyes, a shake
offline            grey, eyes closed, floating "z"s

Frames are drawn at 4x and downsampled, so edges stay smooth at tray size.
Nothing is shipped as image files; frames are generated on first use.

    python desktop/brain_frames.py --out preview/   # export PNG strips + GIFs
"""

import math
import re
from functools import lru_cache
from typing import Dict, List, Tuple

STATES = ("idle", "thinking", "searching", "browsing", "writing",
          "awaiting_approval", "syncing", "listening", "error", "offline")

#: Frames per loop and seconds per frame, per state.
TIMING = {
    "idle": (24, 0.12), "thinking": (12, 0.09), "searching": (12, 0.09),
    "browsing": (12, 0.1), "writing": (8, 0.09), "awaiting_approval": (10, 0.09),
    "syncing": (12, 0.08), "listening": (10, 0.1), "error": (8, 0.08),
    "offline": (16, 0.15),
}

HEMISPHERE = ("M128 30C106 30 88 41 80 59 60 58 44 71 42 90 27 98 20 116 26 133 "
              "17 149 22 170 38 179 41 199 58 212 78 209 90 221 110 227 128 222Z")
GRADIENT = ((0x9B, 0x8C, 0xFF), (0x6D, 0x6A, 0xF5), (0x2D, 0xD4, 0xEE))
INK = (0x1B, 0x16, 0x40, 255)
WHITE = (255, 255, 255, 255)
CYAN = (0x22, 0xD3, 0xEE, 255)
AMBER = (0xF5, 0x9E, 0x0B, 255)
SCALE = 4          # supersampling factor
CANVAS = 256       # design units (matches the SVG viewBox)


def _hemisphere_points(steps: int = 14) -> List[Tuple[float, float]]:
    """Sample the logo's cubic Bézier outline into a polygon."""
    numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", HEMISPHERE)]
    start, rest = numbers[:2], numbers[2:]
    points = [tuple(start)]
    current = tuple(start)
    for i in range(0, len(rest) - 5, 6):
        c1, c2, end = rest[i:i + 2], rest[i + 2:i + 4], rest[i + 4:i + 6]
        for step in range(1, steps + 1):
            t = step / steps
            u = 1 - t
            x = u ** 3 * current[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * end[0]
            y = u ** 3 * current[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * end[1]
            points.append((x, y))
        current = tuple(end)
    return points


_LEFT = _hemisphere_points()
_RIGHT = [(256 - x, y) for x, y in _LEFT]


def _gradient(size: int):
    """Diagonal violet → indigo → cyan gradient, as an RGBA image."""
    from PIL import Image

    image = Image.new("RGBA", (size, size))
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size - 2)
            if t < .55:
                a, b, k = GRADIENT[0], GRADIENT[1], t / .55
            else:
                a, b, k = GRADIENT[1], GRADIENT[2], (t - .55) / .45
            pixels[x, y] = tuple(int(a[c] + (b[c] - a[c]) * k) for c in range(3)) + (255,)
    return image


@lru_cache(maxsize=1)
def _gradient_cached(size: int):
    return _gradient(size)


def _render(state: str, frame: int, frames: int, size: int):
    from PIL import Image, ImageDraw, ImageFilter

    big = CANVAS * SCALE // 2          # draw at 512px, then downsample
    s = big / CANVAS                   # design unit → pixels
    phase = frame / frames
    wave = math.sin(phase * 2 * math.pi)

    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))

    # --- body transform: breathing / bobbing / shaking
    scale, dx, dy = 1.0, 0.0, 0.0
    if state in ("idle", "offline"):
        scale = 1 + .03 * wave
    elif state == "thinking":
        scale, dy = 1 + .04 * wave, -4 * abs(wave)
    elif state == "error":
        dx = 8 * math.sin(phase * 6 * math.pi)
    elif state == "awaiting_approval":
        dy = -3 * abs(wave)

    def tx(point):
        x, y = point
        return (((x - 128) * scale + 128 + dx) * s * .82 + big * .09,
                ((y - 136) * scale + 136 + dy) * s * .82 + big * .09)

    # --- glow
    glow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    glow_strength = {"thinking": 150, "error": 120}.get(state, 90 + int(40 * wave))
    glow_color = (0xFB, 0x71, 0x85) if state == "error" else (0x8B, 0x7C, 0xF6)
    ImageDraw.Draw(glow).ellipse([big * .14, big * .14, big * .86, big * .86],
                                 fill=glow_color + (glow_strength,))
    image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(big * .07)))

    # --- hemispheres with gradient fill
    mask = Image.new("L", (big, big), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.polygon([tx(p) for p in _LEFT], fill=255)
    mask_draw.polygon([tx(p) for p in _RIGHT], fill=255)
    fill = _gradient_cached(big)
    if state == "error":
        fill = Image.new("RGBA", (big, big), (0xF4, 0x5D, 0x7A, 255))
    image.paste(fill, (0, 0), mask)

    draw = ImageDraw.Draw(image)

    def line(points, width, color=(255, 255, 255, 80)):
        draw.line([tx(p) for p in points], fill=color, width=int(width * s * .82), joint="curve")

    def ellipse(cx, cy, rx, ry, color):
        (x0, y0), (x1, y1) = tx((cx - rx, cy - ry)), tx((cx + rx, cy + ry))
        draw.ellipse([x0, y0, x1, y1], fill=color)

    # folds
    line([(128, 44), (128, 214)], 6)
    for points in (((62, 92), (78, 90), (92, 104)), ((194, 92), (178, 90), (164, 104)),
                   ((50, 150), (68, 142), (86, 158)), ((206, 150), (188, 142), (170, 158))):
        line(points, 6)

    # --- neurons (thinking / searching)
    if state in ("thinking", "searching"):
        spots = ((74, 78), (186, 76), (44, 128), (214, 132), (96, 208), (162, 210))
        for index, (x, y) in enumerate(spots):
            level = max(0.0, math.sin((phase + index / len(spots)) * 2 * math.pi))
            ellipse(x, y, 4 + 6 * level, 4 + 6 * level, (255, 255, 255, int(255 * level)))

    # --- face
    if state == "offline":
        for cx in (98, 158):
            line([(cx - 14, 130), (cx, 137), (cx + 14, 130)], 7, INK)
    elif state == "error":
        for cx in (98, 158):
            turn = phase * 2 * math.pi
            spiral = [(cx + math.cos(turn + a / 3) * a / 1.6, 126 + math.sin(turn + a / 3) * a / 1.6)
                      for a in range(0, 26)]
            line(spiral, 5, INK)
    else:
        blink = state == "idle" and frame in (frames - 3, frames - 2)
        look_x, look_y = 0.0, 3.0
        if state in ("browsing", "searching"):
            look_x = 6 * math.sin(phase * 2 * math.pi)
        elif state == "thinking":
            look_x, look_y = 3 * math.sin(phase * 2 * math.pi), -4
        for cx in (98, 158):
            if blink:
                line([(cx - 14, 128), (cx + 14, 128)], 6, INK)
                continue
            ellipse(cx, 126, 17, 20, WHITE)
            ellipse(cx + 2 + look_x, 126 + look_y, 9, 9, INK)
            ellipse(cx + 6 + look_x * .6, 120 + look_y * .6, 3.2, 3.2, WHITE)
    ellipse(76, 156, 12, 7, (255, 126, 182, 140))
    ellipse(180, 156, 12, 7, (255, 126, 182, 140))
    if state in ("writing", "error"):
        ellipse(128, 162, 7, 9, INK)
    elif state != "offline":
        line([(114, 158), (121, 165), (128, 167), (135, 165), (142, 158)], 7, INK)

    # --- accessories
    def at(x, y):
        return tx((x, y))

    if state == "awaiting_approval":
        hop = -14 * abs(math.sin(phase * math.pi * 2))
        cx, cy = 218, 42 + hop
        ellipse(cx, cy, 34, 34, AMBER)
        line([(cx, cy - 18), (cx, cy + 6)], 10, WHITE)
        ellipse(cx, cy + 18, 6, 6, WHITE)
    elif state == "syncing":
        angle = phase * 2 * math.pi
        for k in range(16):
            a = angle + k * 2 * math.pi / 16
            if k % 2:
                continue
            p1 = (128 + 122 * math.cos(a), 150 + 40 * math.sin(a))
            p2 = (128 + 122 * math.cos(a + .25), 150 + 40 * math.sin(a + .25))
            line([p1, p2], 8, CYAN)
        ellipse(128 + 122 * math.cos(angle), 150 + 40 * math.sin(angle), 14, 14, CYAN)
    elif state == "searching":
        ox = 10 * math.sin(phase * 2 * math.pi)
        cx, cy = 208 + ox, 196 - 6 * abs(math.sin(phase * 2 * math.pi))
        (x0, y0), (x1, y1) = at(cx - 26, cy - 26), at(cx + 26, cy + 26)
        draw.ellipse([x0, y0, x1, y1], outline=WHITE, width=int(9 * s * .82))
        line([(cx + 19, cy + 19), (cx + 40, cy + 40)], 12, WHITE)
    elif state == "browsing":
        cx, cy, r = 216, 204, 30
        ellipse(cx, cy, r, r, (11, 19, 36, 255))
        (x0, y0), (x1, y1) = at(cx - r, cy - r), at(cx + r, cy + r)
        draw.ellipse([x0, y0, x1, y1], outline=CYAN, width=int(6 * s * .82))
        squeeze = abs(math.cos(phase * math.pi))
        (x0, y0), (x1, y1) = at(cx - r * squeeze, cy - r), at(cx + r * squeeze, cy + r)
        draw.ellipse([x0, y0, x1, y1], outline=CYAN, width=int(5 * s * .82))
        line([(cx - r, cy), (cx + r, cy)], 5, CYAN)
    elif state == "writing":
        tilt = math.radians(35 + 12 * math.sin(phase * 2 * math.pi))
        base = (206, 170)

        def rot(x, y):
            return (base[0] + x * math.cos(tilt) - y * math.sin(tilt),
                    base[1] + x * math.sin(tilt) + y * math.cos(tilt))
        draw.polygon([at(*rot(-9, -36)), at(*rot(9, -36)), at(*rot(9, 18)), at(*rot(-9, 18))],
                     fill=(0xFB, 0xBF, 0x24, 255))
        draw.polygon([at(*rot(-9, 18)), at(*rot(9, 18)), at(*rot(0, 36))], fill=(0xFD, 0xE6, 0x8A, 255))
        draw.polygon([at(*rot(-9, -44)), at(*rot(9, -44)), at(*rot(9, -36)), at(*rot(-9, -36))],
                     fill=(0xF4, 0x72, 0xB6, 255))
    elif state == "listening":
        for index, radius in enumerate((26, 46)):
            level = .35 + .65 * max(0.0, math.sin((phase - index * .25) * 2 * math.pi))
            arc = [(222 + radius * math.cos(a) * .55, 128 + radius * math.sin(a))
                   for a in [i / 10 - .9 for i in range(19)]]
            line(arc, 8, (0x22, 0xD3, 0xEE, int(255 * level)))
    elif state == "offline":
        for index, (x, y, size_z) in enumerate(((198, 70, 34), (226, 40, 26))):
            t = (phase + index * .5) % 1
            alpha = int(255 * math.sin(t * math.pi))
            px, py = x + 10 * t, y - 16 * t
            z = [(px, py), (px + size_z * .7, py), (px, py + size_z * .8), (px + size_z * .7, py + size_z * .8)]
            line(z, 6, (0xA5, 0xB4, 0xFC, alpha))

    if state == "offline":
        grey = image.convert("LA").convert("RGBA")
        grey.putalpha(image.getchannel("A"))
        image = grey

    return image.resize((size, size), Image.LANCZOS)


@lru_cache(maxsize=None)
def frames(state: str, size: int = 64) -> Tuple:
    """The animation loop for ``state`` as a tuple of RGBA images."""
    if state not in STATES:
        state = "idle"
    count, _ = TIMING[state]
    return tuple(_render(state, index, count, size) for index in range(count))


def frame_seconds(state: str) -> float:
    return TIMING.get(state, TIMING["idle"])[1]


def export(out_dir: str, size: int = 64) -> Dict[str, str]:
    """Write a GIF and a PNG strip per state, for previewing the animations."""
    from pathlib import Path

    from PIL import Image

    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    written = {}
    for state in STATES:
        loop = frames(state, size)
        strip = Image.new("RGBA", (size * len(loop), size))
        for index, image in enumerate(loop):
            strip.paste(image, (index * size, 0))
        strip.save(target / f"{state}.png")
        background = [Image.alpha_composite(Image.new("RGBA", image.size, (20, 22, 34, 255)), image)
                      for image in loop]
        background[0].save(target / f"{state}.gif", save_all=True, append_images=background[1:],
                           duration=int(frame_seconds(state) * 1000), loop=0, disposal=2)
        written[state] = str(target / f"{state}.gif")
    return written


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Export the tray brain animations.")
    parser.add_argument("--out", default="brain-preview")
    parser.add_argument("--size", type=int, default=64)
    options = parser.parse_args()
    for state, path in export(options.out, options.size).items():
        print(f"{state:18} {path}")
