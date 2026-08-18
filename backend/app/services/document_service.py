"""
Document awareness — knowing what you have open and what is in it.

A ``TrackedDocument`` is a file Cerebro has been told about: by the desktop
watcher noticing Word opened it, by the browser extension seeing a SharePoint
link, or by an explicit API call. Text is extracted lazily and re-extracted when
the file changes, so the database never drifts out of sync with what is on disk.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

from sqlalchemy.orm import Session

from app.core import logger
from app.core.config import settings
from app.models.tracked_document import TrackedDocument
from app.services import document_readers as readers
from app.services.document_readers import DocumentError
from app.services.enterprise_service import detect_case
from app.services.llm_service import LLMService

PREVIEW_LIMIT = 8_000

#: Hosts that mean "this is a Microsoft 365 document library".
SHAREPOINT_HOSTS = ("sharepoint.com", "-my.sharepoint.com", "onedrive.live.com")


class DocumentService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------ tracking
    def observe(self, path: str, discovered_by: str = "api",
                web_url: str = None, read_now: bool = True) -> TrackedDocument:
        """
        Record that a document is in play, reading it if it is new or changed.

        Called every time the watcher sees the same file, so it must be cheap:
        an unchanged document only bumps ``last_seen``.
        """
        resolved = Path(path).expanduser()
        record = (self.db.query(TrackedDocument)
                  .filter(TrackedDocument.path == str(resolved)).first())

        if record is None:
            record = TrackedDocument(
                path=str(resolved),
                name=resolved.name,
                kind=readers.classify(resolved) or "unknown",
                discovered_by=discovered_by,
                web_url=web_url,
            )
            self.db.add(record)
        else:
            record.last_seen = datetime.utcnow()
            if web_url and not record.web_url:
                record.web_url = web_url

        try:
            record.size_bytes = resolved.stat().st_size
            current_mtime = resolved.stat().st_mtime
        except OSError:
            current_mtime = None

        needs_read = read_now and (
            record.text_preview is None or record.content_mtime != current_mtime)
        if needs_read:
            self._extract(record, resolved)

        self.db.commit()
        self.db.refresh(record)
        return record

    def _extract(self, record: TrackedDocument, path: Path) -> None:
        """Read the file into the record, storing the failure if it cannot be read."""
        try:
            content = readers.read(path)
        except DocumentError as exc:
            record.read_error = str(exc)
            record.text_preview = None
            logger.info("documents", "Could not read document",
                        {"path": str(path), "reason": str(exc)})
            return
        except Exception as exc:
            record.read_error = f"Unexpected error reading the file: {exc}"
            logger.error("documents", "Reader crashed",
                         {"path": str(path), "error": str(exc)})
            return

        record.read_error = None
        record.kind = content["kind"]
        record.text_preview = content["text"][:PREVIEW_LIMIT]
        record.outline = content["outline_json"]
        record.content_mtime = content.get("mtime")
        record.size_bytes = content.get("size_bytes")
        record.case_id = record.case_id or detect_case(record.name, content["text"][:4000])

    def content(self, record: TrackedDocument, full: bool = False) -> Dict[str, Any]:
        """Current content, re-read from disk so it is never stale."""
        path = Path(record.path)
        content = readers.read(path)

        # Keep the stored copy in step with what we just read.
        record.text_preview = content["text"][:PREVIEW_LIMIT]
        record.outline = content["outline_json"]
        record.content_mtime = content.get("mtime")
        record.read_error = None
        self.db.commit()

        return {
            **record.to_dict(),
            "outline": content["outline"],
            "text": content["text"] if full else content["preview"],
            "truncated": not full and len(content["text"]) > len(content["preview"]),
            "open_in_office": readers.is_open_in_office(path),
        }

    def recent(self, limit: int = 20, kind: str = None,
               case_id: str = None) -> List[TrackedDocument]:
        query = self.db.query(TrackedDocument)
        if kind:
            query = query.filter(TrackedDocument.kind == kind)
        if case_id:
            query = query.filter(TrackedDocument.case_id == case_id)
        return query.order_by(TrackedDocument.last_seen.desc()).limit(limit).all()

    def forget(self, record: TrackedDocument) -> None:
        """Stop tracking a document. The file itself is never touched."""
        self.db.delete(record)
        self.db.commit()

    # ---------------------------------------------------------- reasoning
    def summarise(self, record: TrackedDocument, question: str = None) -> str:
        """Summarise a document, or answer a question about it."""
        llm = LLMService()
        if not llm.enabled:
            raise DocumentError(
                "No AI provider configured. Set one up in Settings → AI Provider.")

        content = readers.read(Path(record.path))
        outline = json.dumps(content["outline"], default=str)[:1500]
        body = content["text"][:12_000]

        if question:
            prompt = f"""Answer this question about the document below.

