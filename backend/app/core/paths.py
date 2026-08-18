"""
Filesystem layout for Cerebro.

Every component resolves paths through this module, so a checkout works the same
no matter which directory you launch it from — and so the packaged Windows build
works too, where the rules are different:

* code and bundled assets are read-only inside the executable;
* the install directory may not be writable;
* user data belongs in ``%LOCALAPPDATA%\\Cerebro``, where it survives upgrades
  and gets removed cleanly on uninstall.
"""

import os
import sys
from pathlib import Path

#: True when running from a PyInstaller build rather than a source checkout.
FROZEN = bool(getattr(sys, "frozen", False))


def _app_data_dir() -> Path:
    """Per-user writable location for the packaged build."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / "Cerebro"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Cerebro"
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "cerebro"


if FROZEN:
    #: Where PyInstaller unpacked the bundled read-only assets.
    BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    #: Where the executable lives — used to find files shipped beside it.
    PROJECT_ROOT = Path(sys.executable).resolve().parent
    DATA_DIR = _app_data_dir()
    WEB_DIR = BUNDLE_ROOT / "app" / "web"
    ENV_FILE = DATA_DIR / "cerebro.env"
else:
    # .../backend/app/core/paths.py -> parents[3] is the repository root
    PROJECT_ROOT = Path(__file__).resolve().parents[3]
    BUNDLE_ROOT = PROJECT_ROOT
    DATA_DIR = PROJECT_ROOT / "data"
    WEB_DIR = PROJECT_ROOT / "backend" / "app" / "web"
    ENV_FILE = PROJECT_ROOT / "backend" / ".env"

BACKEND_DIR = PROJECT_ROOT / "backend"
DESKTOP_DIR = PROJECT_ROOT / "desktop"
EXTENSION_DIR = PROJECT_ROOT / "browser-extension"

LOG_DIR = DATA_DIR / "logs"
AUDIO_DIR = DATA_DIR / "audio"
VECTOR_DIR = DATA_DIR / "vectors"

ENV_EXAMPLE = BACKEND_DIR / ".env.example"

DEFAULT_DB_PATH = DATA_DIR / "cerebro.db"
DEFAULT_LOG_PATH = LOG_DIR / "cerebro.log"


def ensure_data_dirs() -> None:
    """Create the runtime directories if they are missing."""
    for directory in (DATA_DIR, LOG_DIR, AUDIO_DIR, VECTOR_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def default_database_url() -> str:
    """SQLite URL used when the user has not configured a database."""
    return f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"


def bundled(*parts: str) -> Path:
    """Path to an asset shipped with Cerebro, packaged or not."""
    return BUNDLE_ROOT.joinpath(*parts)
