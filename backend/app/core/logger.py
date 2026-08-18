import os
import json
from datetime import datetime
from threading import Lock
from app.core.config import settings

_lock = Lock()

LOG_PATH = getattr(settings, 'CEREBRO_LOG_PATH', None) or os.path.join(os.getcwd(), 'cerebro.log')

LEVELS = ('DEBUG', 'INFO', 'WARN', 'ERROR')


def _now():
    return datetime.utcnow().isoformat() + 'Z'


def _write_line(line: str):
    with _lock:
        try:
            with open(LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:
            # Best-effort: fallback to stdout
            print('LOG WRITE ERROR:', line)


def log(component: str, level: str, message: str, metadata: dict = None):
    """Write a plain-text, human readable log entry plus JSON metadata.

    Format:
      [timestamp] [LEVEL] component: message | {json metadata}

    Example:
      2026-08-11T10:00:00Z [INFO] llm_service: sent request to OpenAI | {"model":"gpt-4","tokens":...}
    """
    if level not in LEVELS:
        level = 'INFO'
    meta = metadata or {}
    timestamp = _now()
    try:
        meta_json = json.dumps(meta, default=str, ensure_ascii=False)
    except Exception:
        meta_json = '{}'
    line = f"{timestamp} [{level}] {component}: {message} | {meta_json}"
    # Write to file and stdout
    _write_line(line)
    try:
        print(line)
    except Exception:
        pass


def debug(component: str, message: str, metadata: dict = None):
    log(component, 'DEBUG', message, metadata)


def info(component: str, message: str, metadata: dict = None):
    log(component, 'INFO', message, metadata)


def warn(component: str, message: str, metadata: dict = None):
    log(component, 'WARN', message, metadata)


def error(component: str, message: str, metadata: dict = None):
    log(component, 'ERROR', message, metadata)
