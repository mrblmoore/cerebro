"""
Approval-gated changes to external systems.

A write tool never changes anything itself. It calls :func:`propose`, which
records an :class:`~app.models.agent_action.AgentAction` with a before/after
preview and returns the card Ask shows. The change runs only when the user
approves that card — then :func:`approve` looks up the executor registered
for the tool and runs it.
"""

import json
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

from app.core import logger
from app.core.activity_state import activity
from app.models.agent_action import AgentAction

#: tool name -> fn(db, args) -> {"detail": str, ...}
EXECUTORS: Dict[str, Callable[[Session, dict], dict]] = {}


def executor(tool: str):
    """Register the function that performs an approved ``tool`` action."""
    def register(fn: Callable[[Session, dict], dict]):
        EXECUTORS[tool] = fn
        return fn
    return register


def card(action: AgentAction) -> dict:
    return {
        "type": "approval", "kind": "agent", "status": "awaiting_approval"
        if action.status == "draft" else action.status,
        "action_id": action.id, "tool": action.tool,
        "integration": action.integration, "title": action.title,
        "detail": action.summary, "preview": action.preview_dict(),
        "approve_label": "Approve", "discard_label": "Discard",
    }


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


def approve(db: Session, action_id: int) -> Dict[str, Any]:
    action = db.get(AgentAction, action_id)
    if action is None:
        return {"reply": "That change no longer exists.", "kind": "completion",
                "cards": [_completion("Change not found", status="error")]}
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
    action.result = json.dumps(result, default=str)[:4000]
    db.commit()
    detail = result.get("detail") or action.title
    return {"reply": f"Done — {detail}", "kind": "completion", "notify": True,
            "agent_action": action.to_dict(),
            "cards": [{"type": "progress", "status": "complete", "title": "Approval recorded"},
                      _completion(action.title, detail)]}


def discard(db: Session, action_id: int) -> Dict[str, Any]:
    action = db.get(AgentAction, action_id)
    if action is None:
        return {"reply": "That change no longer exists.", "kind": "completion"}
    if action.status != "draft":
        return {"reply": f"That change is already {action.status} and can't be discarded.",
                "kind": "completion", "agent_action": action.to_dict()}
    action.status = "discarded"
    db.commit()
    return {"reply": "Discarded. Nothing was changed.", "kind": "completion",
            "agent_action": action.to_dict(),
            "cards": [_completion("Change discarded", "Nothing was changed")]}
