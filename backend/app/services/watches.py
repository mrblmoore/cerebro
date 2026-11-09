"""
Watches — borrowed from OpenMuse's tracking monitors.

A watch re-checks a page on a schedule and tells the user when:

* ``change``   — its text changed;
* ``contains`` — some text appeared;
* ``absent``   — some text is gone;
* ``below`` / ``above`` — a number on it crossed a value (a price, a queue
  length, a count of open cases).

A watch can also follow a **file** — a local one, or a OneDrive/SharePoint
file synced to this PC (``C:\\…``, ``\\\\server\\share\\…`` or ``file://``). Its
modified time and size are compared first, so an unchanged file costs
nothing; when they move, its text is read with the document readers (Word,
Excel, PowerPoint, PDF, text) and the same conditions apply.

A page on a system Cerebro is connected to (Dynamics, a website added under
Add-ons…) is read through that connector in the hidden browser, signed in;
anything else is fetched like ``fetch_url``. The text is normalised first
(times, dates and long numbers that tick over on every load are blanked) so
a clock on the page doesn't count as a change.

Each alert is sent once (by content hash). It raises a notice, posts in the
owning agent's chat, and can start a follow-up job. A watch that keeps
failing backs off (interval × 2ⁿ, up to a day) and pauses after five
failures in a row.
"""

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from app.core import logger

CONDITIONS = ("change", "contains", "absent", "below", "above")
MIN_INTERVAL = 5
MIN_FILE_INTERVAL = 1
MAX_FAILURES = 5

_VOLATILE = [
    re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\s*(am|pm)?\b", re.I),
    re.compile(r"\b\d{4}-\d{2}-\d{2}([t ]\d{2}:\d{2}(:\d{2})?(\.\d+)?z?)?\b", re.I),
    re.compile(r"\b(\d+\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\s+ago|just now)\b", re.I),
    re.compile(r"\b[0-9a-f]{24,}\b", re.I),
]


