"""
API for the secretary: Home → Today (agenda, to-dos, briefing), follow-ups,
briefings and the calendar.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.system import require_local_origin
from app.core.database import get_db
from app.services import briefings, calendar, followups

router = APIRouter(prefix="/api", tags=["secretary"], dependencies=[Depends(require_local_origin)])


@router.get("/today")
def today(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Everything for Home → Today."""
    from app.models.agent_action import AgentAction
    from app.models.task import Task

    agenda, problem = None, None
    if calendar.available():
        try:
            agenda = [calendar.plain(e) for e in calendar.today()]
        except Exception as exc:  # noqa: BLE001 - shown on the card
            problem = str(exc)[:200]
    items = followups.open_items(db, limit=40)
    todos = [{**i.to_dict(), "label": followups.describe(i), "chase": followups.chase_due(i)} for i in items]
    latest = briefings.latest(db)
    morning = briefings.latest(db, "morning")
    show = morning if morning and morning.created_at and morning.created_at.date() == datetime.now().date() \
        else latest
    waiting = [t.to_dict() for t in db.query(Task).filter(
        Task.status.in_(("waiting_input", "waiting_approval"))).order_by(Task.updated_at.desc()).limit(10)]
    approvals = db.query(AgentAction.id).filter(AgentAction.status == "draft").count()
    return {"agenda": agenda, "calendar_connected": calendar.available(), "calendar_error": problem,
            "todos": todos, "briefing": show.to_dict() if show else None,
            "waiting": waiting, "approvals": approvals}


# ---------------------------------------------------------------- follow-ups
class FollowUpIn(BaseModel):
    what: str
    kind: str = "todo"
    who: Optional[str] = None
    due: Optional[str] = None


@router.get("/followups")
def list_followups(status: str = "open", db: Session = Depends(get_db)) -> Dict[str, Any]:
    from app.models.work import FollowUp

    if status == "open":
        rows = followups.open_items(db)
    else:
        rows = (db.query(FollowUp).filter(FollowUp.status == status)
                .order_by(FollowUp.id.desc()).limit(100).all())
    return {"followups": [{**r.to_dict(), "label": followups.describe(r), "chase": followups.chase_due(r)}
                          for r in rows]}


@router.post("/followups")
def add_followup(body: FollowUpIn, db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        item = followups.add(db, body.what, body.kind, who=body.who,
                             due_at=followups.due_from(body.due) if body.due else None, source="manual")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return item.to_dict()


@router.post("/followups/refresh")
def refresh_followups(db: Session = Depends(get_db)) -> Dict[str, Any]:
    return followups.run(db)


@router.post("/followups/{item_id}/{action}")
def act_on_followup(item_id: int, action: str, body: Dict[str, Any] = None,
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    from app.models.work import FollowUp
    from app.services.ask_tools import _draft_card

    item = db.get(FollowUp, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="No such follow-up")
    if action == "chase":
        result = followups.chase(db, item)
        if not result["ok"]:
            raise HTTPException(status_code=400, detail=result["detail"])
        return {"ok": True, "card": _draft_card(result["action"])}
    until = None
    if action == "snooze" and (body or {}).get("days"):
        until = datetime.now() + timedelta(days=float(body["days"]))
    return followups.act(db, item, action, until)


# ---------------------------------------------------------------- briefings
@router.get("/briefings")
def list_briefings(kind: Optional[str] = None, db: Session = Depends(get_db)) -> Dict[str, Any]:
    from app.models.work import Briefing

    query = db.query(Briefing)
    if kind:
        query = query.filter(Briefing.kind == kind)
    return {"briefings": [b.to_dict() for b in query.order_by(Briefing.id.desc()).limit(30)]}


@router.post("/briefings/{kind}")
def make_briefing(kind: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    if kind not in ("morning", "evening", "weekly"):
        raise HTTPException(status_code=400, detail="morning, evening or weekly")
    return briefings.make(db, kind, force=True).to_dict()


# ---------------------------------------------------------------- calendar
@router.get("/calendar")
def get_calendar(start: Optional[str] = None, days: int = 7) -> Dict[str, Any]:
    if not calendar.available():
        return {"connected": False, "events": []}
    begin = calendar.parse_time(start) if start else datetime.now().replace(hour=0, minute=0, second=0,
                                                                           microsecond=0)
    try:
        found = calendar.events(begin, begin + timedelta(days=max(1, min(days, 31))))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))
    return {"connected": True, "events": [calendar.plain(e) for e in found]}
