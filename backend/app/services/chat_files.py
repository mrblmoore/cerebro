"""
Documents attached in chat — Word, Excel, PowerPoint, PDF, text, CSV,
Markdown, logs and zips, up to 50 MB.

Each upload is kept in ``DATA_DIR/uploads/<id>/<its own name>`` so the
existing tools (``read_document``, ``read_file``, ``review_log``,
``edit_document``) work on it by path, and it's tracked like any document
Cerebro has seen, so it becomes searchable too. Images keep their own path
(``chat_images``).
"""

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core import logger
from app.core.paths import UPLOADS_DIR

MAX_BYTES = 50 * 1024 * 1024
EXTENSIONS = {".docx", ".xlsx", ".pptx", ".pdf", ".txt", ".csv", ".md", ".log", ".zip",
              ".json", ".xml", ".vtt", ".srt", ".tsv", ".html", ".htm", ".eml", ".doc", ".xls"}
_ID = re.compile(r"^[0-9a-f]{32}$")


class FileError(ValueError):
    pass


def _safe_name(filename: str) -> str:
    name = Path(filename or "file").name
    name = re.sub(r"[^\w .()+-]+", "_", name).strip(" .") or "file"
    return name[:120]


def save(data: bytes, filename: str) -> Dict[str, Any]:
    if not data:
        raise FileError("The file was empty.")
    if len(data) > MAX_BYTES:
        raise FileError(f"That file is too big (max {MAX_BYTES // (1024 * 1024)} MB).")
    name = _safe_name(filename)
    suffix = Path(name).suffix.lower()
    if suffix not in EXTENSIONS:
        raise FileError(f"Cerebro doesn't take {suffix or 'files without an extension'} here. "
                        "Attach Word, Excel, PowerPoint, PDF, text, CSV, Markdown, logs or zip.")
    file_id = uuid.uuid4().hex
    folder = UPLOADS_DIR / file_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    info = {"id": file_id, "name": name, "size": len(data), "kind": suffix.lstrip(".")}
    (folder / ".info.json").write_text(json.dumps(info), encoding="utf-8")
    logger.info("chat", "File attached", {"name": name, "bytes": len(data)})
    return {**info, "path": str(path)}


def resolve(file_id: str) -> Optional[Dict[str, Any]]:
    if not _ID.match(file_id or ""):
        return None
    folder = UPLOADS_DIR / file_id
    try:
        info = json.loads((folder / ".info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    path = folder / info["name"]
    if not path.is_file():
        return None
    return {**info, "path": str(path)}


def resolve_all(ids: List[str]) -> List[Dict[str, Any]]:
    return [info for info in (resolve(i) for i in ids or []) if info]


def note(files: List[Dict[str, Any]]) -> str:
    """What the agent is told about the attachments."""
    return "\n".join(f"Attached: {f['name']} — {f['path']}" for f in files)


def track(db, files: List[Dict[str, Any]]) -> None:
    """Remember attached documents like any other, so they're searchable."""
    from app.services import document_readers
    from app.services.document_service import DocumentService

    for info in files:
        try:
            if document_readers.is_supported(Path(info["path"])):
                DocumentService(db).observe(info["path"], discovered_by="chat")
        except Exception as exc:  # noqa: BLE001 - tracking is a nicety
            logger.warn("chat", "Couldn't track an attached file", {"error": str(exc)[:200]})
