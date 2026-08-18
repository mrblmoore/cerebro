"""
API for the Outlook/Teams bridge.

Inbound messages arrive as files (see ``enterprise_service``), but the same
normalisation is exposed here so a flow can POST directly if a folder is not
practical, and so the importer script can run against a remote Cerebro.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core import logger
from app.core.config import settings
from app.core.database import get_db
from app.models.enterprise import EnterpriseAction, EnterpriseMessage
from app.services import enterprise_service
from app.services.enterprise_service import EnterpriseService
from app.services.llm_service import LLMService

router = APIRouter(prefix="/api/enterprise", tags=["enterprise"])

VALID_ACTIONS = {"send_email", "reply_email", "send_teams_message", "reply_teams_message"}


class ActionCreate(BaseModel):
    action: str
    body: str
    source: Optional[str] = None
    in_reply_to: Optional[int] = None
    to: Optional[List[str]] = None
    chat_or_channel: Optional[str] = None
    thread_id: Optional[str] = None
    subject: Optional[str] = None
    #: False writes a draft only. True writes the file Power Automate collects,
    #: which is the moment the message actually goes out.
    send: bool = False


class DraftRequest(BaseModel):
    instruction: Optional[str] = None
    tone: Optional[str] = None


# ------------------------------------------------------------------ status
@router.get("/status")
def bridge_status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Folder configuration and how much is waiting."""
    payload = enterprise_service.status()
    payload["messages"] = db.query(EnterpriseMessage).count()
    payload["queued_actions"] = (db.query(EnterpriseAction)
                                 .filter(EnterpriseAction.status == "queued").count())
    payload["auto_send"] = settings.ENTERPRISE_AUTO_SEND
    return payload


