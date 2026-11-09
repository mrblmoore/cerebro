"""
Ideas — borrowed from OpenMuse: jobs the agents suggest, each with the
evidence behind it. Accept one and it becomes a job for that agent (you can
reword it first); dismiss it and it never comes back.

Where they come from (``refresh``, about every ten minutes):

* the two nudge detectors — an important message nobody replied to, a case
  worked on remotely but never written up — whose open nudges become ideas;
* cases quiet for a while (``IDEAS_QUIET_DAYS``) → a follow-up;
* the same question arriving three or more times → a knowledge article;
* a watch that fired → a follow-up (raised by :mod:`app.services.watches`);
* optionally (``IDEAS_AI``), at most hourly, the AI reading the latest mail
  and cases for anything else worth doing.

Each idea goes to the agent whose tool scope fits the work best, else the
default agent.
"""

import json
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.core import logger
from app.core.config import settings

#: idea kind -> the tool group the work needs
NEEDS = {"reply": "messaging", "case": "cases", "kb": "kb", "watch": "web", "other": "knowledge"}
QUIET_DAYS = 5
_last_ai = {"at": 0.0}


def route(db, kind: str) -> int:
    """The agent best placed to do this kind of work."""
    from app.models.agent import Agent
    from app.services import agents

    group = NEEDS.get(kind, "knowledge")
    candidates = [a for a in db.query(Agent).filter(Agent.archived.is_(False)).all()
                  if a.scope() and group in a.scope()]
    if candidates:
        # The most specialised one wins (fewest groups).
        return sorted(candidates, key=lambda a: (len(a.scope()), a.id))[0].id
    return agents.default_agent(db).id


def raise_idea(db, kind: str, title: str, reason: str, prompt: str, evidence: List[dict],
               dedupe_key: str, agent_id: int = None):
    from app.models.work import Idea

    if db.query(Idea.id).filter(Idea.dedupe_key == dedupe_key).first():
        return None
    idea = Idea(agent_id=agent_id or route(db, kind), title=title[:160], reason=reason,
                evidence=json.dumps(evidence[:5]), prompt=prompt, status="new",
                dedupe_key=dedupe_key[:200])
    db.add(idea)
    db.commit()
    return idea


def _evidence(message) -> Dict[str, Any]:
    return {"kind": "mail" if message.source == "outlook" else "message",
            "title": f"{message.sender_name or message.sender or 'Someone'}: {message.subject or ''}".strip(": "),
            "excerpt": (message.preview or message.body or "")[:240], "url": None,
            "message_id": message.id}


# ------------------------------------------------------------ detectors
def from_nudges(db) -> int:
    from app.models.enterprise import EnterpriseMessage
    from app.models.nudge import Nudge

    made = 0
    for nudge in db.query(Nudge).filter(Nudge.status == "open",
                                        Nudge.kind.in_(("unanswered_email", "case_not_updated"))):
        action = {}
        try:
            action = json.loads(nudge.action) if nudge.action else {}
        except ValueError:
            pass
        evidence = []
        if action.get("message_id"):
            message = db.get(EnterpriseMessage, action["message_id"])
            if message is not None:
                evidence.append(_evidence(message))
        if nudge.case_id:
            evidence.append({"kind": "case", "title": nudge.case_id, "excerpt": nudge.body or "",
                             "url": None})
        if nudge.kind == "unanswered_email":
            prompt = (f"Draft a reply for this: {nudge.body}"
                      + (f" (message #{action['message_id']}; use reply_to_message)"
                         if action.get("message_id") else ""))
            kind = "reply"
        else:
            prompt = (f"Case {nudge.case_id} was worked on but never written up. Summarise what "
                      "was done and prepare the case note for approval.")
            kind = "case"
        if raise_idea(db, kind, nudge.title, nudge.body or "", prompt, evidence,
                      f"nudge:{nudge.id}"):
            made += 1
    return made


def quiet_cases(db, days: int = None) -> int:
    from app.models.case import Case

    days = days or int(getattr(settings, "IDEAS_QUIET_DAYS", QUIET_DAYS) or QUIET_DAYS)
    cutoff = datetime.utcnow() - timedelta(days=days)
    made = 0
    for case in (db.query(Case).filter(Case.status.in_(("open", "active", "in progress")),
                                       Case.updated_at < cutoff).limit(20)):
        stamp = case.updated_at.strftime("%Y%m%d") if case.updated_at else "x"
        if raise_idea(db, "case", f"Follow up {case.case_id}",
                      f"Nothing has happened on {case.case_id} for over {days} days.",
                      f"Check where case {case.case_id} ({case.title or ''}) stands, and draft a "
                      "follow-up to the customer for approval.",
                      [{"kind": "case", "title": f"{case.case_id} {case.title or ''}".strip(),
                        "excerpt": (case.description or "")[:240], "url": case.url}],
                      f"quiet:{case.case_id}:{stamp}"):
            made += 1
    return made


_STOP = {"re", "fw", "fwd", "the", "a", "an", "and", "or", "to", "for", "of", "on", "in", "is",
         "with", "my", "our", "your", "please", "help", "issue", "question", "hi", "hello"}


def _topic(subject: str) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", (subject or "").lower()) if w not in _STOP]
    return " ".join(sorted(set(words))[:6])


