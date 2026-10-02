"""
What Cerebro is doing right now.

Every subsystem already reports *configuration* (is the inbox bridge set up,
is an AI provider connected), but nothing answered "is Cerebro busy, and with
what?" — which is exactly what the tray brain and the app header need to show.

This module is that answer: a tiny, thread-safe registry. Work in progress
wraps itself in :func:`activity`::

    with activity("thinking", "Answering your question"):
        reply = llm.chat(...)

and anyone can read :func:`snapshot` — the single most important thing going
on, plus the full list. Nothing is persisted: this is live state only, and it
resets with the process.
"""

import itertools
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

#: Most important first. The tray shows the first state present; ``idle`` is
#: what remains when nothing is registered.
STATES = (
    "error", "writing", "browsing", "thinking", "searching",
    "syncing", "listening", "awaiting_approval", "idle",
)

LABELS = {
    "error": "Something needs attention",
    "writing": "Making a change",
    "browsing": "Working in the browser",
    "thinking": "Thinking",
    "searching": "Searching sources",
    "syncing": "Syncing",
    "listening": "Listening",
    "awaiting_approval": "Waiting for your approval",
    "idle": "Idle",
}

#: How long a reported error stays visible after the work that raised it.
ERROR_TTL_SECONDS = 15

_lock = threading.Lock()
_changed = threading.Condition(_lock)
_ids = itertools.count(1)
_active: Dict[int, Dict[str, Any]] = {}
_last_error: Optional[Dict[str, Any]] = None
_version = 0
#: Things worth telling the user about right away (a new important email,
#: research finished). The tray turns new ones into Windows notifications.
_notices: List[Dict[str, Any]] = []
_notice_ids = itertools.count(1)
NOTICES_KEPT = 20


def _bump() -> None:
    global _version
    _version += 1
    _changed.notify_all()


def begin(state: str, detail: str = None) -> int:
    """Register work in progress and return a handle for :func:`end`."""
    if state not in STATES:
        raise ValueError(f"Unknown activity state: {state}")
    with _lock:
        handle = next(_ids)
        _active[handle] = {
            "state": state, "detail": detail or LABELS[state],
            "started": time.time(),
        }
        _bump()
        return handle


def end(handle: int, error: str = None) -> None:
    global _last_error
    with _lock:
        _active.pop(handle, None)
        if error:
            _last_error = {"state": "error", "detail": error[:200],
                           "started": time.time()}
        _bump()


def update(handle: int, detail: str) -> None:
    """Change the human-readable detail of running work."""
    with _lock:
        if handle in _active:
            _active[handle]["detail"] = detail
            _bump()


@contextmanager
def activity(state: str, detail: str = None):
    """Register work for the duration of a ``with`` block.

    An exception inside the block is recorded as a short-lived ``error``
    state and then re-raised unchanged.
    """
    handle = begin(state, detail)
    try:
        yield handle
    except Exception as exc:
        end(handle, error=f"{LABELS[state]} failed: {exc}")
        raise
    else:
        end(handle)


def report_error(detail: str) -> None:
    """Flag a failure that did not happen inside an :func:`activity` block."""
    global _last_error
    with _lock:
        _last_error = {"state": "error", "detail": detail[:200], "started": time.time()}
        _bump()


def version() -> int:
    with _lock:
        return _version


def wait_for_change(since: int, timeout: float) -> int:
    """Block until the registry changes after ``since`` (or ``timeout``)."""
    with _changed:
        if _version == since:
            _changed.wait(timeout)
        return _version


def snapshot(pending_approvals: int = 0) -> Dict[str, Any]:
    """The current state, ready to serialise.

    ``pending_approvals`` comes from the caller (it lives in the database), so
    a draft waiting for review shows even when nothing is running.
    """
    global _last_error
    now = time.time()
    with _lock:
        items: List[Dict[str, Any]] = [dict(item) for item in _active.values()]
        if _last_error and now - _last_error["started"] > ERROR_TTL_SECONDS:
            _last_error = None
        if _last_error:
            items.append(dict(_last_error))
        current_version = _version

    if pending_approvals and not any(i["state"] == "awaiting_approval" for i in items):
        items.append({
            "state": "awaiting_approval",
            "detail": f"{pending_approvals} draft(s) waiting for approval",
            "started": now,
        })

    items.sort(key=lambda item: (STATES.index(item["state"]), item["started"]))
    top = items[0] if items else {"state": "idle", "detail": LABELS["idle"]}
    return {
        "state": top["state"],
        "label": LABELS[top["state"]],
        "detail": top.get("detail") or LABELS[top["state"]],
        "active": [{**item, "elapsed_s": round(now - item["started"], 1)}
                   for item in items],
        "pending_approvals": pending_approvals,
        "version": current_version,
        "notices": recent_notices()[-5:],
    }


def notice(title: str, body: str = "", kind: str = "message",
           link: Optional[Dict[str, Any]] = None) -> int:
    """Tell the user something now. ``link`` says where Cerebro should open
    ({"tab": "ask", "conversation_id": 3}); returns the notice id."""
    with _lock:
        notice_id = next(_notice_ids)
        _notices.append({"id": notice_id, "title": title[:120], "body": (body or "")[:300],
                         "kind": kind, "link": link or {}, "at": time.time()})
        del _notices[:-NOTICES_KEPT]
        _bump()
    return notice_id


def recent_notices(after: int = 0) -> List[Dict[str, Any]]:
    with _lock:
        return [dict(item) for item in _notices if item["id"] > after]


def reset() -> None:
    """Forget everything (tests only)."""
    global _last_error
    with _lock:
        _active.clear()
        _notices.clear()
        _last_error = None
        _bump()
