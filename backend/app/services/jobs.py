"""
Durable jobs — borrowed from OpenMuse's task worker.

A job is a :class:`~app.models.task.Task` an agent works on in the
background. On top of what a task always had (a schedule, a chat to post
in), a job now has:

* a **plan** — steps the agent sets out (``show_plan`` / ``update_plan``),
  each pending, running, done or waiting;
* a **run log** of :class:`~app.models.work.TaskEvent` — what it looked up,
  what it prepared, what it was waiting for: the receipts;
* **waiting for you** — ``ask_user`` parks it in ``waiting_input`` until you
  answer; changes it prepared park it in ``waiting_approval`` until you
  approve or discard them; then it carries on by itself;
* **pause, resume, cancel and retry**, with pause and cancel taking effect
  between steps;
* a **lease**: a worker claims a job before running it, so it never runs
  twice at once, and a job left running when Cerebro closed is picked up
  again — unless it had already made a change automatically, in which case
  it is not retried (it may or may not have finished): it fails with "check,
  then Retry".

Jobs run on a small pool of threads (``JOBS_PARALLEL``) fed by the scheduler,
so one slow job never holds up the others.
"""

import json
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.core import logger
from app.core.config import settings

LEASE_SECONDS = 15 * 60
RUNNABLE = ("active", "queued")
WAITING = ("waiting_input", "waiting_approval")
STOPPED = ("paused", "cancelled", "done", "failed")
#: Failed runs in a row before a recurring job is paused.
MAX_FAILURES = 3

_running: Dict[int, threading.Thread] = {}
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now()


# ------------------------------------------------------------ run log
def event(db, task_id: int, kind: str, title: str, detail: str = None) -> None:
    from app.models.work import TaskEvent

    db.add(TaskEvent(task_id=task_id, kind=kind, title=(title or "")[:200],
                     detail=(detail or "")[:4000] or None))
    db.commit()


def events(db, task_id: int, limit: int = 200) -> List[dict]:
    from app.models.work import TaskEvent

    rows = (db.query(TaskEvent).filter(TaskEvent.task_id == task_id)
            .order_by(TaskEvent.id.desc()).limit(limit).all())
    return [row.to_dict() for row in reversed(rows)]


def set_state(task, **values) -> None:
    state = task.state_dict()
    for key, value in values.items():
        if value is None:
            state.pop(key, None)
        else:
            state[key] = value
    task.state = json.dumps(state)


def set_plan(task, steps: List[dict]) -> None:
    clean = []
    for step in steps or []:
        if isinstance(step, str):
            step = {"title": step}
        title = str(step.get("title") or "").strip()
        if title:
            status = step.get("status") if step.get("status") in (
                "pending", "running", "done", "failed", "waiting") else "pending"
            clean.append({"title": title[:200], "status": status,
                          "detail": (step.get("detail") or "")[:300] or None})
    task.plan = json.dumps(clean[:20])


# -------------------------------------------------------------- leases
def claim(db, task_id: int) -> bool:
    """Take a job for this worker. False if someone else has it, or it isn't runnable."""
    from app.models.task import Task

    now = _now()
    changed = (db.query(Task)
               .filter(Task.id == task_id,
                       Task.status.in_(RUNNABLE),
                       (Task.lease_until.is_(None)) | (Task.lease_until < now))
               .update({Task.lease_until: now + timedelta(seconds=LEASE_SECONDS)},
                       synchronize_session=False))
    db.commit()
    return changed == 1


def release(db, task) -> None:
    task.lease_until = None
    db.commit()


def heartbeat(task_id: int) -> None:
    from app.core.database import SessionLocal
    from app.models.task import Task

    with SessionLocal() as db:
        db.query(Task).filter(Task.id == task_id).update(
            {Task.lease_until: _now() + timedelta(seconds=LEASE_SECONDS)},
            synchronize_session=False)
        db.commit()


