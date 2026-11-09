"""
Briefings — the secretary's morning note, end-of-day and weekly summaries,
and a brief before each meeting.

Each is put together from what Cerebro already has (no AI needed): your
calendar, urgent mail and mentions, cases needing you, follow-ups due,
watches that fired, ideas, and changes waiting for approval; summaries add
what got done (job runs, sent messages, approved changes). A briefing is
stored once per day/week/meeting, posted in the default agent's "Briefings"
chat, raised as a notice, and shown on Home → Today.

The morning, evening and weekly ones are ordinary recurring jobs (kind
``briefing``) made on first start, so they can be edited, paused or
switched off like any other job; the settings under Settings → Secretary
keep them in step.
"""

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.core import logger
from app.core.config import settings

KINDS = ("morning", "evening", "weekly", "meeting")
TITLES = {"morning": "Morning briefing", "evening": "End-of-day summary", "weekly": "Weekly summary"}


# ------------------------------------------------------------- gathering
def _calendar(day: datetime, days: int = 1) -> Optional[List[Dict[str, Any]]]:
    from app.services import calendar

    if not calendar.available():
        return None
    try:
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        return calendar.events(start, start + timedelta(days=days))
    except Exception as exc:  # noqa: BLE001 - the rest of the briefing still helps
        logger.warn("briefing", "Couldn't read the calendar", {"error": str(exc)[:200]})
        return None


def gather(db, kind: str, now: datetime = None) -> Dict[str, Any]:
    from app.models.agent_action import AgentAction
    from app.models.case import Case
    from app.models.enterprise import EnterpriseAction, EnterpriseMessage
    from app.models.task import Task
    from app.models.work import Idea, TaskEvent, Watch
    from app.services import followups

    now = now or datetime.now()
    span = timedelta(days=7) if kind == "weekly" else timedelta(days=1)
    since = now - (timedelta(hours=18) if kind == "morning" else span)
    data: Dict[str, Any] = {"kind": kind, "at": now.isoformat()}
    if kind == "morning":
        meetings = _calendar(now)
        data["meetings"] = None if meetings is None else [
            {"subject": m["subject"], "start": m["start"].isoformat(), "end": m["end"].isoformat(),
             "location": m.get("location"), "people": [a["name"] or a["email"] for a in m["attendees"]][:6]}
            for m in meetings if not m.get("all_day")]
    messages = (db.query(EnterpriseMessage).filter(EnterpriseMessage.timestamp >= since)
                .order_by(EnterpriseMessage.timestamp.desc()).limit(300).all())
    data["urgent"] = [{"who": m.sender_name or m.sender, "subject": m.subject or (m.preview or "")[:80],
                       "source": m.source, "why": m.urgency_reason}
                      for m in messages if m.urgency in ("high", "urgent") and not m.handled][:8]
    data["mentions"] = [{"who": m.sender_name or m.sender, "subject": m.subject or (m.preview or "")[:80],
                         "source": m.source} for m in messages if m.mentioned and not m.handled][:6]
    data["messages"] = len(messages)
    data["cases"] = [{"case": c.case_id, "customer": c.customer, "title": c.title}
                     for c in db.query(Case).filter(Case.status.in_(("open", "active", "in progress")))
                     .order_by(Case.updated_at.asc()).limit(6)]
    items = followups.open_items(db)
    horizon = now + (timedelta(days=7) if kind == "weekly" else timedelta(days=1))
    data["followups"] = [followups.describe(i) for i in items
                         if (i.due_at and i.due_at <= horizon) or followups.chase_due(i)][:10]
    data["todos"] = len(items)
    data["watches"] = [{"title": w.title, "at": w.last_alert_at.isoformat()}
                       for w in db.query(Watch).filter(Watch.last_alert_at >= since).limit(6)]
    data["ideas"] = [i.title for i in db.query(Idea).filter(Idea.status == "new")
                     .order_by(Idea.id.desc()).limit(4)]
    data["waiting"] = [t.title for t in db.query(Task).filter(
        Task.status.in_(("waiting_input", "waiting_approval"))).limit(8)]
    data["approvals"] = db.query(AgentAction.id).filter(AgentAction.status == "draft").count()
    if kind in ("evening", "weekly"):
        runs = (db.query(Task.title).join(TaskEvent, TaskEvent.task_id == Task.id)
                .filter(TaskEvent.kind == "result", TaskEvent.created_at >= since,
                        TaskEvent.title == "Finished", Task.kind != "briefing").all())
        data["job_runs"] = len(runs)
        data["done_jobs"] = sorted({row[0] for row in runs})[:10]
        data["sent"] = db.query(EnterpriseAction.id).filter(
            EnterpriseAction.status == "sent", EnterpriseAction.updated_at >= since).count()
        data["changes"] = [a.title for a in db.query(AgentAction).filter(
            AgentAction.status == "done", AgentAction.updated_at >= since).limit(20)]
        data["closed_cases"] = db.query(Case.id).filter(Case.closed_at >= since).count()
    return data


