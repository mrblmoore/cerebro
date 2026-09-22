"""Unified source inventory, readiness checks and SharePoint connection."""

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.system import require_local_origin
from app.core.config import settings
from app.core.database import get_db
from app.models.source import Source
from app.services import document_service, ocr_service
from app.services.rag_service import RAGService
from app.services.sharepoint_service import SharePointService
from app.services.source_service import SourceService

router = APIRouter(prefix="/api/sources", tags=["sources"])
_HEARTBEATS: Dict[str, datetime] = {}


class ActiveIn(BaseModel):
    active: bool = True


class HeartbeatIn(BaseModel):
    component: str


class SharePointOpenIn(BaseModel):
    drive_id: Optional[str] = None
    item_id: Optional[str] = None
    name: Optional[str] = None
    web_url: Optional[str] = None


@router.get("")
def list_sources(limit: int = Query(30, ge=1, le=100), kind: Optional[str] = None,
                 active: Optional[bool] = None,
                 db: Session = Depends(get_db)) -> Dict[str, Any]:
    rows = SourceService(db).list(limit=limit, kind=kind, active=active)
    return {"count": len(rows), "sources": [row.to_dict() for row in rows]}


@router.post("/heartbeat")
def heartbeat(payload: HeartbeatIn) -> Dict[str, Any]:
    _HEARTBEATS[payload.component[:80]] = datetime.utcnow()
    return {"ok": True}


def _fresh(component: str, seconds: int = 20) -> bool:
    seen = _HEARTBEATS.get(component)
    return bool(seen and seen >= datetime.utcnow() - timedelta(seconds=seconds))


@router.get("/status")
def status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    counts = {}
    for kind, count in db.query(Source.kind, func.count(Source.id)) \
            .group_by(Source.kind).all():
        counts[kind] = count

    document_state = document_service.status()
    sharepoint_state = SharePointService().status()
    rag_state = RAGService(db).status()
    checks = [
        {"id": "browser", "label": "Browser pages",
         "ok": counts.get("browser", 0) > 0,
         "detail": (f"{counts.get('browser', 0)} readable page(s) captured" if counts.get("browser")
                    else "Extension connected; use Read this page to prove content access"),
         "action": "capture_page"},
        {"id": "documents", "label": "Local documents",
         "ok": bool(document_state.get("enabled")) and _fresh("document_watcher"),
         "detail": ("Watcher running · " if _fresh("document_watcher")
                    else "Watcher not detected · ") + document_state.get("detail", ""),
         "action": "open_document"},
        {"id": "sharepoint", "label": "SharePoint",
         "ok": bool(settings.sharepoint_root_list) or bool(sharepoint_state.get("ok")),
         "detail": (sharepoint_state.get("detail") if settings.SHAREPOINT_GRAPH_ENABLED else
                    (f"{len(settings.sharepoint_root_list)} local sync root(s)" if settings.sharepoint_root_list
                     else "No local sync root or Graph connection configured")),
         "action": "connect_sharepoint"},
        {"id": "ocr", "label": "Scanned PDFs and screenshots",
         **ocr_service.status(), "action": "test_ocr"},
        {"id": "screenpipe", "label": "Screenpipe OCR",
         "ok": counts.get("screenpipe", 0) > 0,
         "enabled": settings.SCREENPIPE_ENABLED,
         "detail": (f"{counts.get('screenpipe', 0)} OCR capture(s) available to Ask"
                    if counts.get("screenpipe") else
                    ("Enabled; waiting for readable Screenpipe OCR" if settings.SCREENPIPE_ENABLED
                     else "Screenpipe disabled")),
         "action": "test_screenpipe"},
        {"id": "knowledge", "label": "Cited knowledge search",
         **rag_state, "action": "search"},
    ]
    return {"ok": all(item.get("ok") for item in checks if item["id"] in ("documents", "knowledge")),
            "counts": counts, "checks": checks}


@router.post("/{source_id}/active", dependencies=[Depends(require_local_origin)])
def set_active(source_id: int, payload: ActiveIn,
               db: Session = Depends(get_db)) -> Dict[str, Any]:
    record = SourceService(db).set_active(source_id, payload.active)
    if not record:
        raise HTTPException(status_code=404, detail="Source not found")
    return record.to_dict()


@router.delete("/{source_id}", dependencies=[Depends(require_local_origin)])
def forget(source_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    if not SourceService(db).forget(source_id):
        raise HTTPException(status_code=404, detail="Source not found")
    return {"ok": True, "forgotten": source_id}


@router.post("/sharepoint/auth/start", dependencies=[Depends(require_local_origin)])
def sharepoint_auth_start() -> Dict[str, Any]:
    return SharePointService().begin_login()


@router.get("/sharepoint/auth/status", dependencies=[Depends(require_local_origin)])
def sharepoint_auth_status() -> Dict[str, Any]:
    return SharePointService().login_state()


@router.delete("/sharepoint/auth", dependencies=[Depends(require_local_origin)])
def sharepoint_disconnect() -> Dict[str, Any]:
    return SharePointService().disconnect()


@router.get("/sharepoint/search", dependencies=[Depends(require_local_origin)])
def sharepoint_search(query: str = Query(..., min_length=2), limit: int = 10) -> Dict[str, Any]:
    try:
        results = SharePointService().search(query, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"query": query, "count": len(results), "results": results}


@router.post("/sharepoint/open", dependencies=[Depends(require_local_origin)])
def sharepoint_open(payload: SharePointOpenIn,
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    service = SharePointService()
    try:
        if payload.drive_id and payload.item_id:
            return service.open_item(payload.drive_id, payload.item_id, payload.name,
                                     payload.web_url, db)
        if payload.web_url:
            return service.open_url(payload.web_url, db)
        raise ValueError("Give a SharePoint URL or a drive/item ID.")
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
