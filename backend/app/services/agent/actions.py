"""
Approval-gated changes to external systems.

A write tool never changes anything itself. It calls :func:`propose`, which
records an :class:`~app.models.agent_action.AgentAction` with a before/after
preview and returns the card Ask shows. The change runs only when the user
approves that card — then :func:`approve` looks up the executor registered
for the tool and runs it.

A system can be set to apply changes automatically (SharePoint, with
``SHAREPOINT_AUTO_APPLY``). Then :func:`propose_or_apply` records the change
exactly the same way and approves it at once, so the card, the before/after
and the audit trail are identical — and :func:`undo` puts it back.
"""

import json
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

from app.core import logger
from app.core.activity_state import activity
from app.models.agent_action import AgentAction

#: tool name -> fn(db, args) -> {"detail": str, ...}
EXECUTORS: Dict[str, Callable[[Session, dict], dict]] = {}
#: tool name -> fn(db, undo) -> {"detail": str}, where ``undo`` is what the
#: executor returned under "undo" when it made the change.
UNDOERS: Dict[str, Callable[[Session, dict], dict]] = {}


def executor(tool: str):
    """Register the function that performs an approved ``tool`` action."""
    def register(fn: Callable[[Session, dict], dict]):
        EXECUTORS[tool] = fn
        return fn
    return register


def undoer(tool: str):
    """Register the function that reverses a completed ``tool`` action."""
    def register(fn: Callable[[Session, dict], dict]):
        UNDOERS[tool] = fn
        return fn
    return register


def card(action: AgentAction) -> dict:
    result = action.result_dict()
    return {
        "type": "approval", "kind": "agent", "status": "awaiting_approval"
        if action.status == "draft" else action.status,
        "action_id": action.id, "tool": action.tool,
        "integration": action.integration, "title": action.title,
        "detail": action.summary, "preview": action.preview_dict(),
        "approve_label": "Approve", "discard_label": "Discard",
        "automatic": bool(result.get("automatic")),
        "can_undo": can_undo(action),
        "error": action.error,
    }


def can_undo(action: AgentAction) -> bool:
    return (action.status == "done" and action.tool in UNDOERS
            and bool(action.result_dict().get("undo")))


def auto_apply(integration: str) -> bool:
    """Has the user let changes in ``integration`` skip approval?"""
    try:
        from app.services import browser

        return browser.get(integration).auto_apply
    except Exception:  # noqa: BLE001 - unknown system: always ask
        return False


def propose_or_apply(db: Session, tool: str, integration: str, title: str, args: dict,
                     preview: dict = None, summary: str = None) -> Dict[str, Any]:
    """
    Record a change, and make it straight away if the user turned on
    automatic changes for that system. Returns ``{"action", "card",
    "applied", "outcome"}``; ``outcome`` is :func:`approve`'s result when it ran.
    """
    action = propose(db, tool, integration, title, args, preview, summary)
    outcome = None
    if auto_apply(integration):
        outcome = approve(db, action.id, automatic=True)
        db.refresh(action)
    return {"action": action, "card": card(action), "outcome": outcome,
            "applied": action.status == "done"}


def propose(db: Session, tool: str, integration: str, title: str, args: dict,
            preview: dict = None, summary: str = None) -> AgentAction:
    if tool not in EXECUTORS:
        raise ValueError(f"No executor registered for {tool}")
    action = AgentAction(tool=tool, integration=integration, title=title,
                         summary=summary, args=json.dumps(args, default=str),
                         preview=json.dumps(preview or {}, default=str), status="draft")
    db.add(action)
    db.commit()
    db.refresh(action)
    logger.info("agent", "Change proposed", {"tool": tool, "id": action.id})
    return action


def pending(db: Session) -> Optional[AgentAction]:
    return (db.query(AgentAction).filter(AgentAction.status == "draft")
            .order_by(AgentAction.created_at.desc(), AgentAction.id.desc()).first())


def _completion(title: str, detail: str = None, status: str = "complete") -> dict:
    return {"type": "completion", "status": status, "title": title, "detail": detail}