def render(data: Dict[str, Any]) -> str:
    kind = data["kind"]
    lines: List[str] = []
    if kind == "morning":
        meetings = data.get("meetings")
        if meetings is None:
            lines.append("**Today** — connect Outlook to see your meetings here.")
        elif meetings:
            lines.append(f"**Today: {len(meetings)} meeting{'s' if len(meetings) != 1 else ''}**")
            for m in meetings:
                start = datetime.fromisoformat(m["start"])
                people = f" — {', '.join(m['people'])}" if m["people"] else ""
                lines.append(f"- {start:%H:%M} {m['subject']}{people}")
        else:
            lines.append("**Today:** no meetings.")
    else:
        lines.append(f"**Done** — {data.get('job_runs', 0)} job run(s), {data.get('sent', 0)} message(s) "
                     f"sent, {len(data.get('changes') or [])} change(s) made, "
                     f"{data.get('closed_cases', 0)} case(s) closed.")
        for title in (data.get("done_jobs") or [])[:6] + (data.get("changes") or [])[:6]:
            lines.append(f"- {title}")
    if data.get("urgent"):
        lines.append("\n**Urgent**")
        lines += [f"- {u['who']}: {u['subject']}" for u in data["urgent"]]
    if data.get("mentions"):
        lines.append("\n**You were mentioned**")
        lines += [f"- {m['who']}: {m['subject']}" for m in data["mentions"]]
    if data.get("followups"):
        lines.append("\n**Follow-ups due**")
        lines += [f"- {f}" for f in data["followups"]]
    if data.get("cases"):
        lines.append("\n**Cases needing you**")
        lines += [f"- {c['case']} {c['customer'] or ''} — {c['title'] or ''}".rstrip(" —") for c in data["cases"]]
    if data.get("watches"):
        lines.append("\n**Watches that fired**")
        lines += [f"- {w['title']}" for w in data["watches"]]
    waiting = data.get("waiting") or []
    if waiting or data.get("approvals"):
        lines.append(f"\n**Waiting on you** — {data.get('approvals', 0)} change(s) to approve"
                     + (f"; jobs: {', '.join(waiting[:5])}" if waiting else ""))
    if data.get("ideas"):
        lines.append("\n**Ideas**: " + "; ".join(data["ideas"]))
    if kind != "morning":
        lines.append(f"\n**Still open**: {data.get('todos', 0)} follow-up(s) and to-do(s).")
    return "\n".join(lines).strip()


def spoken(data: Dict[str, Any]) -> str:
    """The short version read aloud."""
    parts = []
    meetings = data.get("meetings")
    if meetings:
        first = datetime.fromisoformat(meetings[0]["start"])
        parts.append(f"You have {len(meetings)} meeting{'s' if len(meetings) != 1 else ''} today, "
                     f"the first at {first:%H:%M}: {meetings[0]['subject']}.")
    elif meetings == []:
        parts.append("No meetings today.")
    if data.get("urgent"):
        parts.append(f"{len(data['urgent'])} urgent message{'s' if len(data['urgent']) != 1 else ''}.")
    if data.get("followups"):
        parts.append(f"{len(data['followups'])} follow-up{'s' if len(data['followups']) != 1 else ''} due.")
    if data.get("approvals"):
        parts.append(f"{data['approvals']} change{'s' if data['approvals'] != 1 else ''} waiting for you.")
    return " ".join(parts) or "Nothing needs you right now."