Document: {record.name} ({content['kind']})
Structure: {outline}

Question: {question}

Content:
{body}

Answer from the document only. If it does not say, say so."""
        else:
            prompt = f"""Summarise this document for a support engineer.

Document: {record.name} ({content['kind']})
Structure: {outline}

Content:
{body}

Give 3-5 bullet points: what it is, what it says, and anything that needs action.
No preamble."""

        answer = llm._call_llm(prompt)
        if not question:
            record.summary = answer
            self.db.commit()
        return answer

    def index_into_knowledge(self, record: TrackedDocument) -> Dict[str, Any]:
        """Push a document's text into the searchable knowledge base."""
        from app.services.rag_service import RAGService

        content = readers.read(Path(record.path))
        if not content["text"].strip():
            raise DocumentError(f"{record.name} has no readable text to index.")

        document = RAGService(self.db).index_document({
            "title": record.name,
            "content": content["text"],
            "source": f"document:{content['kind']}",
            "url": record.web_url,
            "tags": [content["kind"]] + ([record.case_id] if record.case_id else []),
        })
        record.indexed = True
        self.db.commit()
        return {"ok": True, "document_id": document.id, "title": document.title}


# ------------------------------------------------------------- SharePoint
def is_sharepoint_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host.endswith(suffix.lstrip("-")) or suffix in host
               for suffix in SHAREPOINT_HOSTS)


def filename_from_url(url: str) -> Optional[str]:
    """
    Pull the document filename out of a SharePoint or Office-online URL.

    SharePoint links come in several shapes — a direct path, a ``?file=`` query,
    a Doc.aspx with ``sourcedoc``. The filename is the part worth extracting,
    because it is what lets us find the file in a synced folder.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return None

    from urllib.parse import parse_qs

    query = parse_qs(parsed.query or "")
    for key in ("file", "filename", "id", "sourcedoc"):
        for value in query.get(key, []):
            candidate = unquote(value).split("/")[-1].strip()
            if readers.classify(Path(candidate)):
                return candidate

    candidate = unquote(parsed.path or "").split("/")[-1].strip()
    return candidate if readers.classify(Path(candidate)) else None


def resolve_sharepoint(url: str) -> Optional[Path]:
    """
    Find the local file behind a SharePoint URL.

    In a managed environment the library is normally synced by OneDrive, so the
    document already exists on disk — no Graph API, no tokens, no network. We
    search the configured sync roots for a matching filename.
    """
    filename = filename_from_url(url)
    if not filename:
        return None

    # Prefer the deepest path segment as a disambiguator when several synced
    # libraries hold a file of the same name.
    try:
        segments = [unquote(part) for part in (urlparse(url).path or "").split("/") if part]
    except ValueError:
        segments = []
    hint = segments[-2].lower() if len(segments) >= 2 else None

    matches: List[Path] = []
    for root in settings.sharepoint_root_list:
        base = Path(root).expanduser()
        if not base.exists():
            continue
        try:
            matches.extend(p for p in base.rglob(filename) if p.is_file())
        except OSError:
            continue
        if len(matches) > 20:
            break

    if not matches:
        return None
    if hint:
        for match in matches:
            if hint in str(match).lower():
                return match
    return matches[0]


def status() -> Dict[str, Any]:
    """Document support state, shown in diagnostics."""
    if not settings.DOCUMENTS_ENABLED:
        return {"ok": True, "enabled": False, "detail": "Document reading disabled"}

    formats = readers.available_formats()
    missing = [name for name, available in formats.items() if not available]
    roots = settings.sharepoint_root_list

    detail = "Reads " + ", ".join(sorted(k for k, v in formats.items() if v))
    if missing:
        detail += f" · missing {', '.join(sorted(missing))} "
        detail += "(pip install -r backend/requirements-documents.txt)"
    if roots:
        detail += f" · {len(roots)} SharePoint sync root(s)"

    return {"ok": True, "enabled": True, "formats": formats, "detail": detail}
