"""
API for chat — the real conversation with Cerebro.

``POST /api/chat/message`` is the front door. With an AI provider, the model
answers and decides for itself when to search, read, draft or create a task;
without one, questions get an honest reply listing relevant sources and
instructions become tasks. ``POST /api/chat/stream`` is the same, streamed as
Server-Sent Events with progress as it happens.
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


@router.post("/stream", dependencies=[Depends(require_local_origin)])
def stream_message(request: MessageIn):
    """
    The same as ``/message``, delivered as Server-Sent Events.

    ``event: card`` arrives as each step happens ("Searching knowledge
    base…", then its result), so the UI can show the work while the answer is
    still being put together. ``event: message`` carries the final reply —
    exactly what ``/message`` returns — and ends the stream. ``event: error``
    replaces it if something unexpected fails.
    """
    import json
    import queue
    import threading

    from fastapi.responses import StreamingResponse

    from app.core.database import SessionLocal

    if not request.message.strip() and not request.image:
        raise HTTPException(status_code=400, detail="Say something first.")

    events: "queue.Queue" = queue.Queue()

    def work():
        db = SessionLocal()
        try:
            result = ChatService(db).handle_message(
                request.message, context=_context(db), image=request.image,
                emit=lambda card: events.put(("card", card)))
            events.put(("message", result))
        except Exception as exc:  # noqa: BLE001 - reported to the client
            events.put(("error", {"detail": str(exc)}))
        finally:
            db.close()

    threading.Thread(target=work, daemon=True, name="cerebro-ask").start()

    def sse():
        while True:
            try:
                kind, payload = events.get(timeout=15)
            except queue.Empty:
                yield ": still working\n\n"
                continue
            yield f"event: {kind}\ndata: {json.dumps(payload, default=str)}\n\n"
            if kind in ("message", "error"):
                return

    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


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


@router.post("/changes/{action_id}/approve", dependencies=[Depends(require_local_origin)])
def approve_change(action_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Run an approved change to Dynamics, RightAnswers or another system."""
    return ChatService(db).handle_change(action_id, "approve")


@router.post("/changes/{action_id}/discard", dependencies=[Depends(require_local_origin)])
def discard_change(action_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    return ChatService(db).handle_change(action_id, "discard")


@router.get("/changes", dependencies=[Depends(require_local_origin)])
def list_changes(status: Optional[str] = None, limit: int = Query(30, ge=1, le=200),
                 db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Proposed and completed changes, newest first — the approvals queue."""
    from app.models.agent_action import AgentAction

    query = db.query(AgentAction)
    if status:
        query = query.filter(AgentAction.status == status)
    rows = query.order_by(AgentAction.id.desc()).limit(limit).all()
    return {"count": len(rows), "changes": [row.to_dict() for row in rows]}


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