# ---------------------------------------------------------------- posting
def _key(kind: str, now: datetime) -> str:
    if kind == "weekly":
        return f"weekly:{now:%G-W%V}"
    return f"{kind}:{now:%Y-%m-%d}"


def make(db, kind: str, now: datetime = None, force: bool = False):
    """Put together and post a briefing (once per day/week unless ``force``)."""
    from app.models.work import Briefing

    now = now or datetime.now()
    key = _key(kind, now)
    existing = db.query(Briefing).filter(Briefing.key == key).first()
    if existing is not None and not force:
        return existing
    data = gather(db, kind, now)
    content = render(data)
    data["spoken"] = spoken(data)
    title = f"{TITLES[kind]} — {now:%a %d %b}"
    if existing is not None:
        existing.content, existing.data, existing.title = content, json.dumps(data, default=str), title
        briefing = existing
    else:
        briefing = Briefing(kind=kind, key=key, title=title, content=content,
                            data=json.dumps(data, default=str))
        db.add(briefing)
    db.commit()
    db.refresh(briefing)
    post(db, briefing)
    return briefing


def post(db, briefing) -> None:
    from app.core import activity_state
    from app.services.chat_service import ChatService

    chat = _chat(db)
    ChatService(db, chat.id)._store("assistant", f"**{briefing.title}**\n\n{briefing.content}",
                                    kind="briefing", meta={"briefing": {"id": briefing.id,
                                                                        "kind": briefing.kind}})
    data = json.loads(briefing.data or "{}")
    activity_state.notice(briefing.title, data.get("spoken") or briefing.content[:200], kind="briefing",
                          link={"tab": "home"})


def _chat(db):
    from app.models.conversation import Conversation
    from app.services import agents, conversations

    agent = agents.default_agent(db)
    chat = (db.query(Conversation).filter(Conversation.title == "Briefings",
                                          Conversation.agent_id == agent.id,
                                          Conversation.archived.is_(False)).first())
    if chat is None:
        chat = conversations.create(db, "Briefings", "Your briefings and meeting notes.",
                                    agent_id=agent.id)
    return chat


def latest(db, kind: str = None):
    from app.models.work import Briefing

    query = db.query(Briefing)
    if kind:
        query = query.filter(Briefing.kind == kind)
    return query.order_by(Briefing.id.desc()).first()


# ---------------------------------------------------------- the jobs
def ensure_jobs(db) -> List[int]:
    """The recurring briefing jobs, made once and kept in step with the settings."""
    from app.models.task import Task
    from app.services.task_service import compute_next_run

    wanted = {"morning": (settings.BRIEFING_ENABLED, "weekdays", settings.BRIEFING_TIME or "08:30"),
              "evening": (settings.END_OF_DAY_SUMMARY, "weekdays", "17:00"),
              "weekly": (settings.WEEKLY_SUMMARY, "weekly", "16:00")}
    made = []
    for kind, (enabled, schedule, at) in wanted.items():
        task = (db.query(Task).filter(Task.kind == "briefing", Task.source == f"briefing:{kind}").first())
        if task is None:
            if not enabled:
                continue
            next_run = compute_next_run(schedule, at)
            if kind == "weekly":
                while next_run.weekday() != 4:         # Fridays
                    next_run += timedelta(days=1)
            task = Task(title=TITLES[kind], instruction=TITLES[kind], kind="briefing",
                        spec=json.dumps({"briefing": kind}), schedule=schedule, at_time=at,
                        status="active", source=f"briefing:{kind}", next_run=next_run)
            db.add(task)
            db.commit()
            made.append(task.id)
            continue
        from app.services import jobs

        if not enabled and task.status == "active":
            task.status = "paused"
            jobs.set_state(task, paused_by="settings")
        elif enabled and task.status == "paused" and task.state_dict().get("paused_by") == "settings":
            task.status, task.next_run = "active", compute_next_run(schedule, at)
            jobs.set_state(task, paused_by=None)
        if kind == "morning" and task.at_time != at and enabled:
            task.at_time, task.next_run = at, compute_next_run(schedule, at)
        db.commit()
    return made