def repeated_questions(db, days: int = 14, threshold: int = 3) -> int:
    from app.models.enterprise import EnterpriseMessage

    since = datetime.utcnow() - timedelta(days=days)
    groups = defaultdict(list)
    for message in (db.query(EnterpriseMessage).filter(EnterpriseMessage.ingested_at >= since)
                    .limit(2000)):
        topic = _topic(message.subject or "")
        if len(topic.split()) >= 2:
            groups[topic].append(message)
    made = 0
    for topic, messages in groups.items():
        senders = {m.sender or m.sender_name for m in messages}
        if len(messages) < threshold or len(senders) < 2:
            continue
        subject = messages[0].subject or topic
        if raise_idea(db, "kb", f"Write a KB article: {subject[:80]}",
                      f"{len(messages)} people asked about this in the last {days} days.",
                      f"Several people asked about “{subject}”. Check RightAnswers for an existing "
                      "article; if there isn't a good one, draft one from how these were answered.",
                      [_evidence(m) for m in messages[:5]], f"kb:{topic}"):
            made += 1
    return made


def from_watch(db, watch, what: str) -> None:
    stamp = datetime.now().strftime("%Y%m%d%H%M")
    raise_idea(db, "watch", f"Follow up: {watch.title}", what,
               f"The watch “{watch.title}” fired ({what}). Look at {watch.target}, say what changed "
               "and what should be done about it.",
               [{"kind": "web", "title": watch.title, "excerpt": what, "url": watch.target}],
               f"watch:{watch.id}:{stamp}", agent_id=watch.agent_id)


def ai_pass(db) -> int:
    """Optional: the AI suggests jobs from recent mail and cases (at most hourly)."""
    from app.models.enterprise import EnterpriseMessage
    from app.services.llm_service import LLMService

    if not getattr(settings, "IDEAS_AI", False) or time.time() - _last_ai["at"] < 3600:
        return 0
    llm = LLMService()
    if not llm.enabled:
        return 0
    _last_ai["at"] = time.time()
    since = datetime.utcnow() - timedelta(days=2)
    messages = (db.query(EnterpriseMessage).filter(EnterpriseMessage.ingested_at >= since)
                .order_by(EnterpriseMessage.id.desc()).limit(30).all())
    if not messages:
        return 0
    listing = "\n".join(f"#{m.id} {m.sender_name or m.sender}: {m.subject or ''} — "
                        f"{(m.preview or '')[:160]}" for m in messages)
    try:
        result = llm.chat([{"role": "user", "content": (
            "From these recent messages, suggest up to 3 concrete jobs a support engineer's "
            "assistant should do (only clearly useful ones). Reply with JSON only: "
            '[{"title": "...", "reason": "...", "prompt": "...", "kind": "reply|case|kb|other", '
            '"message_ids": [..]}]\n\n' + listing)}], max_tokens=600)
        found = json.loads(re.search(r"\[.*\]", result.get("content") or "", re.S).group(0))
    except Exception as exc:  # noqa: BLE001 - ideas are a nicety
        logger.warn("ideas", "AI ideas failed", {"error": str(exc)[:200]})
        return 0
    by_id = {m.id: m for m in messages}
    made = 0
    for item in found[:3]:
        if not isinstance(item, dict) or not item.get("title") or not item.get("prompt"):
            continue
        evidence = [_evidence(by_id[i]) for i in item.get("message_ids") or [] if i in by_id]
        if raise_idea(db, item.get("kind") or "other", str(item["title"]), str(item.get("reason") or ""),
                      str(item["prompt"]), evidence, f"ai:{_topic(str(item['title']))}"):
            made += 1
    return made


def refresh(db) -> int:
    made = 0
    for detector in (from_nudges, quiet_cases, repeated_questions, ai_pass):
        try:
            made += detector(db)
        except Exception as exc:  # noqa: BLE001 - one detector never stops the rest
            db.rollback()
            logger.warn("ideas", "Idea detector failed", {"detector": detector.__name__,
                                                         "error": str(exc)[:200]})
    return made


# ------------------------------------------------------------- decisions
def decide(db, idea, action: str, prompt: str = None, agent_id: int = None) -> Dict[str, Any]:
    from app.models.nudge import Nudge
    from app.models.task import Task
    from app.services import jobs
    from app.services.conversations import title_from

    if idea.status != "new":
        return {"ok": False, "detail": f"It was already {idea.status}."}
    if idea.dedupe_key.startswith("nudge:"):
        nudge = db.get(Nudge, int(idea.dedupe_key.split(":")[1]))
        if nudge is not None and nudge.status == "open":
            nudge.status = "acted" if action == "accept" else "dismissed"
            nudge.acted_at = datetime.utcnow()
    if action == "dismiss":
        idea.status = "dismissed"
        db.commit()
        return {"ok": True, "idea": idea.to_dict()}
    if action != "accept":
        return {"ok": False, "detail": f"Unknown action {action}"}
    work = (prompt or idea.prompt).strip()
    task = Task(title=title_from(idea.title)[:80], instruction=work, kind="agent",
                spec=json.dumps({"prompt": work, "idea": idea.id}), schedule="once",
                status="queued", source="idea", agent_id=agent_id or idea.agent_id,
                next_run=datetime.now())
    db.add(task)
    db.commit()
    idea.status, idea.task_id = "accepted", task.id
    if agent_id:
        idea.agent_id = agent_id
    db.commit()
    jobs.event(db, task.id, "status", "Started from an idea", idea.reason)
    return {"ok": True, "idea": idea.to_dict(), "task": task.to_dict()}


def listing(db, agent_id: Optional[int] = None, status: str = "new", limit: int = 50) -> List[dict]:
    from app.models.work import Idea
    from app.services import agents

    query = db.query(Idea)
    if status:
        query = query.filter(Idea.status == status)
    if agent_id is not None:
        default = agents.default_agent(db).id
        query = query.filter(Idea.agent_id == int(agent_id)) if int(agent_id) != default else \
            query.filter((Idea.agent_id == default) | (Idea.agent_id.is_(None)))
    return [row.to_dict() for row in query.order_by(Idea.id.desc()).limit(limit)]
