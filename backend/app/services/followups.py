"""
Follow-ups and to-dos — the secretary's list.

Found by themselves from what Cerebro already sees:

* **waiting on** — a message you sent (through Cerebro) that asks someone for
  something, with no reply in that thread yet. After ``FOLLOWUP_CHASE_DAYS``
  it's due to chase; Chase drafts a polite follow-up for approval. A reply in
  the thread closes it.
* **promised** — a message you sent that promises something ("I'll send it
  Friday"). It's due when you said, and you're nudged the day before.
* **asked** — a message to you that asks you for something.

Added by hand, by meeting notes (action items) or by Ask (``add_todo``):
**todo**. Everything is shown with the words it came from, and can be marked
done or snoozed. A cheap rule pass finds them; nothing leaves the PC.
"""

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.core import logger
from app.core.config import settings

KINDS = ("waiting_on", "promised", "asked", "todo")
LOOKBACK_DAYS = 14

_REQUEST = re.compile(
    r"(\bcan you\b|\bcould you\b|\bwould you\b|\bplease (send|share|confirm|let me know|check|review|"
    r"update|provide|advise)\b|\blet me know\b|\bget back to me\b|\bany update\b|\bwhen can\b|"
    r"\bdo you have\b|\bare you able\b)", re.I)
_PROMISE = re.compile(
    r"\b(i'll|i will|i shall|i'm going to|will get back|i can have|leave it with me|i'll get)\b"
    r"[^.!?\n]{3,120}", re.I)
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


# ------------------------------------------------------------------ dates
def due_from(text: str, base: datetime = None) -> Optional[datetime]:
    """'by Friday', 'tomorrow', 'end of day', 'next week', '12/11' → a due date (end of day)."""
    base = base or datetime.now()
    lowered = (text or "").lower()
    eod = base.replace(hour=17, minute=0, second=0, microsecond=0)
    if "today" in lowered or "end of day" in lowered or "eod" in lowered or "this afternoon" in lowered:
        return eod
    if "tomorrow" in lowered:
        return eod + timedelta(days=1)
    for index, name in enumerate(_DAYS):
        if re.search(rf"\b{name}\b", lowered):
            ahead = (index - base.weekday()) % 7 or 7
            return eod + timedelta(days=ahead)
    if "next week" in lowered:
        return eod + timedelta(days=7 - base.weekday())
    if "end of the week" in lowered or "this week" in lowered:
        return eod + timedelta(days=max(0, 4 - base.weekday()))
    match = re.search(r"\b(\d{1,2})/(\d{1,2})\b", lowered)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        try:
            found = eod.replace(month=month, day=day)
        except ValueError:
            return None
        return found if found >= base - timedelta(days=1) else found.replace(year=found.year + 1)
    return None


def _sentence(text: str, match) -> str:
    start = max(text.rfind(mark, 0, match.start()) for mark in ".?!\n") + 1
    end_candidates = [i for i in (text.find(mark, match.end()) for mark in ".?!\n") if i >= 0]
    end = min(end_candidates) + 1 if end_candidates else len(text)
    return re.sub(r"\s+", " ", text[start:end]).strip()[:300]


# ------------------------------------------------------------------ store
def add(db, what: str, kind: str = "todo", who: str = None, due_at: datetime = None,
        source: str = "manual", evidence: str = None, ref: str = None, thread_id: str = None,
        agent_id: int = None, conversation_id: int = None, started_at: datetime = None):
    """Add an item (or return the one already made for ``ref``). ``started_at``
    is when it began — when the request was sent — for chasing."""
    from app.models.work import FollowUp

    if not (what or "").strip():
        raise ValueError("Say what it is.")
    if ref:
        existing = db.query(FollowUp).filter(FollowUp.ref == ref, FollowUp.kind == kind).first()
        if existing is not None:
            return existing
    item = FollowUp(kind=kind if kind in KINDS else "todo", who=(who or None), what=what.strip()[:500],
                    due_at=due_at, source=source, evidence=(evidence or None), ref=ref,
                    thread_id=thread_id, status="open", agent_id=agent_id,
                    conversation_id=conversation_id, **({"created_at": started_at} if started_at else {}))
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def open_items(db, limit: int = 100) -> List[Any]:
    from app.models.work import FollowUp

    now = datetime.now()
    rows = (db.query(FollowUp).filter(FollowUp.status.in_(("open", "snoozed")))
            .order_by(FollowUp.due_at.asc().nullslast(), FollowUp.id.desc()).limit(limit).all())
    out = []
    for row in rows:
        if row.status == "snoozed":
            if row.snooze_until and row.snooze_until > now:
                continue
            row.status = "open"
        out.append(row)
    db.commit()
    return out


