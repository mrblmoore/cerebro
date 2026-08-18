"""
Task executors — what actually happens when a task fires.

Each task ``kind`` maps to one function here. They share a rule: autonomous
tasks act, non-autonomous tasks produce something and set the task to
``needs_review`` so the user approves it. Nothing that leaves the machine (an
email, a Teams reply) is ever sent without approval, regardless of the flag —
autonomy covers local actions like editing a document, not outbound messages.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.core import logger
from app.models.task import Task


def execute(db: Session, task: Task) -> Dict[str, Any]:
    handler = HANDLERS.get(task.kind, _reminder)
    return handler(db, task, _spec(task))


def _spec(task: Task) -> Dict[str, Any]:
    try:
        return json.loads(task.spec) if task.spec else {}
    except ValueError:
        return {}


# --------------------------------------------------------------- reminder
def _reminder(db: Session, task: Task, spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Surface a reminder as a nudge. It does nothing itself — its value is being
    raised at the right time, which the nudge system delivers.
    """
    from app.services.nudge_service import NudgeService

    NudgeService(db).raise_nudge(
        title=task.title,
        body=task.instruction or task.title,
        kind="reminder",
        task_id=task.id,
        case_id=task.case_id,
    )
    return {"ok": True, "status": "active", "detail": "reminder surfaced"}


