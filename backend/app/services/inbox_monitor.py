"""
Watching Outlook and Teams — and helping with what arrives.

Every ``INBOX_MONITOR_SECONDS`` the Outlook and Teams tabs in the hidden
browser are checked (quietly: a routine check doesn't wake the tray brain or
the desktop buddy). New messages are stored like any other — so briefings,
nudges, Ask and Activity all see them — and then:

* **Important** ones (sent directly to you, an @mention, urgent, or naming a
  case) pop up a Windows notification, per ``INBOX_NOTIFY``.
* Cerebro **looks into** them on its own, per ``INBOX_ASSIST``: the Ask agent
  reads the message, checks the case in Dynamics, searches RightAnswers, the
  knowledge base and SharePoint, and posts what it found — with a draft reply
  waiting for approval — in a pinned chat called **Inbox**. At most
  ``INBOX_ASSIST_PER_HOUR`` of these run an hour, and a message is never
  researched twice.

The first check after Cerebro starts only catches up: messages already there
are stored, but nobody is notified about yesterday's mail.
"""

import queue
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

from app.core import logger
from app.core.config import settings

INBOX_TITLE = "Inbox"
INBOX_INSTRUCTIONS = ("Messages from Outlook and Teams that Cerebro looked into. Keep findings "
                      "short; lead with what the sender needs and what to do next.")
SOURCES = ("outlook", "teams")

ASSIST_PROMPT = """New {kind} from {sender}{case}.
{subject}
{body}

Look into this for me: if it mentions a case, check it in Dynamics; search RightAnswers, the knowledge base and SharePoint for anything that answers it. Then tell me in a few lines what they need and what I should do. If a reply makes sense, write it and prepare it with reply_to_message (message #{number}) — it waits for my approval unless I let replies send on their own."""

_stop = threading.Event()
_thread: Optional[threading.Thread] = None
_assist_thread: Optional[threading.Thread] = None
_assist_queue: "queue.Queue[int]" = queue.Queue()
_assist_times: deque = deque()
#: Sources whose first (catch-up) check has happened.
_primed: set = set()
_last_problem: Dict[str, str] = {}


# ---------------------------------------------------------------- checks
def check_once(db) -> Dict[str, Any]:
    """One pass over Outlook and Teams. Returns what was found, per source."""
    from app.services import browser

    report: Dict[str, Any] = {}
    if not settings.INBOX_MONITOR_ENABLED or not settings.BROWSER_AUTOMATION_ENABLED:
        return report
    for name in SOURCES:
        connector = browser.get(name)
        if not connector.enabled:
            continue
        try:
            messages = connector.collect(quiet=True)
        except Exception as exc:  # noqa: BLE001 - one app failing never stops the other
            problem = str(exc)[:200]
            if _last_problem.get(name) != problem:
                logger.warn("inbox", "Couldn't check for new messages",
                            {"source": name, "error": problem})
                _last_problem[name] = problem
            report[name] = {"error": problem}
            continue
        _last_problem.pop(name, None)
        report[name] = handle(db, name, messages)
    return report


