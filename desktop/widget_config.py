"""
Persisted widget preferences.

Stored per-user (``%APPDATA%\\Cerebro`` on Windows, ``~/.config/cerebro``
elsewhere) rather than in the project folder, so the widget remembers where it
was and how it looked even if the checkout moves.
"""

import json
import os
import sys
from pathlib import Path

APP_DIR_NAME = "Cerebro"

DEFAULTS = {
    "api_url": "http://127.0.0.1:8000",
    "poll_seconds": 4,
    "theme": "dark",            # dark | light
    "opacity": 0.97,            # 0.4 - 1.0
    "always_on_top": True,
    "compact": False,           # start collapsed to the title strip
    "font_scale": 1.0,          # 0.85 - 1.4
    "snap_to_edges": True,
    "notify_on_change": True,   # flash the widget when context changes
    "x": None,
    "y": None,
    "width": 340,
    "height": 460,
    "active_tab": "context",
}


def config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / APP_DIR_NAME.lower()


CONFIG_PATH = config_dir() / "widget.json"


def load() -> dict:
    config = dict(DEFAULTS)
    try:
        stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        # Only accept keys we know about, so an old or hand-edited file cannot
        # inject unexpected settings.
        config.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return config


def save(config: dict) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps({k: config.get(k, v) for k, v in DEFAULTS.items()}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass  # Preferences are a convenience; never break the widget over them.
