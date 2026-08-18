"""
Context Engine — the state machine that tracks what the engineer is doing.

Events arrive from the browser extension, the desktop agent and the audio
recorder; each one updates a single ``ContextState`` row and may produce
recommendations. Event handling is a dispatch table rather than an if/elif
ladder, so adding an event type means adding one method.
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core import logger
from app.models.context_state import ContextState
from app.models.event import Event
from app.schemas.event import EventCreate
from app.services.llm_service import LLMService

#: Event types the engine understands, with the copy shown in the setup docs
#: and the dashboard's event legend.
KNOWN_EVENTS = {
    "CRM_CASE_OPENED": "A CRM case was opened",
    "CRM_CASE_CLOSED": "A CRM case was closed",
    "CALL_STARTED": "A call started",
    "CALL_ENDED": "A call ended",
    "REMOTE_SESSION_CONNECTED": "A remote support session connected",
    "REMOTE_SESSION_DISCONNECTED": "A remote support session disconnected",
    "APPLICATION_CHANGED": "The active window or URL changed",
    "TRANSCRIPT": "A call transcript was captured",
}


class ContextEngine:
    def __init__(self, db: Session):
        self.db = db
        self.llm = LLMService()

    # ------------------------------------------------------------- state
    def get_current_context(self) -> Optional[ContextState]:
        return self.db.query(ContextState).order_by(ContextState.updated_at.desc()).first()

    def init_context(self) -> ContextState:
        existing = self.get_current_context()
        if existing:
            return existing

        context = ContextState()
        self.db.add(context)
        self.db.commit()
        self.db.refresh(context)
        return context

    def reset_context(self) -> ContextState:
        """Clear the live context — exposed as a 'Reset' button in the widget."""
        context = self.get_current_context() or self.init_context()
        context.crm_case = None
        context.crm_system = None
        context.customer = None
        context.call_active = False
        context.remote_session_active = False
        context.remote_host = None
        context.active_application = None
        context.active_url = None
        context.window_title = None
        context.last_suggestion = None
        self.db.commit()
        self.db.refresh(context)
        logger.info("context_engine", "Context reset")
        return context

    # ------------------------------------------------------- event entry
    def process_event(self, event_data: EventCreate) -> Dict[str, Any]:
        context = self.get_current_context() or self.init_context()

        event = Event(
            event_type=event_data.event_type,
            case_id=event_data.case_id,
            source=event_data.source,
            data=event_data.data or {},
            screenshot_path=event_data.screenshot_path,
            ocr_text=event_data.ocr_text,
        )
        self.db.add(event)
        self.db.flush()  # assign event.id before handlers reference it

        logger.info("context_engine", "Received event", {
            "type": event_data.event_type,
            "source": event_data.source,
            "case_id": event_data.case_id,
        })

        handler = self._handlers().get(event_data.event_type)
        if handler:
            handler(context, event_data, event)
        else:
            logger.warn("context_engine", "Unhandled event type",
                        {"type": event_data.event_type})

        recommendations = self._generate_recommendations(context, event_data)
        if recommendations:
            context.last_suggestion = recommendations[0]["message"]
            context.last_suggestion_time = datetime.utcnow()

        self.db.add(context)
        self.db.commit()

        return {
            "event_id": event.id,
            "context": context.to_dict(),
            "recommendations": recommendations,
        }

    def _handlers(self):
        return {
            "CRM_CASE_OPENED": self._on_case_opened,
            "CRM_CASE_CLOSED": self._on_case_closed,
            "CALL_STARTED": self._on_call_started,
            "CALL_ENDED": self._on_call_ended,
            "REMOTE_SESSION_CONNECTED": self._on_remote_connected,
            "REMOTE_SESSION_DISCONNECTED": self._on_remote_disconnected,
            "APPLICATION_CHANGED": self._on_application_changed,
            "TRANSCRIPT": self._on_transcript,
        }

    # ---------------------------------------------------------- handlers
    @staticmethod
    def _on_case_opened(context, event_data, event):
        data = event_data.data or {}
        context.crm_case = data.get("case_id") or event_data.case_id
        context.crm_system = data.get("system", "Salesforce")
        context.customer = data.get("customer") or context.customer

    @staticmethod
    def _on_case_closed(context, event_data, event):
        context.crm_case = None
        context.customer = None

    @staticmethod
    def _on_call_started(context, event_data, event):
        context.call_active = True

    @staticmethod
    def _on_call_ended(context, event_data, event):
        context.call_active = False

    @staticmethod
    def _on_remote_connected(context, event_data, event):
        context.remote_session_active = True
        context.remote_host = (event_data.data or {}).get("host")

    @staticmethod
    def _on_remote_disconnected(context, event_data, event):
        context.remote_session_active = False
        context.remote_host = None

    @staticmethod
    def _on_application_changed(context, event_data, event):
        data = event_data.data or {}
        context.active_application = data.get("application")
        context.active_url = data.get("url")
        context.window_title = data.get("title")

    def _on_transcript(self, context, event_data, event) -> None:
        data = event_data.data or {}
        transcript_text = data.get("transcript") or data.get("text") or ""

        self._store_transcript(context, event_data, event, transcript_text)

        if not transcript_text.strip() or not self.llm.enabled:
            return

        try:
            case_data = {
                "customer": context.customer,
                "title": (context.active_application or "Support call")[:200],
                "error_code": None,
                "application": context.active_application,
                "transcript": transcript_text,
            }
            summary = self.llm.generate_case_summary(case_data)
            steps = self.llm.generate_troubleshooting_steps(case_data, context.to_dict())

            if context.crm_case:
                from app.models.case import Case

                case_obj = self.db.query(Case).filter(Case.case_id == context.crm_case).first()
                if case_obj:
                    case_obj.ai_summary = "\n\n".join(filter(None, [case_obj.ai_summary, summary]))
                    case_obj.troubleshooting_steps = "\n\n".join(
                        filter(None, [case_obj.troubleshooting_steps, steps])
                    )
                    self.db.add(case_obj)
        except Exception as exc:
            logger.error("context_engine", "Transcript post-processing failed", {"error": str(exc)})

    def _store_transcript(self, context, event_data, event, transcript_text: str) -> None:
        from app.models.audio import AudioRecording, Transcription

        data = event_data.data or {}
        try:
            recording = None
            if data.get("audio_path"):
                recording = AudioRecording(
                    audio_path=data["audio_path"],
                    trigger_event_id=event.id,
                    source=event_data.source,
                    start_time=_parse_iso(data.get("start_time")),
                    end_time=_parse_iso(data.get("end_time")),
                    duration=data.get("duration"),
                    status="transcribed",
                )
                self.db.add(recording)
                self.db.flush()

            segments = data.get("segments")
            self.db.add(Transcription(
                audio_id=recording.id if recording else None,
                transcript_text=transcript_text,
                provider=data.get("provider") or data.get("transcription_provider") or "local",
                confidence=str(data.get("confidence", "")),
                details=json.dumps(segments) if segments else None,
            ))
        except Exception as exc:
            logger.error("context_engine", "Failed to persist transcript", {"error": str(exc)})

    # --------------------------------------------------- recommendations
    def current_recommendations(self) -> List[Dict[str, str]]:
        """
        Suggestions derived from the *current* state rather than a single event.

        The widget polls this so it always has something useful on screen, even
        if the engineer started it halfway through a call.
        """
        context = self.get_current_context()
        recommendations: List[Dict[str, str]] = []

        def add(kind: str, message: str, priority: str = "medium", action: str = None):
            recommendations.append({
                "type": kind, "message": message, "priority": priority, "action": action,
            })

        if context is None or not context.crm_case:
            add("open_case",
                "No case is open. Open one in your CRM and Cerebro will follow along.",
                "low")
        else:
            add("retrieve_docs",
                f"Search the knowledge base for {context.customer or context.crm_case}.",
                "high", action=context.customer or context.crm_case)

        if context and context.call_active:
            if context.crm_case:
                add("prepare_notes", "Call in progress — notes will be drafted when it ends.", "medium")
            else:
                add("link_case", "Call in progress with no case open. Link one now.", "high")

        if context and context.remote_session_active:
            add("capture_evidence",
                f"Remote session to {context.remote_host or 'host'} is live — "
                "capture before/after evidence.", "medium")

        if not self.llm.enabled:
            add("configure_ai",
                "AI generation is off. Connect a provider in Settings for automatic summaries.",
                "low")

        return recommendations


    def _generate_recommendations(self, context: ContextState,
                                  event: EventCreate) -> List[Dict[str, str]]:
        """Short, actionable nudges rendered in the widget's Suggestions tab."""
        recommendations: List[Dict[str, str]] = []

        def add(kind: str, message: str, priority: str = "medium", action: str = None):
            recommendations.append({
                "type": kind, "message": message, "priority": priority, "action": action,
            })

        if event.event_type == "CRM_CASE_OPENED":
            who = context.customer or "this customer"
            add("retrieve_docs", f"Pulling knowledge base matches for {who}.", "high",
                action=context.customer or context.crm_case)

        if event.event_type == "CALL_STARTED":
            if context.crm_case:
                add("prepare_notes",
                    "Recording context — I'll draft case notes when the call ends.", "medium")
            else:
                add("link_case",
                    "Call started with no case open. Open the case so notes get attached.", "high")

        if event.event_type == "CALL_ENDED" and context.crm_case:
            add("summarise", f"Call ended. Generate a summary for case {context.crm_case}?",
                "high", action=context.crm_case)

        if event.event_type == "REMOTE_SESSION_CONNECTED":
            add("capture_evidence",
                f"Remote session to {context.remote_host or 'host'} is live — "
                "capture before/after evidence for the case.", "medium")

        if event.event_type == "TRANSCRIPT" and not self.llm.enabled:
            add("configure_ai",
                "Transcript saved. Connect an AI provider in Settings to auto-summarise calls.",
                "low")

        return recommendations


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