def recover(db) -> int:
    """Jobs left running when Cerebro closed (their lease ran out)."""
    from app.models.task import Task

    stale = (db.query(Task).filter(Task.status == "running", Task.lease_until.isnot(None),
                                   Task.lease_until < _now()).all())
    for task in stale:
        task.lease_until = None
        if task.state_dict().get("auto_applied"):
            task.status = "failed"
            task.error = ("Interrupted after making a change automatically — it may or may not "
                          "have finished. Check, then Retry.")
            event(db, task.id, "error", "Interrupted after a change", task.error)
        else:
            task.status = "queued" if task.schedule == "once" else "active"
            task.next_run = _now()
            event(db, task.id, "status", "Picked up again after Cerebro restarted")
    db.commit()
    return len(stale)


# --------------------------------------------------------------- running
def due(db, limit: int = 20) -> List[int]:
    from app.models.task import Task

    now = _now()
    rows = (db.query(Task.id).filter(
        ((Task.status == "queued")) |
        ((Task.status == "active") & Task.next_run.isnot(None) & (Task.next_run <= now)))
        .filter((Task.lease_until.is_(None)) | (Task.lease_until < now))
        .order_by(Task.next_run.asc().nullsfirst()).limit(limit).all())
    return [row[0] for row in rows]


def run(task_id: int) -> Optional[Dict[str, Any]]:
    """Claim and run one job in this thread. ``None`` if it couldn't be claimed."""
    from app.core.database import SessionLocal
    from app.models.task import Task
    from app.services.task_service import TaskService

    db = SessionLocal()
    try:
        if not claim(db, task_id):
            return None
        task = db.get(Task, task_id)
        resting = "active" if task.schedule != "once" else "queued"
        task.status = "running"
        set_state(task, resting=resting)
        db.commit()
        event(db, task.id, "status", "Started" if not task.run_count else f"Run {task.run_count + 1}")
        try:
            return TaskService(db).run(task)
        finally:
            db.refresh(task)
            release(db, task)
    except Exception as exc:  # noqa: BLE001 - a job never takes the worker down
        logger.error("jobs", "Job crashed", {"id": task_id, "error": str(exc)[:300]})
        db.rollback()
        return {"ok": False, "detail": str(exc)}
    finally:
        db.close()


def make_runnable(db, task) -> None:
    """Due now: the "Run now" button, or an answer that lets it carry on."""
    if task.status in ("active", "queued", "failed", "needs_review", "done"):
        task.status = "queued" if task.schedule == "once" else "active"
        task.next_run = _now()
        db.commit()


def start_now(task_id: int) -> bool:
    """Run a job straight away on its own thread."""
    from app.core.database import SessionLocal
    from app.models.task import Task

    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is None:
            return False
        make_runnable(db, task)
    return _spawn(task_id)


def _spawn(task_id: int) -> bool:
    with _lock:
        alive = {k: t for k, t in _running.items() if t.is_alive()}
        _running.clear()
        _running.update(alive)
        if task_id in _running:
            return False
        thread = threading.Thread(target=_run_and_forget, args=(task_id,), daemon=True,
                                  name=f"cerebro-job-{task_id}")
        _running[task_id] = thread
        thread.start()
        return True


def _run_and_forget(task_id: int) -> None:
    try:
        run(task_id)
    finally:
        with _lock:
            _running.pop(task_id, None)


def busy() -> int:
    with _lock:
        return sum(1 for t in _running.values() if t.is_alive())


def dispatch(db) -> int:
    """The scheduler's tick: recover, then start due jobs up to JOBS_PARALLEL."""
    if not settings.TASKS_ENABLED:
        return 0
    recover(db)
    room = max(1, int(settings.JOBS_PARALLEL or 2)) - busy()
    started = 0
    for task_id in due(db):
        if room <= 0:
            break
        if _spawn(task_id):
            room -= 1
            started += 1
    return started


def wait_idle(timeout: float = 30) -> None:
    """Wait for running jobs to finish (tests, shutdown)."""
    import time

    deadline = time.time() + timeout
    while busy() and time.time() < deadline:
        time.sleep(0.05)