def approve(db: Session, action_id: int, automatic: bool = False) -> Dict[str, Any]:
    action = db.get(AgentAction, action_id)
    if action is None:
        return {"reply": "That change no longer exists.", "kind": "completion",
                "cards": [_completion("Change not found", status="error")]}
    if action.status == "failed":
        action.status, action.error = "draft", None     # approving again retries it
    if action.status != "draft":
        return {"reply": f"That change is already {action.status}.", "kind": "completion",
                "agent_action": action.to_dict(),
                "cards": [_completion("Nothing to approve", action.status, "warning")]}

    run = EXECUTORS.get(action.tool)
    if run is None:
        action.status, action.error = "failed", "No executor for this change"
        db.commit()
        return {"reply": "Cerebro no longer knows how to make that change.", "kind": "completion",
                "cards": [_completion("Change failed", action.error, "error")]}

    action.status = "running"
    db.commit()
    try:
        with activity("writing", action.title):
            result = run(db, action.args_dict()) or {}
    except Exception as exc:  # noqa: BLE001 - recorded and explained
        action.status, action.error = "failed", str(exc)[:500]
        db.commit()
        logger.error("agent", "Approved change failed", {"tool": action.tool, "error": str(exc)})
        reply = f"I couldn't make that change: {exc}"
        cards = [_completion("Change failed", str(exc)[:200], "error")]
        from app.services.browser.connector import SignInRequired

        if isinstance(exc, SignInRequired):
            cards.insert(0, {"type": "signin", "integration": exc.connector.name,
                             "title": f"Sign in to {exc.connector.label}",
                             "detail": "Then approve the change again from Activity."})
            action.status = "draft"
            action.error = None
            db.commit()
        return {"reply": reply, "kind": "completion", "agent_action": action.to_dict(),
                "cards": cards}

    action.status = "done"
    if automatic:
        result = {**result, "automatic": True}
    action.result = _result_json(result)
    db.commit()
    detail = result.get("detail") or action.title
    first = ("Applied automatically — undo it from the card if it's not right" if automatic
             else "Approval recorded")
    return {"reply": f"Done — {detail}", "kind": "completion", "notify": True,
            "agent_action": action.to_dict(),
            "cards": [{"type": "progress", "status": "complete", "title": first},
                      _completion(action.title, detail)]}


def _result_json(result: dict) -> str:
    """The result as JSON, shortened without ever cutting the JSON in half."""
    text = json.dumps(result, default=str)
    if len(text) <= 4000:
        return text
    keep = {key: result[key] for key in ("detail", "url", "undo", "automatic") if key in result}
    return json.dumps(keep, default=str)[:4000]


def undo(db: Session, action_id: int) -> Dict[str, Any]:
    """Reverse a change that was made — automatically or after approval."""
    action = db.get(AgentAction, action_id)
    if action is None:
        return {"reply": "That change no longer exists.", "kind": "completion",
                "cards": [_completion("Change not found", status="error")]}
    if not can_undo(action):
        reason = ("it was already undone" if action.status == "undone"
                  else "it hasn't been made" if action.status in ("draft", "discarded")
                  else "Cerebro can't reverse this kind of change")
        return {"reply": f"Nothing to undo — {reason}.", "kind": "completion",
                "agent_action": action.to_dict(),
                "cards": [_completion("Nothing to undo", reason, "warning")]}
    result = action.result_dict()
    try:
        with activity("writing", f"Undoing: {action.title}"):
            outcome = UNDOERS[action.tool](db, result["undo"]) or {}
    except Exception as exc:  # noqa: BLE001 - explained, the change stays as it is
        logger.error("agent", "Undo failed", {"tool": action.tool, "error": str(exc)})
        return {"reply": f"I couldn't undo that: {exc}", "kind": "completion",
                "agent_action": action.to_dict(),
                "cards": [_completion("Undo failed", str(exc)[:200], "error")]}
    action.status = "undone"
    action.result = _result_json({**result, "undo": None, "undone": outcome.get("detail")})
    db.commit()
    detail = outcome.get("detail") or f"{action.title} was reversed"
    return {"reply": f"Undone — {detail}", "kind": "completion", "notify": True,
            "agent_action": action.to_dict(),
            "cards": [_completion("Change undone", detail)]}


def refresh_cards(db: Session, cards: list) -> list:
    """Cards as stored in a chat, with each change's current status.

    A card is saved with the status it had when the answer was written, so
    without this an approved change would still offer Approve after reload.
    """
    drafts = [c.get("action_id") for c in cards or []
              if isinstance(c, dict) and c.get("type") == "draft" and c.get("action_id")]
    if drafts:
        from app.models.enterprise import EnterpriseAction
        from app.services.ask_tools import _draft_card

        current = {row.id: row for row in db.query(EnterpriseAction)
                   .filter(EnterpriseAction.id.in_(drafts))}
        cards = [{**c, **_draft_card(current[c["action_id"]])}
                 if isinstance(c, dict) and c.get("type") == "draft" and c.get("action_id") in current
                 else c for c in cards]
    ids = [c.get("action_id") for c in cards or []
           if isinstance(c, dict) and c.get("kind") == "agent" and c.get("action_id")]
    if not ids:
        return cards
    live = {row.id: row for row in db.query(AgentAction).filter(AgentAction.id.in_(ids))}
    return [card(live[c["action_id"]]) if isinstance(c, dict) and c.get("kind") == "agent"
            and c.get("action_id") in live else c for c in cards]


def discard(db: Session, action_id: int) -> Dict[str, Any]:
    action = db.get(AgentAction, action_id)
    if action is None:
        return {"reply": "That change no longer exists.", "kind": "completion"}
    if action.status not in ("draft", "failed"):
        return {"reply": f"That change is already {action.status} and can't be discarded.",
                "kind": "completion", "agent_action": action.to_dict()}
    action.status = "discarded"
    db.commit()
    return {"reply": "Discarded. Nothing was changed.", "kind": "completion",
            "agent_action": action.to_dict(),
            "cards": [_completion("Change discarded", "Nothing was changed")]}