@router.post("/sync")
def sync_now(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Drain the inbox folder immediately rather than waiting for the next sweep."""
    return enterprise_service.drain_inbox(db)


# ----------------------------------------------------------------- inbound
@router.post("/ingest")
def ingest(payload: Dict[str, Any], db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Ingest one Outlook or Teams payload. Accepts the Power Automate shape."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object.")
    return EnterpriseService(db).ingest_payload(payload, source_file="api")


@router.get("/messages")
def list_messages(
    source: Optional[str] = Query(None, pattern="^(outlook|teams)$"),
    urgency: Optional[str] = Query(None, pattern="^(high|medium|normal)$"),
    case_id: Optional[str] = None,
    unhandled_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    messages = EnterpriseService(db).list_messages(
        source=source, urgency=urgency, case_id=case_id,
        unhandled_only=unhandled_only, limit=limit)
    return {"count": len(messages), "messages": [m.to_dict() for m in messages]}


@router.get("/messages/{message_id}")
def get_message(message_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    message = db.query(EnterpriseMessage).get(message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    payload = message.to_dict(include_body=True)
    if message.thread_id:
        payload["thread"] = [m.to_dict() for m
                             in EnterpriseService(db).thread(message.thread_id)]
    return payload


@router.post("/messages/{message_id}/handled")
def mark_handled(message_id: int, handled: bool = True,
                 db: Session = Depends(get_db)) -> Dict[str, Any]:
    message = db.query(EnterpriseMessage).get(message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    message.handled = handled
    db.commit()
    return {"ok": True, "id": message_id, "handled": handled}


@router.get("/briefing")
def briefing(hours: int = Query(12, ge=1, le=168),
             db: Session = Depends(get_db)) -> Dict[str, Any]:
    """What arrived, what is urgent, and who is waiting."""
    return EnterpriseService(db).briefing(hours=hours)


# ---------------------------------------------------------------- drafting
@router.post("/messages/{message_id}/draft")
def draft_reply(message_id: int, request: DraftRequest = None,
                db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Draft a reply to a message. Returns text only — nothing is sent."""
    message = db.query(EnterpriseMessage).get(message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    llm = LLMService()
    if not llm.enabled:
        raise HTTPException(
            status_code=409,
            detail="No AI provider configured. Set one up in Settings → AI Provider.",
        )

    request = request or DraftRequest()
    channel = "email" if message.source == "outlook" else "Teams message"
    thread = EnterpriseService(db).thread(message.thread_id) if message.thread_id else []
    history = "\n\n".join(
        f"{m.sender or 'unknown'}: {(m.body or m.preview or '')[:800]}"
        for m in thread[-4:]
    ) or f"{message.sender or 'unknown'}: {(message.body or '')[:1500]}"

    prompt = f"""Draft a reply to this {channel}.

From: {message.sender_name or message.sender or 'unknown'}
Subject: {message.subject or '(none)'}
{f'Case: {message.case_id}' if message.case_id else ''}

Conversation:
{history}

{f'Additional instruction: {request.instruction}' if request.instruction else ''}
Tone: {request.tone or 'professional, warm, direct'}

Write only the reply body — no subject line, no signature, no preamble.
Keep it short. If a concrete answer is not possible, say what you are doing
about it and when you will follow up."""

    draft = llm._call_llm(prompt)
    logger.info("enterprise", "Drafted reply", {"message_id": message_id})
    return {
        "message_id": message_id,
        "source": message.source,
        "subject": (message.subject if (message.subject or "").lower().startswith("re:")
                    else f"Re: {message.subject}" if message.subject else None),
        "to": [message.sender] if message.sender else [],
        "draft": draft,
    }


# --------------------------------------------------------------- outbound
@router.post("/actions")
def create_action(request: ActionCreate, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Create an outbound action.

    With ``send: false`` this only records a draft. With ``send: true`` the JSON
    file is written to the outbox, which is the point of no return — a Power
    Automate flow is watching that folder and will send it.
    """
    if request.action not in VALID_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown action. Expected one of: {', '.join(sorted(VALID_ACTIONS))}",
        )
    if not request.body.strip():
        raise HTTPException(status_code=400, detail="An action needs a body.")

    service = EnterpriseService(db)
    record = service.create_action(
        action=request.action, body=request.body, source=request.source,
        in_reply_to=request.in_reply_to, to=request.to,
        chat_or_channel=request.chat_or_channel, thread_id=request.thread_id,
        subject=request.subject,
        send=request.send or settings.ENTERPRISE_AUTO_SEND,
    )
    if record.status == "failed":
        raise HTTPException(status_code=409, detail=record.status_detail)
    return record.to_dict()


@router.get("/actions")
def list_actions(status: Optional[str] = None, limit: int = Query(50, ge=1, le=200),
                 db: Session = Depends(get_db)) -> Dict[str, Any]:
    query = db.query(EnterpriseAction)
    if status:
        query = query.filter(EnterpriseAction.status == status)
    actions = query.order_by(EnterpriseAction.created_at.desc()).limit(limit).all()
    return {"count": len(actions), "actions": [a.to_dict() for a in actions]}


@router.post("/actions/{action_id}/send")
def send_action(action_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Approve a draft: write it to the outbox for Power Automate to pick up."""
    record = db.query(EnterpriseAction).get(action_id)
    if not record:
        raise HTTPException(status_code=404, detail="Action not found")
    if record.status == "queued":
        return record.to_dict()

    record = EnterpriseService(db).dispatch_action(record)
    if record.status == "failed":
        raise HTTPException(status_code=409, detail=record.status_detail)
    return record.to_dict()


@router.delete("/actions/{action_id}")
def delete_action(action_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Discard a draft. Already-queued actions cannot be recalled from disk."""
    record = db.query(EnterpriseAction).get(action_id)
    if not record:
        raise HTTPException(status_code=404, detail="Action not found")
    if record.status == "queued":
        raise HTTPException(
            status_code=409,
            detail="This action is already in the outbox and may have been sent. "
                   "Remove the file from the outbox folder to stop it.",
        )
    db.delete(record)
    db.commit()
    return {"ok": True, "deleted": action_id}
