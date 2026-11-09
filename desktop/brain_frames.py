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


#: Activity state -> anime-face expression strip
#: (``assets/faces/anime/<palette>/<hairstyle>/``).
ANIME_FOR = {
    "idle": "calm", "thinking": "thinking", "browsing": "focused", "writing": "focused",
    "syncing": "focused", "searching": "reading", "listening": "listening",
    "awaiting_approval": "surprised", "error": "dizzy", "offline": "asleep",
}
ANIME_PALETTES = ("pink", "black", "silver", "blue", "brown", "green")
ANIME_HAIRS = ("long", "twintails", "bob", "sideponytail")
#: Passing reactions the buddy can show for a few seconds (strips that exist).
REACTIONS = {"happy": "happy", "wink": "happy", "love": "happy", "smug": "happy",
             "sorry": "sorry", "pout": "sorry", "surprised": "surprised"}
REACTION_SECONDS = 3.0

#: Which face the tray and the buddy show (the default agent's): set by use_face().
FACE = {"face": "brain", "palette": "pink", "hair": "long"}
_REACTION = {"sprite": None, "until": 0.0}


def use_face(face: str = "brain", palette: str = None, hair: str = None) -> bool:
    """Switch the desktop face. Live2D/VRM can't be drawn here: they use the anime face.

    Returns True when it changed (callers then redraw).
    """
    face = "anime" if face in ("anime", "live2d", "vrm") else "brain"
    palette = palette if palette in ANIME_PALETTES else "pink"
    hair = hair if hair in ANIME_HAIRS else "long"
    wanted = {"face": face, "palette": palette if face == "anime" else FACE["palette"],
              "hair": hair if face == "anime" else FACE["hair"]}
    if wanted == FACE:
        return False
    FACE.update(wanted)
    manifest.cache_clear()
    raw_frames.cache_clear()
    frames.cache_clear()
    return True


def react(emotion: str, seconds: float = REACTION_SECONDS) -> bool:
    """Show a passing reaction (happy, sorry, surprised…) on the anime face."""
    import time

    sprite = REACTIONS.get(emotion or "")
    if FACE["face"] != "anime" or not sprite:
        return False
    _REACTION.update(sprite=sprite, until=time.time() + seconds)
    return True


def face_dir():
    if FACE["face"] == "anime":
        base = mascot_dir().parent / "faces" / "anime" / FACE["palette"]
        styled = base / FACE.get("hair", "long")
        if (styled / "face.json").is_file():
            return styled
        if (base / "long" / "face.json").is_file():
            return base / "long"
        return base
    return mascot_dir()


def mascot_dir():
    try:
        from branding import assets_dir
    except ImportError:   # imported from the backend or tests
        from pathlib import Path
        return Path(__file__).resolve().parent.parent / "assets" / "mascot"
    return assets_dir() / "mascot"


@lru_cache(maxsize=1)
def manifest() -> Dict:
    name = "face.json" if FACE["face"] == "anime" else "mascot.json"
    return json.loads((face_dir() / name).read_text(encoding="utf-8"))


def sprite_for(state: str) -> str:
    if FACE["face"] == "anime":
        import time

        states = manifest()["states"]
        if _REACTION["sprite"] and time.time() < _REACTION["until"] and _REACTION["sprite"] in states:
            return _REACTION["sprite"]
        sprite = ANIME_FOR.get(state, "calm")
        if sprite not in states:              # strips made before 0.9
            sprite = {"surprised": "alert"}.get(sprite, "calm")
        return sprite
    return SPRITE_FOR.get(state, "dozing")


def talk_frame(level: float, size: int = 64):
    """The anime face's mouth open by ``level`` (0..1); ``None`` for the brain."""
    if FACE["face"] != "anime" or "talk" not in manifest()["states"]:
        return None
    talk = raw_frames("talk")
    index = min(len(talk) - 1, max(0, round(level * (len(talk) - 1))))
    return _square(talk[index], size)


@lru_cache(maxsize=None)
def raw_frames(sprite: str) -> Tuple:
    """Frames of one mascot animation at native pixel size (RGBA)."""
    from PIL import Image

    info = manifest()["states"][sprite]
    width, height = manifest()["frame"]["width"], manifest()["frame"]["height"]
    strip = Image.open(face_dir() / info["file"]).convert("RGBA")
    return tuple(strip.crop((index * width, 0, (index + 1) * width, height))
                 for index in range(info["frames"]))


@lru_cache(maxsize=None)
def frames(state: str, size: int = 64) -> Tuple:
    """The animation loop for an activity ``state``, as square RGBA images."""
    return tuple(_square(frame, size)
                 for frame in raw_frames(sprite_for(state if state in STATES else "idle")))


def _square(frame, size: int):
    from PIL import Image

    side = max(frame.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.alpha_composite(frame, ((side - frame.width) // 2, side - frame.height))
    # Pixel art: BOX when shrinking, NEAREST when growing. The drawn face: LANCZOS.
    if FACE["face"] == "anime":
        method = Image.LANCZOS
    else:
        method = Image.BOX if size < side else Image.NEAREST
    return square.resize((size, size), method)


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