def describe(item) -> str:
    label = {"waiting_on": f"Waiting on {item.who or 'them'}", "promised": "You promised",
             "asked": f"{item.who or 'Someone'} asked you", "todo": "To do"}.get(item.kind, "To do")
    due = f" (due {item.due_at:%a %d %b})" if item.due_at else ""
    return f"#{item.id} {label}: {item.what}{due}"


def chase_due(item) -> bool:
    if item.kind != "waiting_on" or item.status != "open":
        return False
    base = item.chased_at or item.created_at or datetime.now()
    return datetime.now() - base >= timedelta(days=max(1, int(settings.FOLLOWUP_CHASE_DAYS or 3)))


def act(db, item, action: str, until: datetime = None) -> Dict[str, Any]:
    if action == "done":
        item.status, item.done_at = "done", datetime.now()
    elif action == "snooze":
        item.status = "snoozed"
        item.snooze_until = until or (datetime.now() + timedelta(days=1))
    elif action == "reopen":
        item.status, item.done_at = "open", None
    else:
        return {"ok": False, "detail": f"Unknown action {action}"}
    db.commit()
    return {"ok": True, "item": item.to_dict()}


def chase(db, item) -> Dict[str, Any]:
    """Draft a polite follow-up in the original thread, for approval."""
    from app.models.enterprise import EnterpriseAction
    from app.services.enterprise_service import EnterpriseService

    original = None
    if item.ref and item.ref.startswith("action:"):
        original = db.get(EnterpriseAction, int(item.ref.split(":", 1)[1]))
    first = (item.who or "").split("@")[0].split(".")[0].split()[0].title() if item.who else ""
    body = (f"Hi{' ' + first if first else ''},\n\nJust following up on this — "
            f"{item.what.rstrip('.?')}. Let me know if you need anything from me.\n\nThanks")
    service = EnterpriseService(db)
    if original is not None and original.in_reply_to:
        action = service.create_action("reply", body=body, source=original.source,
                                       in_reply_to=original.in_reply_to, send=False)
    elif original is not None and original.source == "teams" and original.chat_or_channel:
        action = service.create_action("send_teams_message", body=body, source="teams",
                                       chat_or_channel=original.chat_or_channel, send=False)
    else:
        recipients = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", item.who or "")
        if not recipients:
            return {"ok": False, "detail": f"Cerebro doesn't know how to reach {item.who or 'them'}."}
        subject = (original.subject if original is not None and original.subject else item.what)[:120]
        action = service.create_action("send_email", body=body, source="outlook", to=recipients,
                                       subject=f"Re: {subject}" if not subject.lower().startswith("re:")
                                       else subject, send=False)
    item.chased_at = datetime.now()
    db.commit()
    return {"ok": True, "action": action}


# ---------------------------------------------------------------- finding
def _new(db, ref: str, kind: str) -> bool:
    from app.models.work import FollowUp

    return db.query(FollowUp.id).filter(FollowUp.ref == ref, FollowUp.kind == kind).first() is None


