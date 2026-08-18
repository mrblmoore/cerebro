"""
Filesystem layout for Cerebro.

Every component (backend, installer, CLI, desktop widget) resolves paths through
this module so that a Cerebro checkout works the same no matter which directory
you launch it from.
"""

from pathlib import Path

# .../backend/app/core/paths.py -> parents[3] is the repository root
PROJECT_ROOT = Path(__file__).resolve().parents[3]

BACKEND_DIR = PROJECT_ROOT / "backend"
DESKTOP_DIR = PROJECT_ROOT / "desktop"
EXTENSION_DIR = PROJECT_ROOT / "browser-extension"
WEB_DIR = BACKEND_DIR / "app" / "web"

#: Everything Cerebro generates at runtime lives here, so a clean-up is one
#: ``rm -rf data/`` away and nothing is scattered through the source tree.
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = DATA_DIR / "logs"
AUDIO_DIR = DATA_DIR / "audio"
VECTOR_DIR = DATA_DIR / "vectors"

ENV_FILE = BACKEND_DIR / ".env"
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