def run_job(db, task, spec: Dict[str, Any]) -> Dict[str, Any]:
    """The ``briefing`` job kind."""
    kind = spec.get("briefing") or "morning"
    now = datetime.now()
    if kind == "weekly" and now.weekday() != 4:
        return {"ok": True, "status": None, "summary": "Not Friday", "detail": ""}
    briefing = make(db, kind, now)
    return {"ok": True, "status": None, "summary": briefing.title, "detail": briefing.content[:1500]}


# ---------------------------------------------------------- meeting briefs
def meeting_brief(db, event: Dict[str, Any]) -> str:
    from app.models.case import Case
    from app.models.enterprise import EnterpriseMessage

    people = [a for a in event["attendees"] if a.get("name") or a.get("email")]
    lines = [f"**{event['subject']}** at {event['start']:%H:%M}"
             + (f" ({event['location']})" if event.get("location") else "")]
    if people:
        lines.append("Who's coming: " + ", ".join(p["name"] or p["email"] for p in people[:10]))
    since = datetime.now() - timedelta(days=14)
    recent, customers = [], set()
    for person in people[:10]:
        keys = [k for k in (person.get("email"), person.get("name")) if k]
        if not keys:
            continue
        query = db.query(EnterpriseMessage).filter(EnterpriseMessage.timestamp >= since)
        rows = [m for m in query.order_by(EnterpriseMessage.timestamp.desc()).limit(400)
                if any(k.lower() in ((m.sender or "") + " " + (m.sender_name or "")).lower() for k in keys)][:3]
        for message in rows:
            recent.append(f"- {message.sender_name or message.sender} ({message.source}, "
                          f"{message.timestamp:%d %b}): {message.subject or (message.preview or '')[:80]}")
            if message.customer:
                customers.add(message.customer)
        if person.get("email") and "@" in person["email"]:
            customers.add(person["email"].split("@", 1)[1].split(".")[0])
    if recent:
        lines.append("\nRecently with them:\n" + "\n".join(recent[:8]))
    cases = []
    for customer in list(customers)[:5]:
        cases += db.query(Case).filter(Case.customer.ilike(f"%{customer}%"),
                                       Case.status.in_(("open", "active", "in progress"))).limit(3).all()
    if cases:
        lines.append("\nTheir open cases:\n" + "\n".join(f"- {c.case_id} {c.title or ''}" for c in cases[:6]))
    if (event.get("body") or "").strip():
        lines.append("\nAgenda:\n" + event["body"].strip()[:800])
    if event.get("online_url"):
        lines.append(f"\nJoin: {event['online_url']}")
    return "\n".join(lines)


def meeting_briefs(db, now: datetime = None) -> int:
    """Post a brief for each meeting starting within MEETING_BRIEF_MINUTES."""
    from app.models.work import Briefing
    from app.services import calendar

    lead = int(settings.MEETING_BRIEF_MINUTES or 0)
    if lead <= 0 or not calendar.available():
        return 0
    now = now or datetime.now()
    try:
        upcoming = calendar.events(now, now + timedelta(minutes=lead + 1))
    except Exception as exc:  # noqa: BLE001
        logger.warn("briefing", "Couldn't read the calendar", {"error": str(exc)[:200]})
        return 0
    posted = 0
    for event in upcoming:
        if event.get("all_day") or event["start"] < now - timedelta(minutes=1):
            continue
        key = f"meeting:{event['id']}:{event['start']:%Y%m%d%H%M}"[:250]
        if db.query(Briefing.id).filter(Briefing.key == key).first():
            continue
        content = meeting_brief(db, event)
        briefing = Briefing(kind="meeting", key=key, title=f"Before {event['subject']}"[:120],
                            content=content, data=json.dumps({"event": calendar.plain(event),
                                                              "spoken": f"{event['subject']} starts at "
                                                                        f"{event['start']:%H:%M}."},
                                                             default=str))
        db.add(briefing)
        db.commit()
        post(db, briefing)
        posted += 1
    return posted


def tick(db) -> None:
    """The scheduler's minute tick for the secretary."""
    try:
        meeting_briefs(db)
    except Exception as exc:  # noqa: BLE001 - never stops the scheduler
        db.rollback()
        logger.warn("briefing", "Meeting briefs failed", {"error": str(exc)[:200]})