# ------------------------------------------------------------- controls
def control(db, task, action: str) -> Dict[str, Any]:
    """pause | resume | cancel | retry."""
    status = task.status
    if action == "pause":
        if status in STOPPED:
            return {"ok": False, "detail": f"It's already {status}."}
        set_state(task, paused_from=status)
        task.status = "paused"
    elif action == "resume":
        if status != "paused":
            return {"ok": False, "detail": "It isn't paused."}
        back = task.state_dict().get("paused_from") or ("queued" if task.schedule == "once" else "active")
        task.status = "queued" if back == "running" else back
        if task.status == "active" and not task.next_run:
            from app.services.task_service import compute_next_run

            task.next_run = compute_next_run(task.schedule, task.at_time, after=_now())
        task.attempts = 0
        set_state(task, paused_from=None)
    elif action == "cancel":
        if status in ("done", "cancelled"):
            return {"ok": False, "detail": f"It's already {status}."}
        task.status = "cancelled"
        task.next_run = None
    elif action == "retry":
        if status not in ("failed", "cancelled", "needs_review", "done"):
            return {"ok": False, "detail": "Only a finished or failed job can be retried."}
        task.status = "queued" if task.schedule == "once" else "active"
        task.next_run = _now()
        task.error = None
        set_state(task, auto_applied=None)
    else:
        return {"ok": False, "detail": f"Unknown action {action}"}
    db.commit()
    event(db, task.id, "status", {"pause": "Paused", "resume": "Resumed", "cancel": "Cancelled",
                                  "retry": "Retrying"}[action])
    return {"ok": True, "task": task.to_dict()}


def answer(db, task, text: str) -> Dict[str, Any]:
    if task.status != "waiting_input":
        return {"ok": False, "detail": "It isn't waiting for an answer."}
    task.answer = (text or "").strip()[:4000]
    task.status = "queued" if task.schedule == "once" else "active"
    task.next_run = _now()
    db.commit()
    event(db, task.id, "input", "You answered", task.answer)
    return {"ok": True, "task": task.to_dict()}


def waiting_in_chat(db, conversation_id: int):
    """A job in this chat waiting for the user's answer, if any."""
    from app.models.task import Task

    if not conversation_id:
        return None
    return (db.query(Task).filter(Task.conversation_id == conversation_id,
                                  Task.status == "waiting_input")
            .order_by(Task.updated_at.desc()).first())


def decided(db, kind: str, action_id: int, outcome: str) -> None:
    """An approval a job was waiting on was decided: carry on when none are left."""
    from app.models.task import Task

    key = f"{kind}:{action_id}"
    for task in db.query(Task).filter(Task.status == "waiting_approval").all():
        waiting = task.state_dict().get("waiting") or []
        if key not in waiting:
            continue
        rest = [w for w in waiting if w != key]
        decisions = task.state_dict().get("decisions") or []
        decisions.append(f"{key} {outcome}")
        set_state(task, waiting=rest or None, decisions=decisions[-20:])
        event(db, task.id, "approval", f"Change {outcome}", key)
        if not rest:
            task.status = "queued" if task.schedule == "once" else "active"
            task.next_run = _now()
        db.commit()


def listing(db, agent_id: int = None, project_id: int = None, status: str = None,
            limit: int = 100) -> List[dict]:
    from app.models.task import Task

    query = db.query(Task)
    if agent_id is not None:
        from app.services import agents

        default = agents.default_agent(db)
        if int(agent_id) == default.id:
            query = query.filter((Task.agent_id == default.id) | (Task.agent_id.is_(None)))
        else:
            query = query.filter(Task.agent_id == int(agent_id))
    if project_id is not None:
        query = query.filter(Task.project_id == int(project_id))
    if status == "open":
        query = query.filter(Task.status.notin_(("done", "cancelled")))
    elif status:
        query = query.filter(Task.status == status)
    rows = query.order_by(Task.updated_at.desc(), Task.id.desc()).limit(limit).all()
    return [row.to_dict() for row in rows]