# ------------------------------------------------------- document_update
def _document_update(db: Session, task: Task, spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Maintain a document over time — the "keep this updated daily under my name".

    The section belonging to the user is found by a heading marker, so Cerebro
    only ever touches *your* part. What it writes is generated in your voice and
    attributed to you; with autonomy off it is staged for review instead of
    written.
    """
    from app.services import document_editors, document_readers
    from app.services.document_readers import DocumentError
    from app.services.llm_service import LLMService
    from app.services.style_service import StyleService

    target = spec.get("document")
    if not target:
        return {"ok": False, "status": "needs_review",
                "detail": "No document set — tell me which file to keep updated."}

    path = _resolve_document(db, target)
    if path is None:
        return {"ok": False, "status": "needs_review",
                "detail": f"Could not find the document '{target}'."}

    try:
        content = document_readers.read(path)
    except DocumentError as exc:
        return {"ok": False, "status": "failed", "detail": str(exc)}

    llm = LLMService()
    if not llm.enabled:
        return {"ok": False, "status": "needs_review",
                "detail": "Need an AI provider to write the update."}

    section = spec.get("section") or "mine"
    owner = task.attribution or "the user"
    style = StyleService(db).drafting_directive()

    prompt = f"""Write today's update to add to a running document, as {owner}.

Document so far:
{content['text'][:6000]}

What to add: {spec.get('content_hint') or task.instruction or 'a brief status update'}
Date: {datetime.now():%Y-%m-%d}

Write only the new entry — one short paragraph or a bullet or two. No preamble.
{style}"""

    entry = llm._call_llm(prompt).strip()
    dated_entry = f"{datetime.now():%Y-%m-%d} — {entry}"

    # Editing is a local action, so an autonomous task may do it; otherwise stage
    # the text for the user to approve.
    if not task.autonomous:
        return {"ok": True, "status": "needs_review",
                "summary": f"Drafted an update for {path.name}",
                "detail": dated_entry}

    if path.suffix.lower() not in (".docx",):
        return {"ok": False, "status": "needs_review",
                "detail": f"Prepared an update, but I can only auto-append to Word "
                          f"documents. Add this to {path.name}:\n\n{dated_entry}"}

    try:
        heading = _find_user_section(content, section, owner)
        operations = [{"op": "insert_paragraph", "index": heading, "text": dated_entry}] \
            if heading is not None else [{"op": "append_paragraph", "text": dated_entry}]
        document_editors.apply(path, operations)
    except DocumentError as exc:
        return {"ok": False, "status": "needs_review",
                "detail": f"Prepared the update but could not write it ({exc}). "
                          f"Add manually:\n\n{dated_entry}"}

    _touch_document(db, path)
    logger.info("tasks", "Updated document", {"path": str(path), "task": task.id})
    return {"ok": True, "status": "active",
            "summary": f"Added today's entry to {path.name}",
            "detail": dated_entry}


def _find_user_section(content: Dict[str, Any], section: str,
                       owner: str) -> "int | None":
    """
    Locate the paragraph index just after the user's section heading.

    "Which parts belong to you" is answered by the document's own headings — a
    section named for the user, or explicitly asked for. New entries go directly
    under that heading so the rest of the document is never disturbed.
    """
    headings = content.get("outline", {}).get("headings", [])
    wanted = section.lower().strip()

    # A title (Word style "Title") is the document's name, never a section to
    # write into — skip it so "mine" cannot accidentally match the heading at the
    # very top.
    sections = [h for h in headings if (h.get("style") or "").lower() != "title"]

    # An explicitly named section wins.
    if wanted and wanted != "mine":
        for heading in sections:
            if wanted in (heading.get("text") or "").lower():
                return heading.get("paragraph", 0) + 1

    # "mine": prefer a heading that reads as the user's own — first person or a
    # personal-log phrasing — over a generic one.
    personal = ("my ", "mine", "personal", owner.lower())
    for heading in sections:
        text = (heading.get("text") or "").lower()
        if any(marker in text for marker in personal):
            return heading.get("paragraph", 0) + 1
    return None


# ------------------------------------------------------------ draft_reply
def _draft_reply(db: Session, task: Task, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Draft a reply the task is about, leaving it for approval."""
    from app.models.enterprise import EnterpriseMessage

    message_id = spec.get("message_id")
    message = db.query(EnterpriseMessage).get(message_id) if message_id else None
    if message is None:
        return {"ok": False, "status": "needs_review",
                "detail": "No message linked to draft a reply to."}

    from app.services.nudge_service import NudgeService

    NudgeService(db).raise_nudge(
        title=f"Reply to {message.sender_name or message.sender}?",
        body=task.instruction or f"Draft a reply to: {message.subject}",
        kind="draft_reply", task_id=task.id, case_id=task.case_id,
        action={"type": "draft_reply", "message_id": message.id})
    return {"ok": True, "status": "needs_review", "detail": "reply suggested"}


# ------------------------------------------------------------- summarise
def _summarise(db: Session, task: Task, spec: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.llm_service import LLMService

    llm = LLMService()
    if not llm.enabled:
        return {"ok": False, "status": "needs_review", "detail": "no AI provider"}
    summary = llm._call_llm(spec.get("prompt") or task.instruction or "Summarise recent activity.")
    return {"ok": True, "status": "needs_review", "summary": "Summary ready",
            "detail": summary}


# ---------------------------------------------------------------- helpers
def _resolve_document(db: Session, target: str) -> "Path | None":
    """Find a document by path, tracked name, or the most recent match."""
    path = Path(target).expanduser()
    if path.exists():
        return path

    from app.models.tracked_document import TrackedDocument

    record = (db.query(TrackedDocument)
              .filter(TrackedDocument.name == target).first()
              or db.query(TrackedDocument)
              .filter(TrackedDocument.path.like(f"%{target}%")).first())
    if record and Path(record.path).exists():
        return Path(record.path)
    return None


def _touch_document(db: Session, path: Path) -> None:
    from app.models.tracked_document import TrackedDocument

    record = (db.query(TrackedDocument)
              .filter(TrackedDocument.path == str(path)).first())
    if record:
        record.last_edited_by_cerebro = datetime.utcnow()
        record.content_mtime = None
        db.commit()


HANDLERS = {
    "reminder": _reminder,
    "document_update": _document_update,
    "draft_reply": _draft_reply,
    "summarise": _summarise,
}
