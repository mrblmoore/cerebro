"""
API for talking to Cerebro.

``POST /api/voice/transcribe`` — a recording in, its text out (the mic
button uses this so the words can be checked before sending).
``POST /api/voice/command`` — a recording (or text) in: it is transcribed,
acted on as a spoken request, and answered with the reply, its cards and a
short spoken summary as audio. "Yes, send it" / "approve" / "cancel" act on
the change waiting for approval; "stop" just stops.
``POST /api/voice/speak`` — text in, speech out.
``POST /api/voice/state`` — the push-to-talk key tells the tray and the desktop
buddy that Cerebro is listening.
"""

import base64
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.system import require_local_origin
from app.core import activity_state, logger
from app.core.config import settings
from app.core.database import get_db
from app.services import agents as _agents
from app.services import speech, tts

router = APIRouter(prefix="/api/voice", tags=["voice"],
                   dependencies=[Depends(require_local_origin)])

_listening: Dict[str, Optional[int]] = {"handle": None}


@router.get("/status")
def status() -> Dict[str, Any]:
    return {"enabled": bool(settings.VOICE_ENABLED), "hotkey": settings.VOICE_HOTKEY,
            "speech_to_text": speech.available(), "model": settings.VOICE_MODEL,
            "speak_replies": bool(settings.VOICE_SPEAK_REPLIES),
            "mic_mode": settings.VOICE_MIC_MODE, "voice": tts.status()}


def _transcribe(db, data: bytes, context: Dict[str, Any] = None) -> str:
    if not settings.VOICE_ENABLED:
        raise HTTPException(status_code=400, detail="Voice is switched off in Settings → Voice.")
    try:
        with activity_state.activity("listening", "Understanding what you said"):
            return speech.transcribe(data, speech.hints(db, context))["text"]
    except speech.SpeechUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/transcribe")
async def transcribe(audio: UploadFile = File(...), db: Session = Depends(get_db)) -> Dict[str, Any]:
    from app.api.tasks import _context

    data = await audio.read()
    return {"text": _transcribe(db, data, _context(db))}


def _speech(text: str, fmt: str, agent=None) -> Optional[Dict[str, Any]]:
    if not settings.VOICE_SPEAK_REPLIES or not text:
        return None
    try:
        spoken = tts.synthesize(tts.speakable(text), fmt=fmt,
                                voice=getattr(agent, "voice_name", None) or None,
                                rate=getattr(agent, "voice_rate", None))
    except Exception as exc:  # noqa: BLE001 - a silent answer is still an answer
        logger.warn("voice", "Couldn't speak the reply", {"error": str(exc)[:200]})
        return None
    return {"audio": base64.b64encode(spoken["audio"]).decode("ascii"), "mime": spoken["mime"],
            "provider": spoken["provider"]}


@router.post("/command")
def command(audio: Optional[UploadFile] = File(None), text: Optional[str] = Form(None),
            conversation_id: Optional[int] = Form(None), speak: bool = Form(True),
            fmt: str = Form("mp3"), notify: bool = Form(False),
            agent_id: Optional[int] = Form(None),
            db: Session = Depends(get_db)) -> Dict[str, Any]:
    """A spoken request, start to finish."""
    from app.api.tasks import _context

    context = _context(db)
    said = (text or "").strip()
    if audio is not None and not said:
        said = _transcribe(db, audio.file.read(), context)
    if not said:
        return {"transcript": "", "reply": "I didn't catch that.", "kind": "empty",
                "speech": _speech("I didn't catch that.", fmt) if speak else None}

    intent = speech.spoken_intent(said)
    if intent == "stop":
        return {"transcript": said, "reply": "", "kind": "stop", "stop": True}
    named, said = _addressed(db, said)
    if named is not None:
        agent_id, conversation_id = named.id, None
    service = _chat_for(db, conversation_id, agent_id)
    agent = _agents.get(db, service.conversation.agent_id)
    message = said
    if intent in ("approve", "discard") and _something_waiting(db):
        message = intent              # exactly the Approve / Discard button
    result = service.handle_message(message, context={**context, "voice": True})
    reply = result.get("reply") or ""
    if notify and reply:
        # Push-to-talk works with the window closed: the answer also arrives
        # as a notification that opens this chat.
        activity_state.notice(f"“{said[:60]}”", reply[:300], kind="voice",
                              link={"tab": "ask", "conversation_id": service.conversation_id})
    return {"transcript": said, "reply": reply, "kind": result.get("kind"),
            "cards": result.get("cards") or [], "conversation_id": service.conversation_id,
            "agent": {"id": agent.id, "name": agent.name},
            "speech": _speech(reply, fmt, agent) if speak else None}