def handle(db, source: str, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Store new messages; notify about and research the important ones."""
    from app.services import message_filter
    from app.models.enterprise import EnterpriseMessage
    from app.services.enterprise_service import EnterpriseService

    service = EnterpriseService(db)
    catching_up = source not in _primed
    new: List[EnterpriseMessage] = []
    filtered = 0
    for payload in messages:
        try:
            keep, _why = message_filter.allow(source, payload)
        except Exception:  # noqa: BLE001 - a filter bug must never hide mail
            keep = True
        if not keep:
            filtered += 1
            continue
        try:
            stored = service.ingest_payload({**payload, "source": source})
        except Exception as exc:  # noqa: BLE001 - one odd message never stops the rest
            logger.warn("inbox", "Couldn't store a message", {"source": source, "error": str(exc)[:200]})
            continue
        if stored["status"] == "ingested":
            new.append(db.query(EnterpriseMessage).get(stored["id"]))
    _primed.add(source)

    important = [m for m in new if is_important(m)]
    if not catching_up:
        for message in new:
            if settings.INBOX_NOTIFY == "all" or (
                    settings.INBOX_NOTIFY == "important" and message in important):
                notify(db, message)
            if settings.INBOX_ASSIST == "all" or (
                    settings.INBOX_ASSIST == "important" and message in important):
                _assist_queue.put(message.id)
        _ensure_assistant()
    if new:
        logger.info("inbox", "New messages", {"source": source, "new": len(new),
                                              "important": len(important),
                                              "catching_up": catching_up})
    return {"new": len(new), "important": len(important), "catching_up": catching_up,
            "filtered": filtered}


def is_important(message) -> bool:
    return bool(message.direct or message.mentioned or message.urgency == "high"
                or message.case_id)


def notify(db, message, title: str = None, body: str = None) -> int:
    from app.core import activity_state

    sender = message.sender_name or message.sender or "someone"
    what = "Email" if message.source == "outlook" else (
        "@mention" if message.mentioned else "Teams message")
    title = title or f"{what} from {sender}"
    body = body or (message.subject or "") + (" — " if message.subject else "") + (message.preview or "")
    link = {"tab": "ask"}
    chat = inbox_chat(db, create=False)
    if chat is not None:
        link["conversation_id"] = chat.id
    return activity_state.notice(title, body.strip(), kind=message.source, link=link)


# ------------------------------------------------------------- research
def inbox_chat(db, create: bool = True):
    """The pinned "Inbox" chat research is posted to."""
    from app.models.conversation import Conversation
    from app.services import conversations

    chat = (db.query(Conversation).filter(Conversation.title == INBOX_TITLE,
                                          Conversation.archived.is_(False))
            .order_by(Conversation.id.desc()).first())
    if chat is None and create:
        chat = conversations.create(db, INBOX_TITLE, INBOX_INSTRUCTIONS)
        conversations.update(db, chat, pinned=True)
    return chat


def _within_hourly_cap() -> bool:
    now = time.time()
    while _assist_times and now - _assist_times[0] > 3600:
        _assist_times.popleft()
    return len(_assist_times) < max(1, int(settings.INBOX_ASSIST_PER_HOUR or 12))


def assist(db, message_id: int) -> Optional[Dict[str, Any]]:
    """Research one message and post the findings in the Inbox chat."""
    from app.models.enterprise import EnterpriseMessage
    from app.services.agent import loop
    from app.services.chat_service import ChatService
    from app.services.llm_service import LLMService

    message = db.query(EnterpriseMessage).get(message_id)
    if message is None or message.assisted or not LLMService().enabled:
        return None
    if not _within_hourly_cap():
        logger.info("inbox", "Hourly research limit reached; skipping", {"message": message_id})
        return None
    _assist_times.append(time.time())
    message.assisted = True
    db.commit()

    chat = ChatService(db, inbox_chat(db).id)
    sender = message.sender_name or message.sender or "someone"
    prompt = ASSIST_PROMPT.format(
        kind="email" if message.source == "outlook" else "Teams message", sender=sender,
        case=f" about case {message.case_id}" if message.case_id else "",
        subject=f"Subject: {message.subject}" if message.subject else "",
        body=(message.body or message.preview or "")[:4000], number=message.id)
    context = {"conversation_id": chat.conversation_id, "task_run": "inbox"}
    if message.case_id:
        context["crm_case"] = message.case_id
    result = loop.run(db, prompt, context, history=loop._history(db, chat.conversation_id),
                      instructions=chat.conversation.instructions)
    meta = {key: result[key] for key in ("cards", "sources", "tools_used")
            if result.get(key) not in (None, [], {})}
    meta["incoming"] = {"id": message.id, "source": message.source, "sender": sender,
                        "subject": message.subject, "preview": message.preview,
                        "case_id": message.case_id, "chat": message.chat_or_channel}
    stored = chat._store("assistant", result["reply"], kind="inbox_assist", meta=meta,
                         case_id=message.case_id)
    if settings.INBOX_NOTIFY == "suggestions":
        notify(db, message, title=f"Cerebro looked into {sender}'s message",
               body=result["reply"].split("\n")[0][:200])
    return {"message_id": stored.id, "reply": result["reply"], "kind": result.get("kind")}


def _assistant_loop() -> None:
    from app.core.database import SessionLocal

    while not _stop.is_set():
        try:
            message_id = _assist_queue.get(timeout=5)
        except queue.Empty:
            continue
        db = SessionLocal()
        try:
            assist(db, message_id)
        except Exception as exc:  # noqa: BLE001 - research is a courtesy; keep going
            logger.error("inbox", "Looking into a message failed", {"error": str(exc)[:300]})
        finally:
            db.close()


def _ensure_assistant() -> None:
    global _assist_thread
    if _assist_thread is None or not _assist_thread.is_alive():
        _assist_thread = threading.Thread(target=_assistant_loop, name="cerebro-inbox-assist",
                                          daemon=True)
        _assist_thread.start()


# ------------------------------------------------------------------ loop
def _loop() -> None:
    from app.core.database import SessionLocal

    _stop.wait(15)                       # let the app finish starting
    while not _stop.is_set():
        db = SessionLocal()
        try:
            check_once(db)
        except Exception as exc:  # noqa: BLE001 - the monitor must never die
            logger.error("inbox", "Inbox check failed", {"error": str(exc)[:300]})
        finally:
            db.close()
        _stop.wait(max(20, int(settings.INBOX_MONITOR_SECONDS or 60)))


def start() -> None:
    global _thread
    _stop.clear()
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_loop, name="cerebro-inbox-monitor", daemon=True)
        _thread.start()


def stop() -> None:
    _stop.set()


def reset() -> None:
    """Forget the catch-up state and the hourly count (tests only)."""
    _primed.clear()
    _assist_times.clear()
    _last_problem.clear()
