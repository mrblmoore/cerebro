"""
The task engine — parsing instructions, scheduling them, and carrying them out.

Flow: a natural-language instruction ("keep the project log updated daily with a
line under my name") is parsed into a structured task; the scheduler wakes it
when due; an executor does the work — autonomously if the user allowed it, or as
a draft for review if not.

Parsing uses the LLM when one is available, because intent is genuinely
open-ended, and falls back to a keyword parser so reminders and simple recurring
tasks work with no AI at all.
"""

import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core import logger
from app.core.config import settings
from app.models.task import Task

SCHEDULES = ("once", "hourly", "daily", "weekdays", "weekly", "manual")

TIME_RE = re.compile(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)


# ---------------------------------------------------------- NL parsing
def _keyword_parse(instruction: str) -> Dict[str, Any]:
    """A no-AI fallback that handles the common shapes."""
    text = instruction.strip()
    lowered = text.lower()

    schedule = "once"
    if "every day" in lowered or "daily" in lowered or "each day" in lowered:
        schedule = "daily"
    elif "weekday" in lowered or "every workday" in lowered:
        schedule = "weekdays"
    elif "every week" in lowered or "weekly" in lowered:
        schedule = "weekly"
    elif "every hour" in lowered or "hourly" in lowered:
        schedule = "hourly"

    at_time = None
    match = TIME_RE.search(lowered)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = match.group(3)
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23:
            at_time = f"{hour:02d}:{minute:02d}"

    kind = "reminder"
    if any(w in lowered for w in ("update", "add to", "maintain", "keep", "log")):
        kind = "document_update"
    elif any(w in lowered for w in ("reply", "respond", "draft")):
        kind = "draft_reply"
    elif "summar" in lowered:
        kind = "summarise"

    attribution = None
    match = re.search(r"under (?:my name|my|the name of)\s*([A-Za-z .'-]+)?", lowered)
    if "under my name" in lowered or "under my" in lowered:
        attribution = "user"

    autonomous = any(p in lowered for p in
                     ("without asking", "automatically", "on your own", "just do it"))

    title = re.sub(
        r"^\s*(?:please\s+|can you\s+|could you\s+|remind me to\s+|remind me\s+|"
        r"i need you to\s+|make sure (?:to|you)\s+|keep\s+)",
        "", text, flags=re.IGNORECASE).strip()

    return {
        "title": (title or text)[:80], "kind": kind, "schedule": schedule,
        "at_time": at_time, "attribution": attribution, "autonomous": autonomous,
        "spec": {},
    }