def detect(db, now: datetime = None) -> Dict[str, int]:
    """One rule pass over recent sent and received messages."""
    from app.models.enterprise import EnterpriseAction, EnterpriseMessage
    from app.models.work import FollowUp

    now = now or datetime.now()
    since = now - timedelta(days=LOOKBACK_DAYS)
    made = {"waiting_on": 0, "promised": 0, "asked": 0, "closed": 0}
    sent = (db.query(EnterpriseAction)
            .filter(EnterpriseAction.status == "sent", EnterpriseAction.created_at >= since)
            .order_by(EnterpriseAction.id.desc()).limit(200).all())
    for action in sent:
        body = action.body or ""
        original = db.get(EnterpriseMessage, action.in_reply_to) if action.in_reply_to else None
        thread = action.thread_id or (original.thread_id if original else None)
        who = ", ".join(action.to_dict().get("to") or []) or action.chat_or_channel \
            or ((original.sender_name or original.sender) if original else None)
        request = _REQUEST.search(body) or re.search(r"[^.!?\n]{8,}\?", body)
        if request and _new(db, f"action:{action.id}", "waiting_on"):
            add(db, _sentence(body, request), "waiting_on", who=who, source=action.source or "mail",
                evidence=body[:600], ref=f"action:{action.id}", thread_id=thread,
                started_at=action.updated_at or action.created_at)
            made["waiting_on"] += 1
        for promise in _PROMISE.finditer(body):
            ref = f"action:{action.id}:{promise.start()}"
            if not _new(db, ref, "promised"):
                continue
            text = _sentence(body, promise)
            add(db, text, "promised", who=who, due_at=due_from(text, action.created_at or now),
                source=action.source or "mail", evidence=body[:600], ref=ref, thread_id=thread)
            made["promised"] += 1
    received = (db.query(EnterpriseMessage)
                .filter(EnterpriseMessage.timestamp >= since, EnterpriseMessage.handled.is_(False))
                .order_by(EnterpriseMessage.id.desc()).limit(300).all())
    for message in received:
        if not (message.direct or message.mentioned):
            continue
        body = message.body or message.preview or ""
        request = _REQUEST.search(body)
        if request and _new(db, f"message:{message.id}", "asked"):
            add(db, _sentence(body, request), "asked", who=message.sender_name or message.sender,
                due_at=due_from(body, message.timestamp or now), source=message.source or "mail",
                evidence=body[:600], ref=f"message:{message.id}", thread_id=message.thread_id)
            made["asked"] += 1
    # A reply in the thread answers what you were waiting on.
    for item in db.query(FollowUp).filter(FollowUp.kind == "waiting_on", FollowUp.status == "open",
                                          FollowUp.thread_id.isnot(None)).all():
        replied = (db.query(EnterpriseMessage.id)
                   .filter(EnterpriseMessage.thread_id == item.thread_id,
                           EnterpriseMessage.timestamp > (item.created_at or since)).first())
        if replied:
            item.status, item.done_at = "done", now
            made["closed"] += 1
    db.commit()
    return made


def nudges(db, now: datetime = None) -> int:
    """Tell the user about promises due within a day and requests due to chase."""
    from app.services.nudge_service import NudgeService

    now = now or datetime.now()
    raised = 0
    for item in open_items(db):
        if item.kind == "promised" and item.due_at and item.due_at - now <= timedelta(days=1):
            title = f"Due {'today' if item.due_at.date() == now.date() else item.due_at.strftime('%a')}: {item.what[:60]}"
        elif chase_due(item):
            title = f"No reply from {item.who or 'them'} yet: {item.what[:50]}"
        else:
            continue
        NudgeService(db).raise_nudge(title=title, body=item.evidence or item.what, kind="followup",
                                     dedupe_key=f"followup:{item.id}:{now:%Y%m%d}")
        raised += 1
    return raised


def run(db) -> Dict[str, int]:
    try:
        made = detect(db)
        made["nudged"] = nudges(db)
        return made
    except Exception as exc:  # noqa: BLE001 - the list is a nicety; never stops the scheduler
        db.rollback()
        logger.warn("followups", "Couldn't update follow-ups", {"error": str(exc)[:200]})
        return {}
