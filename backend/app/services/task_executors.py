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


# ------------------------------------------------------------------ agent
#: How a scheduled run is put to the agent. The schedule itself is already
#: handled, so the model must do the work rather than schedule it again.
AGENT_TASK_PROMPT = ("This is a run of a job assigned to you ({schedule}). Do it now "
                     "and report what you found or did. The schedule is already handled — "
                     "do not create another task. For work with several steps, set out the "
                     "plan with update_plan and mark steps done as you go. If you need "
                     "something only the user knows, call ask_user and stop.\n\nJob: {work}")


def _agent(db: Session, task: Task, spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run an agent on the job, inside the chat it belongs to.

    The agent can use every tool in its scope; its answer is posted into the
    chat. Its progress is written to the job's run log and plan. Anything
    that would change an outside system is only prepared as an approval card,
    and the job waits (``waiting_approval``) until those are decided; a
    question to the user parks it in ``waiting_input``. Both resume by
    themselves (see :mod:`app.services.jobs`).
    """
    from app.services import agents, conversations, jobs
    from app.services.agent import loop
    from app.services.chat_service import ChatService
    from app.services.llm_service import LLMService

    if not LLMService().enabled:
        return {"ok": False, "status": "failed",
                "detail": "Connect an AI provider to run chat tasks."}

    agent = agents.get(db, task.agent_id)
    chat = None
    if task.conversation_id:
        try:
            chat = ChatService(db, task.conversation_id)
        except LookupError:
            chat = None
    if chat is None:
        # The chat was deleted, or the job was given to an agent directly.
        created = conversations.create(db, title=task.title)
        created.agent_id = agent.id
        db.commit()
        chat = ChatService(db, created.id)
        task.conversation_id = chat.conversation_id
        db.commit()

    work = spec.get("prompt") or task.instruction or task.title
    schedule = "one-off" if task.schedule == "once" else f"runs {task.schedule}"
    prompt = AGENT_TASK_PROMPT.format(schedule=schedule, work=work)
    plan = task.plan_list()
    if plan:
        prompt += "\n\nYour plan so far:\n" + "\n".join(
            f"- [{step['status']}] {step['title']}" for step in plan)
    if task.answer:
        prompt += f"\n\nYou asked: {task.question}\nThe user answered: {task.answer}\nCarry on."
    trigger = task.state_dict().get("trigger")
    if trigger:
        prompt += f"\n\nWhy this run started: {trigger}"
        jobs.set_state(task, trigger=None)
        db.commit()
    prompt += _skill_memory(task, spec)
    decisions = task.state_dict().get("decisions")
    if decisions:
        prompt += "\n\nWhat happened to the changes you prepared: " + "; ".join(decisions) \
            + ". Carry on from there; don't prepare them again."
        jobs.set_state(task, decisions=None)
        db.commit()

    context = {"conversation_id": chat.conversation_id, "task_run": task.id,
               **agents.context_for(agent)}
    if task.case_id:
        context["crm_case"] = task.case_id

    def emit(card: dict) -> None:
        try:
            if card.get("type") == "plan":
                jobs.set_plan(task, card.get("steps") or [])
                db.commit()
                jobs.event(db, task.id, "plan", card.get("title") or "Plan",
                           "\n".join(s if isinstance(s, str) else s.get("title", "")
                                     for s in card.get("steps") or []))
            elif card.get("type") == "plan_update":
                jobs.set_plan(task, card.get("steps") or [])
                db.commit()
                jobs.event(db, task.id, "step", card.get("title") or "Plan updated")
            elif card.get("type") == "progress" and card.get("status") != "running":
                jobs.event(db, task.id, "tool", card.get("title") or "Step", card.get("detail"))
        except Exception as exc:  # noqa: BLE001 - the log is a nicety; the job carries on
            logger.warn("jobs", "Couldn't record a step", {"error": str(exc)[:200]})

    checks = {"n": 0}

    def should_stop() -> bool:
        checks["n"] += 1
        jobs.heartbeat(task.id)
        fresh = db.query(Task.status).filter(Task.id == task.id).scalar()
        return fresh in ("paused", "cancelled")

    history = loop._history(db, chat.conversation_id)
    result = loop.run(db, prompt, context, history=history, emit=emit, agent=agent,
                      instructions=chat.conversation.instructions, should_stop=should_stop)

    if result.get("plan_steps"):
        jobs.set_plan(task, result["plan_steps"])
    meta = {key: result[key] for key in ("cards", "sources", "action", "tools_used")
            if result.get(key) not in (None, [], {})}
    meta["task"] = {"id": task.id, "title": task.title, "run": (task.run_count or 0) + 1}
    chat._store("assistant", result["reply"], kind="task_result", meta=meta,
                task_id=task.id, case_id=task.case_id)

    cards = result.get("cards") or []
    if any(c.get("automatic") for c in cards):
        jobs.set_state(task, auto_applied=True)
    waiting = [f"{'change' if c.get('kind') == 'agent' else 'draft'}:{c['action_id']}"
               for c in cards if c.get("action_id") and (
                   c.get("status") in ("awaiting_approval", "draft"))]
    db.commit()
    if result.get("stopped"):
        return {"ok": True, "status": None, "summary": "Stopped", "detail": result["reply"]}
    if result.get("question"):
        task.question = result["question"][:2000]
        task.answer = None
        db.commit()
        _tell(db, task, agent, f"{agent.name} has a question", task.question)
        return {"ok": True, "status": "waiting_input", "summary": "Waiting for your answer",
                "detail": result["reply"]}
    if waiting:
        jobs.set_state(task, waiting=waiting)
        db.commit()
        _tell(db, task, agent, f"{task.title}: waiting for your approval",
              f"Open “{chat.conversation.title or 'the chat'}” to review it.")
        return {"ok": True, "status": "waiting_approval", "summary": "Waiting for approval",
                "detail": result["reply"]}
    task.question = None
    _link_skill(db, task, spec)
    db.commit()
    return {"ok": True, "status": None, "summary": "Posted to chat", "detail": result["reply"]}


def skill_name(task: Task) -> str:
    """The skill a job's procedure is saved under."""
    from app.services import skills

    return skills.slug(f"job-{task.id}-{task.title or 'job'}")[:48]


def _skill_memory(task: Task, spec: Dict[str, Any]) -> str:
    """Remembering how it did it: follow saved steps, or save them once it works."""
    from app.services import skills

    name = spec.get("skill") or skill_name(task)
    saved = skills.exact(name)
    if saved:
        return ("\n\nYou saved how to do this job before — follow these steps (if the page, file "
                "or app has changed, adapt, and save the corrected steps with save_skill under "
                f"the same name “{name}”):\n{saved['instructions'][:4000]}")
    if spec.get("keep") or spec.get("learn"):
        return (f"\n\nWhen it has worked, save the steps that worked with save_skill, named "
                f"“{name}” (where to go, what to read, what to change, how to save), so later "
                "runs can follow them.")
    return ""


def _link_skill(db: Session, task: Task, spec: Dict[str, Any]) -> None:
    import json as _json

    from app.services import skills

    if spec.get("skill"):
        return
    name = skill_name(task)
    if skills.exact(name):
        spec = {**spec, "skill": name}
        task.spec = _json.dumps(spec)


def _tell(db: Session, task: Task, agent, title: str, body: str) -> None:
    from app.core import activity_state
    from app.services.nudge_service import NudgeService

    activity_state.notice(title, body, kind="job",
                          link={"tab": "ask", "conversation_id": task.conversation_id})
    NudgeService(db).raise_nudge(title=title, body=body, kind="task_result", task_id=task.id,
                                 case_id=task.case_id,
                                 dedupe_key=f"job:{task.id}:{task.run_count or 0}:{title[:40]}")


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
    "agent": _agent,
}
