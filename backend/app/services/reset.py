"""
Clearing parts of Cerebro's memory — chosen in the installer when upgrading.

The installer can't safely touch the database itself, so it writes the user's
choices to ``%LOCALAPPDATA%\\Cerebro\\reset.json``::

    {"parts": ["chats", "memory", "documents", ...]}

On the next start, before anything else uses the database, :func:`apply_pending`
first saves a backup (every affected table as JSON, plus the affected folders,
zipped into ``backups/``) and then clears exactly those parts. Nothing else is
touched; the file is removed once done, and what happened is logged.
"""

import json
import shutil
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

from app.core import logger
from app.core.paths import (ACTIVITY_DIR, AUDIO_DIR, BROWSER_PROFILE_DIR, BROWSER_SCREENSHOTS_DIR,
                            CHAT_IMAGES_DIR, DATA_DIR, ENV_FILE, UPLOADS_DIR, VECTOR_DIR)

REQUEST_FILE = DATA_DIR / "reset.json"
BACKUP_DIR = DATA_DIR / "backups"

#: part → (label, tables, folders)
PARTS: Dict[str, Dict[str, Any]] = {
    "chats": {"label": "Chats and their history",
              "tables": ["chat_messages", "agent_actions", "briefings", "conversations"],
              "folders": [CHAT_IMAGES_DIR, UPLOADS_DIR]},
    "memory": {"label": "Learned memory and writing style",
               "tables": ["memories", "style_profile", "nudges"], "folders": []},
    "documents": {"label": "Indexed documents and sources",
                  "tables": ["knowledge_chunks", "tracked_documents", "documents", "sources"],
                  "folders": [VECTOR_DIR]},
    "activity": {"label": "Activity recordings and screenshots",
                 "tables": ["activity_snapshots", "transcriptions", "audio_recordings", "events", "context_state"],
                 "folders": [ACTIVITY_DIR, AUDIO_DIR, BROWSER_SCREENSHOTS_DIR]},
    "inbox": {"label": "Mail and Teams messages Cerebro read, and follow-ups",
              "tables": ["enterprise_actions", "enterprise_messages", "followups", "cases"], "folders": []},
    "agents": {"label": "Agents, jobs, watches and ideas",
               "tables": ["task_events", "tasks", "watches", "ideas", "projects", "agents"], "folders": []},
    "settings": {"label": "Settings (back to the defaults)", "tables": [], "folders": [], "files": [ENV_FILE]},
    "signins": {"label": "Sign-ins in Cerebro's hidden browser", "tables": [], "folders": [BROWSER_PROFILE_DIR]},
}


def _jsonable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return None
    return value


def apply_pending() -> Dict[str, Any]:
    """Apply the installer's reset request, if there is one. Returns what was done."""
    if not REQUEST_FILE.is_file():
        return {}
    try:
        request = json.loads(REQUEST_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        logger.warn("reset", "Couldn't read the reset request; nothing was cleared", {"error": str(exc)})
        REQUEST_FILE.unlink(missing_ok=True)
        return {}
    parts = [p for p in (request.get("parts") or []) if p in PARTS]
    if not parts:
        REQUEST_FILE.unlink(missing_ok=True)
        return {}
    result = clear(parts)
    REQUEST_FILE.unlink(missing_ok=True)
    return result


def clear(parts: List[str]) -> Dict[str, Any]:
    """Back up, then clear, the given parts."""
    from sqlalchemy import inspect, text

    from app.core.database import engine

    parts = [p for p in parts if p in PARTS]
    present = set(inspect(engine).get_table_names())
    tables = [t for p in parts for t in PARTS[p]["tables"] if t in present]
    folders = [Path(f) for p in parts for f in PARTS[p].get("folders", [])]
    files = [Path(f) for p in parts for f in PARTS[p].get("files", [])]

    # 1. The backup, before anything is removed.
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = BACKUP_DIR / f"before-reset-{stamp}.zip"
    counts: Dict[str, int] = {}
    with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("parts.json", json.dumps({"parts": parts, "at": stamp}))
        with engine.connect() as connection:
            for table in tables:
                rows = [dict(row._mapping) for row in connection.execute(text(f'SELECT * FROM "{table}"'))]
                counts[table] = len(rows)
                archive.writestr(f"tables/{table}.json",
                                 json.dumps([{k: _jsonable(v) for k, v in r.items()} for r in rows]))
        for folder in folders:
            if folder.is_dir():
                for path in folder.rglob("*"):
                    if path.is_file():
                        archive.write(path, f"folders/{folder.name}/{path.relative_to(folder).as_posix()}")
        for path in files:
            if path.is_file():
                archive.write(path, f"files/{path.name}")

    # 2. Clear: rows (children before parents), folders, files.
    with engine.begin() as connection:
        if "agents" in parts and "conversations" in present and "chats" not in parts:
            # Chats that stay are handed to the default agent, which is recreated below.
            connection.execute(text('UPDATE "conversations" SET agent_id = NULL'))
        for table in tables:
            connection.execute(text(f'DELETE FROM "{table}"'))
    for folder in folders:
        shutil.rmtree(folder, ignore_errors=True)
    for path in files:
        path.unlink(missing_ok=True)
    if "settings" in parts:
        # Back to the defaults (and whatever the environment says), without a restart.
        from app.core.config import Settings, settings

        fresh = Settings()
        for name in type(settings).model_fields:
            setattr(settings, name, getattr(fresh, name))
    if "agents" in parts:
        from app.services import agents

        agents.ensure_default()
    logger.info("reset", "Cleared parts of Cerebro's memory, after a backup",
                {"parts": parts, "rows": counts, "backup": str(backup)})
    return {"parts": parts, "rows": counts, "backup": str(backup)}