def parse_instruction(instruction: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Turn a natural-language instruction into a structured task.

    The LLM sees the current context (open case, active document) so "keep *this*
    document updated" resolves to a real path. Its output is validated and merged
    onto the keyword parse, so a malformed LLM reply degrades rather than breaks.
    """
    base = _keyword_parse(instruction)

    from app.services.llm_service import LLMService

    llm = LLMService()
    if not llm.enabled:
        return base

    context = context or {}
    prompt = f"""Convert this instruction into a task. Return one JSON object, no prose.

Instruction: "{instruction}"

Current context:
- open case: {context.get('crm_case') or 'none'}
- active document: {context.get('active_document') or 'none'}

Fields:
  title: short label
  kind: reminder | document_update | draft_reply | summarise | custom
  schedule: once | hourly | daily | weekdays | weekly | manual
  at_time: "HH:MM" 24h, or null
  autonomous: true only if the user clearly said to act without asking
  attribution: "user" if they said "under my name", else null
  spec: object with any specifics — for document_update include
        {{"document": "<path or name>", "section": "<heading or 'mine'>",
          "content_hint": "<what to add>"}}

JSON:"""

    raw = llm._call_llm(prompt)
    parsed = _extract_json(raw)
    if not parsed:
        return base

    # Merge: trust the LLM for the rich fields, keep the keyword parse as backstop.
    merged = {**base, **{k: v for k, v in parsed.items() if v is not None}}
    if merged.get("schedule") not in SCHEDULES:
        merged["schedule"] = base["schedule"]
    if not isinstance(merged.get("spec"), dict):
        merged["spec"] = {}
    return merged


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


# ----------------------------------------------------------- scheduling
def compute_next_run(schedule: str, at_time: str = None,
                     after: datetime = None) -> Optional[datetime]:
    """When a task with this schedule should next fire."""
    now = after or datetime.now()

    if schedule in ("manual",):
        return None
    if schedule == "hourly":
        return now + timedelta(hours=1)

    hour, minute = 9, 0
    if at_time:
        try:
            hour, minute = (int(part) for part in at_time.split(":"))
        except (ValueError, TypeError):
            pass

    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)

    if schedule == "once":
        return target
    if schedule == "weekdays":
        while target.weekday() >= 5:  # Sat/Sun
            target += timedelta(days=1)
        return target
    if schedule == "weekly":
        # A weekly task fires seven days on, not tomorrow. `after` carries the
        # previous fire time on reschedules, so this steps a genuine week.
        if after is not None:
            return after.replace(hour=hour, minute=minute, second=0,
                                 microsecond=0) + timedelta(days=7)
        return target

    return target


# ------------------------------------------------------------- service
class TaskService:
    def __init__(self, db: Session):
        self.db = db

    def create_from_instruction(self, instruction: str,
                                context: Dict[str, Any] = None,
                                source: str = "user") -> Task:
        """Parse and store a task from natural language."""
        parsed = parse_instruction(instruction, context)
        spec = parsed.get("spec") or {}

        # If the instruction is about "this document", pin the active one now.
        if parsed.get("kind") == "document_update" and not spec.get("document"):
            if context and context.get("active_document"):
                spec["document"] = context["active_document"]

        next_run = compute_next_run(parsed["schedule"], parsed.get("at_time"))

        task = Task(
            title=parsed.get("title") or instruction[:80],
            instruction=instruction,
            kind=parsed.get("kind", "reminder"),
            spec=json.dumps(spec),
            schedule=parsed.get("schedule", "once"),
            at_time=parsed.get("at_time"),
            autonomous=bool(parsed.get("autonomous")),
            attribution=parsed.get("attribution"),
            case_id=(context or {}).get("crm_case"),
            next_run=next_run,
            status="active",
            source=source,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        logger.info("tasks", "Created task",
                    {"title": task.title, "kind": task.kind, "schedule": task.schedule})
        return task

    def due(self, now: datetime = None) -> List[Task]:
        now = now or datetime.now()
        return (self.db.query(Task)
                .filter(Task.status == "active")
                .filter(Task.next_run.isnot(None))
                .filter(Task.next_run <= now)
                .all())

    def run(self, task: Task) -> Dict[str, Any]:
        """Execute one task and reschedule or close it."""
        from app.services.task_executors import execute

        try:
            result = execute(self.db, task)
        except Exception as exc:
            logger.error("tasks", "Task failed", {"id": task.id, "error": str(exc)})
            result = {"ok": False, "detail": str(exc), "status": "failed"}

        task.last_run = datetime.now()
        task.run_count = (task.run_count or 0) + 1
        task.last_result = (result.get("detail") or result.get("summary") or "")[:2000]

        outcome = result.get("status")
        # A one-off task is finished once it has fired — the only thing that
        # keeps it around is the executor explicitly parking it for the user.
        if task.schedule == "once":
            task.status = outcome if outcome in ("needs_review", "failed") else "done"
        elif outcome:
            task.status = outcome

        if task.status == "active":
            task.next_run = compute_next_run(task.schedule, task.at_time,
                                             after=task.last_run)
        else:
            task.next_run = None

        self.db.commit()
        return result

    def list_tasks(self, status: str = None, limit: int = 100) -> List[Task]:
        query = self.db.query(Task)
        if status:
            query = query.filter(Task.status == status)
        return query.order_by(Task.next_run.asc().nullslast(),
                              Task.created_at.desc()).limit(limit).all()

    def status(self) -> Dict[str, Any]:
        if not settings.TASKS_ENABLED:
            return {"ok": True, "enabled": False, "detail": "Tasks disabled"}
        active = self.db.query(Task).filter(Task.status == "active").count()
        review = self.db.query(Task).filter(Task.status == "needs_review").count()
        detail = f"{active} active task(s)"
        if review:
            detail += f", {review} awaiting review"
        return {"ok": True, "enabled": True, "active": active,
                "needs_review": review, "detail": detail}


def run_due_tasks(db: Session) -> Dict[str, Any]:
    """Called by the scheduler each tick."""
    if not settings.TASKS_ENABLED:
        return {"ran": 0}
    service = TaskService(db)
    due = service.due()
    for task in due:
        service.run(task)
    return {"ran": len(due)}
