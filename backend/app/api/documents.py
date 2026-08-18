"""
API for documents Cerebro is watching — reading them, asking about them,
and editing them.

Edits are deliberate: they name the operations to perform, they support a dry
run that validates against the real file without writing, and every write is
backed up first.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.tracked_document import TrackedDocument
from app.services import document_editors, document_readers, document_service
from app.services.document_readers import DocumentError
from app.services.document_service import DocumentService

router = APIRouter(prefix="/api/documents", tags=["documents"])


class ObserveRequest(BaseModel):
    """A document has come into view — from the watcher, a browser tab, or you."""
    path: Optional[str] = None
    web_url: Optional[str] = None
    discovered_by: str = "api"
    case_id: Optional[str] = None


class AskRequest(BaseModel):
    question: Optional[str] = None


class EditRequest(BaseModel):
    operations: List[Dict[str, Any]]
    #: Validate against the real document and report what would change,
    #: without writing anything.
    dry_run: bool = False


def _document_error(exc: DocumentError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


def _get(db: Session, document_id: int) -> TrackedDocument:
    record = db.query(TrackedDocument).get(document_id)
    if not record:
        raise HTTPException(status_code=404, detail="Document not found")
    return record


@router.get("/status")
def status() -> Dict[str, Any]:
    """Which formats can be read and edited right now."""
    payload = document_service.status()
    payload["editable"] = sorted(document_editors.EDITORS)
    payload["operations"] = document_editors.describe_operations()
    payload["sharepoint_roots"] = settings.sharepoint_root_list
    return payload


@router.post("/observe")
def observe(request: ObserveRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Tell Cerebro about a document.

    Accepts a local path, or a SharePoint/OneDrive URL which is resolved to the
    locally synced file when one can be found.
    """
    if not settings.DOCUMENTS_ENABLED:
        raise HTTPException(status_code=409,
                            detail="Document reading is off. Turn it on in Settings → Documents.")

    path = request.path
    if not path and request.web_url:
        resolved = document_service.resolve_sharepoint(request.web_url)
        if resolved is None:
            filename = document_service.filename_from_url(request.web_url)
            raise HTTPException(
                status_code=404,
                detail=(f"Could not find {filename or 'that document'} in any synced folder. "
                        "Add the OneDrive sync root in Settings → Documents, or sync the "
                        "library locally."),
            )
        path = str(resolved)

    if not path:
        raise HTTPException(status_code=400, detail="Give either a path or a web_url.")

    target = Path(path).expanduser()
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {target}")
    if not document_readers.is_supported(target):
        raise HTTPException(
            status_code=415,
            detail=f"Cerebro does not read {target.suffix or 'that file type'} yet.")

    service = DocumentService(db)
    record = service.observe(str(target), discovered_by=request.discovered_by,
                             web_url=request.web_url)
    if request.case_id:
        record.case_id = request.case_id
        db.commit()

    return record.to_dict()


@router.get("")
def list_documents(limit: int = Query(20, ge=1, le=100), kind: Optional[str] = None,
                   case_id: Optional[str] = None,
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Documents Cerebro has seen, most recent first."""
    records = DocumentService(db).recent(limit=limit, kind=kind, case_id=case_id)
    return {"count": len(records), "documents": [r.to_dict() for r in records]}


@router.get("/{document_id}")
def get_document(document_id: int, full: bool = False,
                 db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Current content and structure, re-read from disk."""
    record = _get(db, document_id)
    try:
        return DocumentService(db).content(record, full=full)
    except DocumentError as exc:
        raise _document_error(exc) from exc


@router.post("/{document_id}/ask")
def ask(document_id: int, request: AskRequest = None,
        db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Summarise the document, or answer a question about it."""
    record = _get(db, document_id)
    request = request or AskRequest()
    try:
        answer = DocumentService(db).summarise(record, question=request.question)
    except DocumentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"document_id": document_id, "name": record.name,
            "question": request.question, "answer": answer}


@router.post("/{document_id}/index")
def index_document(document_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Add this document's text to the searchable knowledge base."""
    record = _get(db, document_id)
    try:
        return DocumentService(db).index_into_knowledge(record)
    except DocumentError as exc:
        raise _document_error(exc) from exc


@router.post("/{document_id}/edit")
def edit_document(document_id: int, request: EditRequest,
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Apply edit operations to a Word or Excel document.

    Send ``dry_run: true`` first to see exactly what would change. Call
    ``GET /api/documents/status`` for the operations each format supports.
    """
    record = _get(db, document_id)
    try:
        result = document_editors.apply(Path(record.path), request.operations,
                                        dry_run=request.dry_run)
    except DocumentError as exc:
        raise _document_error(exc) from exc

    if not request.dry_run:
        from datetime import datetime

        record.last_edited_by_cerebro = datetime.utcnow()
        # The file changed underneath us; force a re-read on next access.
        record.content_mtime = None
        db.commit()

    return result


@router.delete("/{document_id}")
def forget_document(document_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Stop tracking a document. The file on disk is not touched."""
    record = _get(db, document_id)
    DocumentService(db).forget(record)
    return {"ok": True, "forgotten": document_id}
