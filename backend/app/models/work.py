"""
Durable work, borrowed from OpenMuse: a job's run log, projects with
milestones, watches that re-check a page, and ideas with their evidence.
"""

import json

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


def _load(text, default):
    try:
        value = json.loads(text) if text else default
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def _iso(value):
    return value.isoformat() if value else None


class TaskEvent(Base):
    """One line of a job's run log — the receipts."""

    __tablename__ = "task_events"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, index=True)
    #: plan | step | tool | approval | input | result | error | status
    kind = Column(String, default="status")
    title = Column(String)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now(), index=True)

    def to_dict(self) -> dict:
        return {"id": self.id, "task_id": self.task_id, "kind": self.kind, "title": self.title,
                "detail": self.detail, "created_at": _iso(self.created_at)}


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, nullable=True, index=True)
    title = Column(String)
    description = Column(Text, nullable=True)
    #: active | paused | done
    status = Column(String, default="active", index=True)
    #: JSON list of {"id", "title", "done"}.
    milestones = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())

    def milestone_list(self) -> list:
        return _load(self.milestones, [])

    def to_dict(self) -> dict:
        milestones = self.milestone_list()
        return {"id": self.id, "agent_id": self.agent_id, "title": self.title,
                "description": self.description or "", "status": self.status,
                "milestones": milestones,
                "progress": (sum(1 for m in milestones if m.get("done")) / len(milestones))
                if milestones else 0.0,
                "created_at": _iso(self.created_at)}


class Watch(Base):
    __tablename__ = "watches"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, nullable=True, index=True)
    title = Column(String)
    #: A web address (a page on a connected site is read signed in).
    target = Column(Text)
    #: change | contains | absent | below | above
    condition = Column(String, default="change")
    value = Column(String, nullable=True)
    interval_minutes = Column(Integer, default=60)
    #: active | paused | stopped
    status = Column(String, default="active", index=True)
    next_check_at = Column(DateTime, nullable=True, index=True)
    last_checked_at = Column(DateTime, nullable=True)
    last_hash = Column(String, nullable=True)
    last_value = Column(Text, nullable=True)
    last_alert_hash = Column(String, nullable=True)
    last_alert_at = Column(DateTime, nullable=True)
    failures = Column(Integer, nullable=True)
    last_error = Column(Text, nullable=True)
    checks = Column(Integer, nullable=True)
    #: A job prompt to run when it fires (optional).
    follow_up = Column(Text, nullable=True)
    #: An existing job to run when it fires ("keep it updated" from a source).
    task_id = Column(Integer, nullable=True, index=True)
    #: A watched file's "modified time:size", so unchanged files aren't re-read.
    last_stamp = Column(String, nullable=True)
    created_at = Column(DateTime, default=func.now())

    def to_dict(self) -> dict:
        return {"id": self.id, "agent_id": self.agent_id, "title": self.title,
                "target": self.target, "condition": self.condition, "value": self.value,
                "interval_minutes": self.interval_minutes, "status": self.status,
                "next_check_at": _iso(self.next_check_at), "last_checked_at": _iso(self.last_checked_at),
                "last_value": (self.last_value or "")[:300], "last_alert_at": _iso(self.last_alert_at),
                "failures": self.failures or 0, "last_error": self.last_error,
                "checks": self.checks or 0, "follow_up": self.follow_up,
                "task_id": self.task_id, "kind": "file" if is_file_target(self.target) else "page",
                "created_at": _iso(self.created_at)}


def is_file_target(target: str) -> bool:
    """A local or synced file (C:\\…, \\\\server\\…, /…, file://…) rather than a web page."""
    import re

    text = (target or "").strip()
    return (text.lower().startswith("file:") or bool(re.match(r"^[a-zA-Z]:[\\/]", text))
            or text.startswith("\\\\") or text.startswith("/"))


class Idea(Base):
    __tablename__ = "ideas"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, nullable=True, index=True)
    title = Column(String)
    reason = Column(Text, nullable=True)
    #: JSON list of {"kind", "title", "excerpt", "url"}.
    evidence = Column(Text, nullable=True)
    #: What the job would be asked to do if accepted.
    prompt = Column(Text)
    #: new | accepted | dismissed
    status = Column(String, default="new", index=True)
    task_id = Column(Integer, nullable=True)
    dedupe_key = Column(String, unique=True, index=True)
    created_at = Column(DateTime, default=func.now(), index=True)

    def to_dict(self) -> dict:
        return {"id": self.id, "agent_id": self.agent_id, "title": self.title,
                "reason": self.reason or "", "evidence": _load(self.evidence, []),
                "prompt": self.prompt, "status": self.status, "task_id": self.task_id,
                "created_at": _iso(self.created_at)}


class FollowUp(Base):
    """Something to chase or deliver: the secretary's to-do list.

    ``kind`` is ``waiting_on`` (you asked someone; no reply yet), ``promised``
    (you said you'd do something), ``asked`` (someone asked you) or ``todo``
    (added by you, a meeting's action items, a reminder).
    """

    __tablename__ = "followups"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String, default="todo", index=True)
    who = Column(String, nullable=True)
    what = Column(Text)
    due_at = Column(DateTime, nullable=True, index=True)
    #: mail | teams | chat | meeting | manual
    source = Column(String, nullable=True)
    #: The words it came from, so the user can see why it's here.
    evidence = Column(Text, nullable=True)
    #: What it's about ("action:5", "message:12", "meeting:<id>"), for de-duplication.
    ref = Column(String, nullable=True, index=True)
    thread_id = Column(String, nullable=True, index=True)
    #: open | done | snoozed
    status = Column(String, default="open", index=True)
    snooze_until = Column(DateTime, nullable=True)
    chased_at = Column(DateTime, nullable=True)
    done_at = Column(DateTime, nullable=True)
    agent_id = Column(Integer, nullable=True, index=True)
    conversation_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=func.now())

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "who": self.who, "what": self.what,
                "due_at": _iso(self.due_at), "source": self.source, "evidence": self.evidence,
                "ref": self.ref, "thread_id": self.thread_id, "status": self.status,
                "snooze_until": _iso(self.snooze_until), "chased_at": _iso(self.chased_at),
                "done_at": _iso(self.done_at), "agent_id": self.agent_id,
                "conversation_id": self.conversation_id, "created_at": _iso(self.created_at)}


class Briefing(Base):
    """A briefing Cerebro put together: morning, end of day, weekly, or before a meeting."""

    __tablename__ = "briefings"

    id = Column(Integer, primary_key=True, index=True)
    #: morning | evening | weekly | meeting
    kind = Column(String, index=True)
    #: One per day / week / meeting ("morning:2026-11-09", "meeting:<event id>").
    key = Column(String, unique=True, index=True)
    title = Column(String)
    content = Column(Text)
    data = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now(), index=True)

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "key": self.key, "title": self.title,
                "content": self.content, "data": _load(self.data, {}),
                "created_at": _iso(self.created_at)}