def normalise(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    for pattern in _VOLATILE:
        text = pattern.sub("·", text)
    return text


def number_in(text: str, near: str = None) -> Optional[float]:
    """The first number in the text (after ``near``, when given)."""
    if near:
        index = (text or "").lower().find(near.lower())
        if index >= 0:
            text = text[index + len(near):]
    match = re.search(r"-?\d[\d,]*(\.\d+)?", text or "")
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def describe(watch) -> str:
    return {"change": "alerts when it changes",
            "contains": f"alerts when “{watch.value}” appears",
            "absent": f"alerts when “{watch.value}” is gone",
            "below": f"alerts when the number goes below {watch.value}",
            "above": f"alerts when the number goes above {watch.value}"}.get(watch.condition, "")


def file_path(target: str):
    """The local path a file watch reads."""
    from pathlib import Path
    from urllib.parse import unquote, urlparse

    text = (target or "").strip()
    if text.lower().startswith("file:"):
        parsed = urlparse(text)
        path = unquote(parsed.path)
        if parsed.netloc:                        # file://server/share/x → \\server\share\x
            path = f"//{parsed.netloc}{path}"
        elif re.match(r"^/[a-zA-Z]:", path):     # file:///C:/x → C:/x
            path = path[1:]
        return Path(path)
    return Path(text)


def create(db, url: str, title: str = "", condition: str = "change", value: str = "",
           interval_minutes: Any = 60, follow_up: str = "", agent_id: int = None,
           task_id: int = None):
    from app.models.work import Watch, is_file_target

    url = (url or "").strip().strip('"')
    is_file = is_file_target(url)
    if is_file:
        path = file_path(url)
        if not path.exists():
            raise ValueError(f"There's no file at {path}.")
        if path.is_dir():
            raise ValueError("Watch a file, not a folder.")
    elif not url.startswith(("http://", "https://")):
        raise ValueError("Give the page's full address (https://…) or a file's path.")
    condition = (condition or "change").strip().lower()
    if condition not in CONDITIONS:
        raise ValueError(f"Condition must be one of: {', '.join(CONDITIONS)}.")
    if condition != "change" and not (value or "").strip():
        raise ValueError(f"Say what to look for with “{condition}”.")
    if condition in ("below", "above"):
        try:
            float(str(value).replace(",", ""))
        except ValueError:
            raise ValueError("Give a number for below/above.") from None
    try:
        minutes = max(MIN_FILE_INTERVAL if is_file else MIN_INTERVAL,
                      int(float(interval_minutes or 60)))
    except (TypeError, ValueError):
        minutes = 60
    watch = Watch(agent_id=agent_id, title=(title or url)[:120], target=url, condition=condition,
                  value=(value or "").strip() or None, interval_minutes=minutes, status="active",
                  next_check_at=datetime.now(), follow_up=(follow_up or "").strip() or None,
                  checks=0, failures=0, task_id=task_id)
    db.add(watch)
    db.commit()
    db.refresh(watch)
    return watch


# --------------------------------------------------------------- reading
def stamp(target: str) -> Optional[str]:
    """A file's "modified time:size" (``None`` for pages)."""
    from app.models.work import is_file_target

    if not is_file_target(target):
        return None
    info = file_path(target).stat()
    return f"{info.st_mtime_ns}:{info.st_size}"


def read(url: str) -> str:
    """The page's (or file's) readable text, signed in when it's on a connected system."""
    from app.models.work import is_file_target
    from app.services.agent import web_tools

    if is_file_target(url):
        from app.services import document_readers

        path = file_path(url)
        if document_readers.is_supported(path):
            return document_readers.read(path)["text"]
        return path.read_text(encoding="utf-8", errors="replace")

    connector = None
    try:
        connector = web_tools.site_for(url)
    except Exception:  # noqa: BLE001 - the hidden browser isn't available
        connector = None
    if connector is not None:
        def work(page):
            page.goto(url, wait_until="domcontentloaded")
            connector.settle(page)
            if connector.looks_like_login(page.url):
                from app.services.browser.connector import SignInRequired

                raise SignInRequired(connector)
            return connector.readable_text(page)
        return connector.run(work, f"Checking {connector.label}", quiet=True) or ""
    import requests

    from app.services.browser.messaging import strip_html

    response = requests.get(url, timeout=30, headers={"User-Agent": "Cerebro watch"})
    response.raise_for_status()
    kind = response.headers.get("Content-Type", "")
    return strip_html(response.text) if "html" in kind or "<" in response.text[:200] else response.text


# -------------------------------------------------------------- checking
def evaluate(watch, text: str) -> Tuple[bool, str, str]:
    """``(fires, digest, what to say)`` for a fresh reading."""
    clean = normalise(text)
    digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:32]
    condition, value = watch.condition, watch.value or ""
    if condition == "change":
        if watch.last_hash is None:
            return False, digest, "First reading saved."
        return digest != watch.last_hash, digest, "The page changed."
    if condition in ("contains", "absent"):
        present = value.lower() in clean.lower()
        fires = present if condition == "contains" else not present
        state = f"{condition}:{present}"
        return fires, hashlib.sha256(state.encode()).hexdigest()[:32], (
            f"“{value}” is now on the page." if present else f"“{value}” is no longer on the page.")
    number = number_in(clean)
    if number is None:
        return False, digest, "No number found on the page."
    limit = float(value.replace(",", ""))
    fires = number < limit if condition == "below" else number > limit
    state = f"{condition}:{fires}"
    return fires, hashlib.sha256(state.encode()).hexdigest()[:32], (
        f"It's {number:g} now ({condition} {limit:g}).")


def check(db, watch, text: str = None) -> Dict[str, Any]:
    """Check one watch now. ``text`` replaces reading the page (tests)."""
    now = datetime.now()
    watch.last_checked_at = now
    watch.checks = (watch.checks or 0) + 1
    try:
        current = stamp(watch.target) if text is None else None
        if current and current == watch.last_stamp and watch.last_value is not None:
            # A file that hasn't been touched: nothing to read.
            watch.failures, watch.last_error = 0, None
            watch.next_check_at = now + timedelta(minutes=watch.interval_minutes)
            db.commit()
            return {"ok": True, "fired": False, "detail": "Unchanged.", "skipped": True}
        reading = read(watch.target) if text is None else text
    except Exception as exc:  # noqa: BLE001 - recorded on the watch, retried later
        watch.failures = (watch.failures or 0) + 1
        watch.last_error = str(exc)[:500]
        if watch.failures >= MAX_FAILURES:
            watch.status = "paused"
            _notify(db, watch, f"Paused watching {watch.title}",
                    f"It failed {watch.failures} times in a row: {watch.last_error[:160]}")
        backoff = min(watch.interval_minutes * (2 ** watch.failures), 24 * 60)
        watch.next_check_at = now + timedelta(minutes=backoff)
        db.commit()
        return {"ok": False, "error": watch.last_error, "status": watch.status}

    fires, digest, what = evaluate(watch, reading)
    previous_alert = watch.last_alert_hash
    watch.failures = 0
    watch.last_error = None
    if watch.condition == "change":
        watch.last_hash = digest
    watch.last_value = normalise(reading)[:2000]
    if text is None:
        watch.last_stamp = current
    watch.next_check_at = now + timedelta(minutes=watch.interval_minutes)
    alerted = False
    if fires and digest != previous_alert:
        watch.last_alert_hash = digest
        watch.last_alert_at = now
        alerted = True
    elif not fires and watch.condition != "change":
        watch.last_alert_hash = None          # re-arm: alert again next time it happens
    db.commit()
    if alerted:
        _alert(db, watch, what)
    return {"ok": True, "fired": alerted, "detail": what}