def _addressed(db, said: str):
    """ "Inbox, reply to Priya…" → (the Inbox assistant, "reply to Priya…")."""
    from app.models.agent import Agent

    lowered = said.lower()
    for agent in sorted(db.query(Agent).filter(Agent.archived.is_(False)).all(),
                        key=lambda a: -len(a.name)):
        for name in {agent.name.lower(), agent.name.lower().split(" ")[0]}:
            for lead in (f"{name},", f"{name}:", f"hey {name},", f"hey {name} ", f"ok {name},"):
                if len(name) > 2 and lowered.startswith(lead):
                    return agent, said[len(lead):].strip(" ,.")
    return None, said


def _chat_for(db, conversation_id: Optional[int], agent_id: Optional[int]):
    """The chat a spoken request goes to: the one named, else the agent's latest."""
    from app.services import conversations
    from app.services.chat_service import ChatService

    if conversation_id:
        try:
            return ChatService(db, conversation_id)
        except LookupError:
            pass
    if agent_id:
        chats = conversations.listing(db, agent_id=agent_id, limit=1)
        if chats:
            return ChatService(db, chats[0]["id"])
        return ChatService(db, conversations.create(db, agent_id=agent_id).id)
    return ChatService(db, None)


#: A spoken "yes" only ever answers something prepared moments ago.
SPOKEN_DECISION_MINUTES = 30


def _something_waiting(db) -> bool:
    from datetime import datetime, timedelta

    from app.services.agent import actions
    from app.services.ask_tools import AskToolService

    recent = datetime.utcnow() - timedelta(minutes=SPOKEN_DECISION_MINUTES)
    waiting = [actions.pending(db), AskToolService(db).pending_action()]
    return any(item is not None and (item.created_at is None or item.created_at >= recent)
               for item in waiting)


class SpeakIn(BaseModel):
    text: str
    fmt: str = "mp3"
    agent_id: Optional[int] = None


@router.post("/speak")
def speak(body: SpeakIn, db: Session = Depends(get_db)) -> Response:
    try:
        agent = _agents.get(db, body.agent_id) if body.agent_id else None
        spoken = tts.synthesize(tts.speakable(body.text), fmt=body.fmt,
                                voice=getattr(agent, "voice_name", None) or None,
                                rate=getattr(agent, "voice_rate", None))
    except tts.SpeechFailed as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return Response(spoken["audio"], media_type=spoken["mime"],
                    headers={"X-Voice-Provider": spoken["provider"]})


class StateIn(BaseModel):
    state: str = "idle"           # listening | idle
    caption: Optional[str] = None


@router.post("/state")
def set_state(body: StateIn) -> Dict[str, Any]:
    handle = _listening["handle"]
    if body.state == "listening":
        if handle is None:
            _listening["handle"] = activity_state.begin("listening", body.caption or "Listening…")
        else:
            activity_state.update(handle, body.caption or "Listening…")
    elif handle is not None:
        activity_state.end(handle)
        _listening["handle"] = None
    return {"ok": True}


class NoticeIn(BaseModel):
    title: str = "Voice"
    body: str = ""


@router.post("/notice")
def notice(body: NoticeIn) -> Dict[str, Any]:
    return {"id": activity_state.notice(body.title, body.body, kind="voice")}
