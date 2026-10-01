"""
The tray brain: Cerebro's pixel-art mascot, one short loop per activity.

The animations are built once from the two drawings in ``assets/mascot``
(``packaging/make_mascot.py``) and shared by the app, the tray and the desktop
buddy. Each activity state shows the pose that fits it:

==================  ===========================================
thinking, browsing  working — typing away on the laptop
writing, syncing    working
searching           studying — cap on, nose in the book
listening           studying
idle                dozing — screen dimmed, a few z's
awaiting_approval   alert — a bouncing "!"
error               dizzy — flushed red, stars circling
offline             asleep — grey, lights out
==================  ===========================================

    python desktop/brain_frames.py --out preview/   # export a GIF per state
"""

import json
from functools import lru_cache
from typing import Dict, Tuple

STATES = ("idle", "thinking", "searching", "browsing", "writing",
          "awaiting_approval", "syncing", "listening", "error", "offline")

#: Activity state -> mascot animation (``assets/mascot/<name>.png``).
SPRITE_FOR = {
    "idle": "dozing", "thinking": "working", "browsing": "working",
    "writing": "working", "syncing": "working",
    "searching": "studying", "listening": "studying",
    "awaiting_approval": "alert", "error": "dizzy", "offline": "asleep",
}


def mascot_dir():
    try:
        from branding import assets_dir
    except ImportError:   # imported from the backend or tests
        from pathlib import Path
        return Path(__file__).resolve().parent.parent / "assets" / "mascot"
    return assets_dir() / "mascot"


@lru_cache(maxsize=1)
def manifest() -> Dict:
    return json.loads((mascot_dir() / "mascot.json").read_text(encoding="utf-8"))


def sprite_for(state: str) -> str:
    return SPRITE_FOR.get(state, "dozing")


@lru_cache(maxsize=None)
def raw_frames(sprite: str) -> Tuple:
    """Frames of one mascot animation at native pixel size (RGBA)."""
    from PIL import Image

    info = manifest()["states"][sprite]
    width, height = manifest()["frame"]["width"], manifest()["frame"]["height"]
    strip = Image.open(mascot_dir() / info["file"]).convert("RGBA")
    return tuple(strip.crop((index * width, 0, (index + 1) * width, height))
                 for index in range(info["frames"]))


@lru_cache(maxsize=None)
def frames(state: str, size: int = 64) -> Tuple:
    """The animation loop for an activity ``state``, as square RGBA images."""
    from PIL import Image

    out = []
    for frame in raw_frames(sprite_for(state if state in STATES else "idle")):
        side = max(frame.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.alpha_composite(frame, ((side - frame.width) // 2, side - frame.height))
        # BOX keeps pixel art legible when shrinking; NEAREST when growing.
        method = Image.BOX if size < side else Image.NEAREST
        out.append(square.resize((size, size), method))
    return tuple(out)


def frame_seconds(state: str) -> float:
    return manifest()["states"][sprite_for(state)]["ms"] / 1000


def export(out_dir: str, size: int = 128) -> Dict[str, str]:
    """Write a GIF per activity state, for previewing the animations."""
    from pathlib import Path

    from PIL import Image

    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    written = {}
    for state in STATES:
        loop = [Image.alpha_composite(Image.new("RGBA", image.size, (24, 26, 38, 255)), image)
                for image in frames(state, size)]
        path = target / f"{state}.gif"
        loop[0].save(path, save_all=True, append_images=loop[1:],
                     duration=int(frame_seconds(state) * 1000), loop=0, disposal=2)
        written[state] = str(path)
    return written


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Export the mascot animations as GIFs.")
    parser.add_argument("--out", default="brain-preview")
    parser.add_argument("--size", type=int, default=128)
    options = parser.parse_args()
    for state, path in export(options.out, options.size).items():
        print(f"{state:18} {path}")
