"""
API for tasks and nudges — the secretary.

``POST /api/tasks/instruct`` is the front door: give it a sentence and Cerebro
turns it into a scheduled task. Everything else inspects, runs or resolves.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.system import require_local_origin
from app.core.database import get_db
from app.models.nudge import Nudge
from app.models.task import Task
from app.services.context_engine import ContextEngine
from app.services.nudge_service import NudgeService
from app.services.task_service import TaskService

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class InstructIn(BaseModel):
    instruction: str


class RunNow(BaseModel):
    pass


def _context(db: Session) -> Dict[str, Any]:
    context = ContextEngine(db).get_current_context()
    if context is None:
        return {}
    payload = context.to_dict()
    # "this document" resolves to whatever is in view.
    if context.active_application == "Document" and context.window_title:
        from app.models.tracked_document import TrackedDocument

        record = (db.query(TrackedDocument)
                  .filter(TrackedDocument.name == context.window_title).first())
        payload["active_document"] = record.path if record else context.window_title
    return payload


@router.post("/instruct", dependencies=[Depends(require_local_origin)])
def instruct(request: InstructIn, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Create a task from natural language, using the current context."""
    if not request.instruction.strip():
        raise HTTPException(status_code=400, detail="Say what you'd like me to do.")
    task = TaskService(db).create_from_instruction(
        request.instruction, context=_context(db))
    return {"ok": True, "task": task.to_dict(),
            "message": _confirm(task)}


def _confirm(task: Task) -> str:
    """A human confirmation of what was understood, in persona."""
    from app.services.style_service import persona

    subject = "We'll" if persona() == "partner" else "I'll"
    when = {
        "once": f"once, {'today' if task.at_time else 'shortly'}",
        "daily": f"every day{' at ' + task.at_time if task.at_time else ''}",
        "weekdays": f"on weekdays{' at ' + task.at_time if task.at_time else ''}",
        "weekly": "weekly",
        "hourly": "every hour",
        "manual": "when you ask",
    }.get(task.schedule, task.schedule)
    return f"{subject} {task.title.lower()} — {when}."


@router.get("", dependencies=[Depends(require_local_origin)])
def list_tasks(status: Optional[str] = None, limit: int = Query(100, ge=1, le=200),
               db: Session = Depends(get_db)) -> Dict[str, Any]:
    tasks = TaskService(db).list_tasks(status=status, limit=limit)
    return {"count": len(tasks), "tasks": [t.to_dict() for t in tasks]}


@router.get("/status")
def status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    payload = TaskService(db).status()
    payload["nudges"] = NudgeService(db).status()
    return payload


@router.post("/{task_id}/run", dependencies=[Depends(require_local_origin)])
def run_now(task_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    task = db.query(Task).get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    result = TaskService(db).run(task)
    return {"ok": result.get("ok", True), "result": result, "task": task.to_dict()}


@router.post("/{task_id}/cancel", dependencies=[Depends(require_local_origin)])
def cancel(task_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    task = db.query(Task).get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = "cancelled"
    task.next_run = None
    db.commit()
    return task.to_dict()


@router.delete("/{task_id}", dependencies=[Depends(require_local_origin)])
def delete_task(task_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    task = db.query(Task).get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    db.delete(task)
    db.commit()
    return {"ok": True, "deleted": task_id}


# ------------------------------------------------------------------ nudges
@router.get("/nudges", dependencies=[Depends(require_local_origin)])
def list_nudges(limit: int = Query(20, ge=1, le=100),
                db: Session = Depends(get_db)) -> Dict[str, Any]:
    nudges = NudgeService(db).open_nudges(limit=limit)
    return {"count": len(nudges), "nudges": [n.to_dict() for n in nudges]}


@router.post("/nudges/scan", dependencies=[Depends(require_local_origin)])
def scan(db: Session = Depends(get_db)) -> Dict[str, Any]:
    return NudgeService(db).scan()


@router.post("/nudges/{nudge_id}/dismiss", dependencies=[Depends(require_local_origin)])
def dismiss(nudge_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    nudge = db.query(Nudge).get(nudge_id)
    if not nudge:
        raise HTTPException(status_code=404, detail="Nudge not found")
    NudgeService(db).resolve(nudge, status="dismissed")
    return {"ok": True}


@router.post("/nudges/{nudge_id}/act", dependencies=[Depends(require_local_origin)])
def act(nudge_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Carry out a nudge's offered action (e.g. draft the reply it suggested)."""
    nudge = db.query(Nudge).get(nudge_id)
    if not nudge:
        raise HTTPException(status_code=404, detail="Nudge not found")

    action = nudge.to_dict().get("action") or {}
    result: Dict[str, Any] = {"handled": action.get("type", "none")}

    if action.get("type") == "draft_reply" and action.get("message_id"):
        from app.api.enterprise import DraftRequest, draft_reply

        try:
            result["draft"] = draft_reply(action["message_id"], DraftRequest(), db)
        except HTTPException as exc:
            result["error"] = exc.detail
    elif action.get("type") == "summarise_case" and action.get("case_id"):
        result["hint"] = (f"Open case {action['case_id']} and choose Summarise, "
                          "or POST /api/cases/{id}/summarise.")

    NudgeService(db).resolve(nudge, status="acted")
    return {"ok": True, "result": result}