def _notify(db, watch, title: str, body: str) -> None:
    from app.core import activity_state

    chat = _chat(db, watch)
    activity_state.notice(title, body, kind="watch",
                          link={"tab": "ask", "conversation_id": chat.id if chat else None})


def _chat(db, watch):
    """The owning agent's "Watches" chat (made on first alert)."""
    from app.models.conversation import Conversation
    from app.services import agents, conversations

    agent = agents.get(db, watch.agent_id)
    chat = (db.query(Conversation).filter(Conversation.title == "Watches",
                                          Conversation.agent_id == agent.id,
                                          Conversation.archived.is_(False)).first())
    if chat is None:
        chat = conversations.create(db, "Watches", "Alerts from pages this agent watches.",
                                    agent_id=agent.id)
    return chat


def _alert(db, watch, what: str) -> None:
    import json

    from app.models.task import Task
    from app.services import jobs
    from app.services.chat_service import ChatService

    chat = _chat(db, watch)
    text = f"**{watch.title}** — {what}\n\n{watch.target}"
    ChatService(db, chat.id)._store(
        "assistant", text, kind="watch_alert",
        meta={"watch": {"id": watch.id, "title": watch.title, "url": watch.target}})
    _notify(db, watch, f"Watch: {watch.title}", what)
    try:
        from app.services import ideas

        ideas.from_watch(db, watch, what)
    except Exception:  # noqa: BLE001 - an idea is a nicety
        pass
    if watch.task_id:
        job = db.get(Task, watch.task_id)
        if job is not None and job.status not in ("cancelled", "paused"):
            state = job.state_dict()
            state["trigger"] = f"The source changed ({watch.title}): {what}"
            job.state = json.dumps(state)
            jobs.make_runnable(db, job)
            jobs.event(db, job.id, "status", f"Started because “{watch.title}” changed", what)
    elif watch.follow_up:
        task = Task(title=f"{watch.title}: follow-up"[:80], instruction=watch.follow_up,
                    kind="agent", spec=json.dumps({"prompt": f"{watch.follow_up}\n\n(The watch "
                                                            f"“{watch.title}” fired: {what} "
                                                            f"Page: {watch.target})"}),
                    schedule="once", status="queued", source="watch", agent_id=watch.agent_id,
                    conversation_id=chat.id, next_run=datetime.now())
        db.add(task)
        db.commit()
        jobs.event(db, task.id, "status", f"Started by the watch “{watch.title}”", what)
    logger.info("watches", "Watch fired", {"id": watch.id, "title": watch.title})


def due(db, limit: int = 10):
    from app.models.work import Watch

    return (db.query(Watch).filter(Watch.status == "active",
                                   (Watch.next_check_at.is_(None)) |
                                   (Watch.next_check_at <= datetime.now()))
            .order_by(Watch.next_check_at.asc().nullsfirst()).limit(limit).all())


def run_due(db) -> int:
    count = 0
    for watch in due(db):
        try:
            check(db, watch)
        except Exception as exc:  # noqa: BLE001 - one watch never stops the rest
            db.rollback()
            logger.warn("watches", "Check failed", {"id": watch.id, "error": str(exc)[:200]})
        count += 1
    return count


def control(db, watch, action: str) -> Dict[str, Any]:
    if action == "pause":
        watch.status = "paused"
    elif action == "resume":
        watch.status, watch.failures = "active", 0
        watch.next_check_at = datetime.now()
    elif action == "stop":
        watch.status = "stopped"
    elif action == "check":
        db.commit()
        return check(db, watch)
    else:
        return {"ok": False, "detail": f"Unknown action {action}"}
    db.commit()
    return {"ok": True, "watch": watch.to_dict()}
