"""
API for chat — the real conversation with Cerebro.

``POST /api/chat/message`` is the front door. With an AI provider, the model
answers and decides for itself when to search, read, draft or create a task;
without one, questions get an honest reply listing relevant sources and
instructions become tasks. ``POST /api/chat/stream`` is the same, streamed as
Server-Sent Events with progress as it happens.
``GET /api/chat/history`` returns the thread so a client can render it.

Ask has separate chats (``/api/chat/conversations``): every message route takes
a ``conversation_id`` and defaults to the most recently active chat. A chat
can carry standing instructions and have tasks assigned to it, whose results
post back into it.

``POST /api/chat/upload-image`` and ``GET /api/chat/image/{name}`` are the two
halves of image support: stash an upload, then let the widget (or anything
else local) fetch it back to render inline.
"""

import threading
from typing import Any, Dict, Optional, Set

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

#: Chats with an answer or task being worked on right now (for the list's
#: "working" dot, and so a client that switched away knows to refresh).
_WORKING: Dict[int, int] = {}
_WORKING_LOCK = threading.Lock()


def _working(conversation_id: int, delta: int) -> None:
    with _WORKING_LOCK:
        count = _WORKING.get(conversation_id, 0) + delta
        if count > 0:
            _WORKING[conversation_id] = count
        else:
            _WORKING.pop(conversation_id, None)


def _busy() -> Set[int]:
    with _WORKING_LOCK:
        return set(_WORKING)


def _service(db: Session, conversation_id: Optional[int]) -> ChatService:
    try:
        return ChatService(db, conversation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class MessageIn(BaseModel):
    message: str = ""
    #: Stored name from a prior ``/upload-image`` call.
    image: Optional[str] = None
    #: The chat this message belongs to; the latest open chat when omitted.
    conversation_id: Optional[int] = None


@router.post("/message", dependencies=[Depends(require_local_origin)])
def send_message(request: MessageIn, db: Session = Depends(get_db)) -> Dict[str, Any]:
    if not request.message.strip() and not request.image:
        raise HTTPException(status_code=400, detail="Say something first.")
    service = _service(db, request.conversation_id)
    _working(service.conversation_id, 1)
    try:
        result = service.handle_message(request.message, context=_context(db),
                                        image=request.image)
    finally:
        _working(service.conversation_id, -1)
    result["conversation"] = service.conversation.to_dict()
    return result


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
    with SessionLocal() as db:
        service = _service(db, request.conversation_id)
        conversation_id = service.conversation_id
    _working(conversation_id, 1)

    def work():
        # The answer is worked out (and stored) even if the client goes away —
        # switching chats mid-answer does not lose it.
        db = SessionLocal()
        try:
            service = ChatService(db, conversation_id)
            result = service.handle_message(
                request.message, context=_context(db), image=request.image,
                emit=lambda card: events.put(("card", card)))
            result["conversation"] = service.conversation.to_dict()
            events.put(("message", result))
        except Exception as exc:  # noqa: BLE001 - reported to the client
            events.put(("error", {"detail": str(exc)}))
        finally:
            _working(conversation_id, -1)
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
           conversation_id: Optional[int] = None,
           db: Session = Depends(get_db)) -> Dict[str, Any]:
    service = _service(db, conversation_id)
    messages = service.history(limit=limit)
    return {"count": len(messages), "messages": messages,
            "conversation": {**service.conversation.to_dict(),
                             "working": service.conversation_id in _busy()}}


# ------------------------------------------------------------ conversations
class ConversationIn(BaseModel):
    title: Optional[str] = None
    instructions: Optional[str] = None


class ConversationPatch(BaseModel):
    title: Optional[str] = None
    instructions: Optional[str] = None
    pinned: Optional[bool] = None
    archived: Optional[bool] = None


def _chat_or_404(db: Session, conversation_id: int):
    from app.services import conversations

    chat = conversations.get(db, conversation_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="That chat no longer exists.")
    return chat


