"""
API for chat — the real conversation with Cerebro.

``POST /api/chat/message`` is the front door: general questions get an answer,
instructions become tasks (asking for missing detail first, if any), and a
reply to a clarifying question completes the instruction it belongs to.
``GET /api/chat/history`` returns the thread so a client can render it.

``POST /api/chat/upload-image`` and ``GET /api/chat/image/{name}`` are the two
halves of image support: stash an upload, then let the widget (or anything
else local) fetch it back to render inline.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.system import require_local_origin
from app.api.tasks import _context
from app.core.database import get_db
from app.services import chat_images
from app.services.chat_images import ImageError
from app.services.chat_service import ChatService
from app.services.ask_tools import AskToolService

router = APIRouter(prefix="/api/chat", tags=["chat"])


class MessageIn(BaseModel):
    message: str = ""
    #: Stored name from a prior ``/upload-image`` call.
    image: Optional[str] = None


@router.post("/message", dependencies=[Depends(require_local_origin)])
def send_message(request: MessageIn, db: Session = Depends(get_db)) -> Dict[str, Any]:
    if not request.message.strip() and not request.image:
        raise HTTPException(status_code=400, detail="Say something first.")
    return ChatService(db).handle_message(request.message, context=_context(db),
                                          image=request.image)


@router.get("/history", dependencies=[Depends(require_local_origin)])
def history(limit: int = Query(50, ge=1, le=200),
           db: Session = Depends(get_db)) -> Dict[str, Any]:
    messages = ChatService(db).history(limit=limit)
    return {"count": len(messages), "messages": messages}


@router.get("/tools", dependencies=[Depends(require_local_origin)])
def tools() -> Dict[str, Any]:
    """Tools Ask can use and the safety mode applied to each one."""
    items = AskToolService.catalog()
    return {"count": len(items), "tools": items}


@router.post("/actions/{action_id}/approve", dependencies=[Depends(require_local_origin)])
def approve_action(action_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    return ChatService(db).handle_action(action_id, "approve")


@router.post("/actions/{action_id}/discard", dependencies=[Depends(require_local_origin)])
def discard_action(action_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    return ChatService(db).handle_action(action_id, "discard")


@router.post("/upload-image", dependencies=[Depends(require_local_origin)])
async def upload_image(file: UploadFile) -> Dict[str, Any]:
    # Read capped at one byte past the limit rather than the whole body, so an
    # oversized upload can't balloon memory before ``save_upload`` gets to see
    # (and reject) it.
    data = await file.read(chat_images.MAX_BYTES + 1)
    if len(data) > chat_images.MAX_BYTES:
        raise HTTPException(status_code=422, detail=(
            f"That image is too big (max {chat_images.MAX_BYTES // 1_000_000} MB)."))
    try:
        stored = chat_images.save_upload(data, filename=file.filename or "upload.png")
    except ImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"image": stored}


@router.get("/image/{name}", dependencies=[Depends(require_local_origin)])
def get_image(name: str) -> FileResponse:
    try:
        path = chat_images.resolve(name)
    except ImageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(str(path), media_type=chat_images.mime_type(name))
