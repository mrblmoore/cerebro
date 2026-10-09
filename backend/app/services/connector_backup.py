"""Backups of what Cerebro has learned about each connected system.

A backup is a zip of ``CONNECTORS_DIR/*.json`` (learned layouts and
overrides) plus the non-secret settings. Keeps the newest ``KEEP`` zips.
"""

import json
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core import logger
from app.core.paths import CONNECTORS_DIR, DATA_DIR, ENV_FILE

KEEP = 10
BACKUP_DIR = DATA_DIR / "backups"
_SECRET = re.compile(r"(PASSWORD|SECRET|TOKEN|API_KEY|_KEY)", re.I)


def _env_lines() -> List[str]:
    try:
        lines = Path(ENV_FILE).read_text(encoding="utf-8").splitlines()
    except (OSError, NameError, TypeError):
        return []
    kept = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if "=" in line and not _SECRET.search(key):
            kept.append(line)
    return kept


def create_backup(reason: str = "manual") -> Dict[str, Any]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = f"integrations-{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}.zip"
    path = BACKUP_DIR / name
    count = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        CONNECTORS_DIR.mkdir(parents=True, exist_ok=True)
        for file in sorted(CONNECTORS_DIR.glob("*.json")):
            archive.write(file, f"connectors/{file.name}")
            count += 1
        archive.writestr("settings.env", "\n".join(_env_lines()))
        archive.writestr("meta.json", json.dumps({"reason": reason, "created": time.time()}))
    for old in sorted(BACKUP_DIR.glob("integrations-*.zip"))[:-KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
    logger.info("backup", "Integration backup created", {"file": name, "connectors": count})
    return {"name": name, "connectors": count, "size": path.stat().st_size}


def list_backups() -> List[Dict[str, Any]]:
    if not BACKUP_DIR.exists():
        return []
    items = []
    for path in sorted(BACKUP_DIR.glob("integrations-*.zip"), reverse=True):
        items.append({"name": path.name, "size": path.stat().st_size,
                      "created": path.stat().st_mtime})
    return items


def restore_backup(name: str) -> Dict[str, Any]:
    """Restore connector layouts from a backup (settings are left alone)."""
    path = BACKUP_DIR / Path(name).name
    if not path.is_file():
        raise FileNotFoundError(name)
    create_backup("before-restore")
    restored = 0
    CONNECTORS_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        for member in archive.namelist():
            if member.startswith("connectors/") and member.endswith(".json"):
                target = CONNECTORS_DIR / Path(member).name
                target.write_bytes(archive.read(member))
                restored += 1
    return {"restored": restored}


def reset_connector(name: str) -> Dict[str, Any]:
    """Forget what was learned about one system (backs up first)."""
    safe = Path(name).name
    path = CONNECTORS_DIR / f"{safe}.json"
    if not path.exists():
        return {"reset": False, "detail": "Nothing learned yet."}
    create_backup(f"before-reset-{safe}")
    path.unlink()
    return {"reset": True, "detail": f"Forgot the learned layout for {safe}."}


def latest_backup() -> Optional[Dict[str, Any]]:
    items = list_backups()
    return items[0] if items else None