def _with_status(item: Dict[str, Any], busy: Set[int]) -> Dict[str, Any]:
    item["working"] = item["id"] in busy
    return item


@router.get("/conversations", dependencies=[Depends(require_local_origin)])
def list_conversations(archived: bool = False, q: Optional[str] = None,
                       db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Chats, pinned first then most recent; ``q`` searches titles and messages."""
    from app.models.task import Task
    from app.services import conversations

    busy = _busy()
    items = [_with_status(item, busy) for item in conversations.listing(db, archived, q)]
    scheduled = {cid for (cid,) in db.query(Task.conversation_id)
                 .filter(Task.conversation_id.isnot(None), Task.status == "active")}
    for item in items:
        item["has_tasks"] = item["id"] in scheduled
    return {"count": len(items), "conversations": items}


@router.post("/conversations", dependencies=[Depends(require_local_origin)])
def create_conversation(request: ConversationIn = None,
                        db: Session = Depends(get_db)) -> Dict[str, Any]:
    from app.services import conversations

    request = request or ConversationIn()
    return conversations.create(db, request.title, request.instructions).to_dict()


@router.get("/conversations/{conversation_id}", dependencies=[Depends(require_local_origin)])
def get_conversation(conversation_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    return _with_status(_chat_or_404(db, conversation_id).to_dict(), _busy())


@router.patch("/conversations/{conversation_id}", dependencies=[Depends(require_local_origin)])
def update_conversation(conversation_id: int, request: ConversationPatch,
                        db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Rename, set instructions, pin, archive or restore a chat."""
    from app.services import conversations

    chat = _chat_or_404(db, conversation_id)
    return conversations.update(db, chat, **request.dict()).to_dict()


@router.post("/conversations/{conversation_id}/end", dependencies=[Depends(require_local_origin)])
def end_conversation(conversation_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """End a chat: archive it (still readable, restorable) and open a new one."""
    from app.services import conversations

    chat = _chat_or_404(db, conversation_id)
    fresh, stopped = conversations.end(db, chat)
    return {"ended": chat.to_dict(), "conversation": fresh.to_dict(), "tasks_stopped": stopped}


@router.delete("/conversations/{conversation_id}", dependencies=[Depends(require_local_origin)])
def delete_conversation(conversation_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    from app.services import conversations

    conversations.delete(db, _chat_or_404(db, conversation_id))
    return {"ok": True, "deleted": conversation_id}


class AssignTaskIn(BaseModel):
    instruction: str
    #: once | hourly | daily | weekdays | weekly | manual
    schedule: str = "once"
    #: HH:MM, for daily/weekday/weekly (and a one-off later today).
    at_time: Optional[str] = None
    title: Optional[str] = None
    #: Start it straight away, in the background, as well as on its schedule.
    run_now: bool = False


@router.get("/conversations/{conversation_id}/tasks", dependencies=[Depends(require_local_origin)])
def conversation_tasks(conversation_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Tasks assigned to this chat that are still live or need a look."""
    from app.models.task import Task

    _chat_or_404(db, conversation_id)
    rows = (db.query(Task).filter(Task.conversation_id == conversation_id,
                                  Task.status.in_(("active", "needs_review", "failed")))
            .order_by(Task.id.desc()).all())
    return {"count": len(rows), "tasks": [row.to_dict() for row in rows],
            "working": conversation_id in _busy()}


@router.post("/conversations/{conversation_id}/tasks",
             dependencies=[Depends(require_local_origin)])
def assign_task(conversation_id: int, request: AssignTaskIn,
                db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Give this chat a job: "every weekday at 8:45 list new cases assigned to me".

    It runs with the same tools as Ask and posts its result here; any change
    to an outside system waits as an approval card in this chat.
    """
    from app.services.task_service import TaskService, describe_confirmation

    chat = _chat_or_404(db, conversation_id)
    if not request.instruction.strip():
        raise HTTPException(status_code=400, detail="Say what the task should do.")
    try:
        task = TaskService(db).assign_to_chat(chat.id, request.instruction.strip(),
                                              request.schedule, request.at_time, request.title)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if request.run_now:
        if task.schedule == "once" and not task.at_time:
            # "Now, once": this run is the task; the scheduler must not repeat it.
            task.next_run = None
            db.commit()
        run_task_in_background(task.id)
    return {"task": task.to_dict(), "confirmation": describe_confirmation(task)}


@router.post("/conversations/{conversation_id}/tasks/{task_id}/run",
             dependencies=[Depends(require_local_origin)])
def run_conversation_task(conversation_id: int, task_id: int,
                          db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Run an assigned task now, in the background; its result posts to the chat."""
    from app.models.task import Task

    task = db.query(Task).filter(Task.id == task_id,
                                 Task.conversation_id == conversation_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found in this chat")
    run_task_in_background(task.id)
    return {"ok": True, "started": task.id}


def run_task_in_background(task_id: int) -> None:
    from app.core.database import SessionLocal
    from app.models.task import Task
    from app.services.task_service import TaskService

    with SessionLocal() as db:
        task = db.query(Task).get(task_id)
        conversation_id = task.conversation_id if task else None
    if conversation_id:
        _working(conversation_id, 1)

    def work():
        db = SessionLocal()
        try:
            task = db.query(Task).get(task_id)
            if task is not None:
                TaskService(db).run(task)
        finally:
            if conversation_id:
                _working(conversation_id, -1)
            db.close()

    threading.Thread(target=work, daemon=True, name=f"cerebro-task-{task_id}").start()


@router.get("/tools", dependencies=[Depends(require_local_origin)])
def tools() -> Dict[str, Any]:
    """Tools Ask can use and the safety mode applied to each one."""
    items = AskToolService.catalog()
    return {"count": len(items), "tools": items}


@router.post("/actions/{action_id}/approve", dependencies=[Depends(require_local_origin)])
def approve_action(action_id: int, conversation_id: Optional[int] = None, to: Optional[str] = None,
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Approve (or retry) a draft; ``to`` replaces the destination first."""
    return _service(db, conversation_id).handle_action(action_id, "approve", to=to)


@router.post("/actions/{action_id}/discard", dependencies=[Depends(require_local_origin)])
def discard_action(action_id: int, conversation_id: Optional[int] = None,
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    return _service(db, conversation_id).handle_action(action_id, "discard")


@router.post("/changes/{action_id}/approve", dependencies=[Depends(require_local_origin)])
def approve_change(action_id: int, conversation_id: Optional[int] = None,
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Run an approved change to Dynamics, RightAnswers or another system."""
    return _service(db, conversation_id).handle_change(action_id, "approve")


@router.post("/changes/{action_id}/discard", dependencies=[Depends(require_local_origin)])
def discard_change(action_id: int, conversation_id: Optional[int] = None,
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    return _service(db, conversation_id).handle_change(action_id, "discard")


@router.post("/changes/{action_id}/undo", dependencies=[Depends(require_local_origin)])
def undo_change(action_id: int, conversation_id: Optional[int] = None,
                db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Reverse a change that was made (automatically or after approval)."""
    return _service(db, conversation_id).handle_change(action_id, "undo")


@router.get("/changes", dependencies=[Depends(require_local_origin)])
def list_changes(status: Optional[str] = None, limit: int = Query(30, ge=1, le=200),
                 db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Proposed and completed changes, newest first — the approvals queue."""
    from app.models.agent_action import AgentAction

    query = db.query(AgentAction)
    if status:
        query = query.filter(AgentAction.status == status)
    from app.services.agent import actions

    rows = query.order_by(AgentAction.id.desc()).limit(limit).all()
    changes = [{**row.to_dict(), "automatic": bool(row.result_dict().get("automatic")),
                "can_undo": actions.can_undo(row)} for row in rows]
    return {"count": len(changes), "changes": changes}


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
