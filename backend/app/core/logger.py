"""
Plain-text structured logging.

Format: ``<iso-timestamp> [LEVEL] component: message | {json metadata}``

The log file lives under ``data/logs/`` by default and is rotated once it grows
past ``MAX_BYTES`` so a long-running agent cannot fill the disk.
"""

import json
import os
from datetime import datetime, timezone
from threading import Lock

from app.core.config import settings

_lock = Lock()

LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")
_LEVEL_ORDER = {name: index for index, name in enumerate(LEVELS)}

MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3


def log_path() -> str:
    """Resolved at call time so a path change from Settings takes effect at once."""
    return settings.log_path


def _min_level() -> int:
    return _LEVEL_ORDER.get(settings.LOG_LEVEL.upper(), _LEVEL_ORDER["INFO"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _rotate_if_needed(path: str) -> None:
    try:
        if os.path.getsize(path) < MAX_BYTES:
            return
    except OSError:
        return

    for index in range(BACKUP_COUNT - 1, 0, -1):
        source, target = f"{path}.{index}", f"{path}.{index + 1}"
        if os.path.exists(source):
            os.replace(source, target)
    os.replace(path, f"{path}.1")


def _write_line(line: str) -> None:
    path = log_path()
    with _lock:
        try:
            _rotate_if_needed(path)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:
            # Logging must never take the application down.
            print("LOG WRITE ERROR:", line)


def log(component: str, level: str, message: str, metadata: dict = None) -> None:
    level = level if level in LEVELS else "INFO"
    if _LEVEL_ORDER[level] < _min_level():
        return

    try:
        meta_json = json.dumps(metadata or {}, default=str, ensure_ascii=False)
    except Exception:
        meta_json = "{}"

    line = f"{_now()} [{level}] {component}: {message} | {meta_json}"
    _write_line(line)

    if settings.LOG_TO_STDOUT:
        try:
            print(line)
        except Exception:
            pass


def debug(component: str, message: str, metadata: dict = None) -> None:
    log(component, "DEBUG", message, metadata)


def info(component: str, message: str, metadata: dict = None) -> None:
    log(component, "INFO", message, metadata)


def warn(component: str, message: str, metadata: dict = None) -> None:
    log(component, "WARN", message, metadata)


def error(component: str, message: str, metadata: dict = None) -> None:
    log(component, "ERROR", message, metadata)


def tail(lines: int = 200) -> list:
    """Return the last ``lines`` log entries — powers the dashboard log viewer."""
    try:
        with open(log_path(), "r", encoding="utf-8", errors="replace") as handle:
            return [line.rstrip("\n") for line in handle.readlines()[-lines:]]
    except FileNotFoundError:
        return []
    except Exception as exc:  # pragma: no cover
        return [f"(unable to read log file: {exc})"]
