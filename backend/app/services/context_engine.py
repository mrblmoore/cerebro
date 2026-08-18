"""
Context Engine - State machine for tracking case, call, and session state.
Manages current context and transitions based on events.
"""

from sqlalchemy.orm import Session
from app.models.context_state import ContextState
from app.models.event import Event
from app.schemas.event import EventCreate
from typing import Dict, Any, Optional
import json

# Import LLMService lazily to avoid circular imports at module import time
from app.services.llm_service import LLMService


class ContextEngine:
    def __init__(self, db: Session):
        self.db = db
        self.llm = LLMService()
    
    def get_current_context(self) -> Optional[ContextState]:
        """Get the current context state."""
        return self.db.query(ContextState).order_by(ContextState.updated_at.desc()).first()
    
    def init_context(self) -> ContextState:
        """Initialize a new context state."""
        existing = self.get_current_context()
        if existing:
            return existing
        
        context = ContextState()
        self.db.add(context)
        self.db.commit()
        self.db.refresh(context)
        return context
    
    def process_event(self, event_data: EventCreate) -> Dict[str, Any]:
        """
        Process an event and update context state.
        Returns recommendations if applicable.
        """
        from app.core import logger
        context = self.get_current_context() or self.init_context()
        
        # Store the event
        event = Event(
            event_type=event_data.event_type,
            case_id=event_data.case_id,
            source=event_data.source,
            data=event_data.data,
            screenshot_path=event_data.screenshot_path,
            ocr_text=event_data.ocr_text
        )
        self.db.add(event)
        logger.info('context_engine', 'Received event', {'type': event_data.event_type, 'source': event_data.source, 'case_id': event_data.case_id})
        
        # Update context based on event type
        if event_data.event_type == "CRM_CASE_OPENED":
            context.crm_case = event_data.data.get("case_id")
            context.crm_system = event_data.data.get("system", "Salesforce")
            context.customer = event_data.data.get("customer")
        
        elif event_data.event_type == "CALL_STARTED":
            context.call_active = True
        
        elif event_data.event_type == "CALL_ENDED":
            context.call_active = False
        
        elif event_data.event_type == "REMOTE_SESSION_CONNECTED":
            context.remote_session_active = True
            context.remote_host = event_data.data.get("host")
        
        elif event_data.event_type == "REMOTE_SESSION_DISCONNECTED":
            context.remote_session_active = False
            context.remote_host = None
        
        elif event_data.event_type == "APPLICATION_CHANGED":
            context.active_application = event_data.data.get("application")
            context.active_url = event_data.data.get("url")
            context.window_title = event_data.data.get("title")
        
        # New: handle incoming transcripts
        elif event_data.event_type == "TRANSCRIPT":
            # event.data expected to include: transcript (text), audio_path, case_id (optional), segments (optional), provider
            transcript_text = event_data.data.get("transcript") or event_data.data.get("text")
            audio_path = event_data.data.get("audio_path")
            segments = event_data.data.get("segments")
            provider = event_data.data.get("provider") or event_data.data.get("transcription_provider")
            start_time_str = event_data.data.get("start_time")
            end_time_str = event_data.data.get("end_time")
            duration = event_data.data.get("duration")

            # Persist AudioRecording and Transcription
            try:
                from app.models.audio import AudioRecording, Transcription
                from datetime import datetime

                audio_record = None
                if audio_path:
                    # parse iso timestamps if present
                    def _parse_iso(ts):
                        if not ts:
                            return None
                        try:
                            # Handle trailing Z
                            if ts.endswith('Z'):
                                ts = ts.replace('Z', '+00:00')
                            return datetime.fromisoformat(ts)
                        except Exception:
                            return None

                    st = _parse_iso(start_time_str)
                    et = _parse_iso(end_time_str)

                    audio_record = AudioRecording(
                        audio_path=audio_path,
                        trigger_event_id=event.id if hasattr(event, 'id') else None,
                        source=event_data.source,
                        start_time=st,
                        end_time=et,
                        duration=duration,
                        status='transcribed',
                        notes=None
                    )
                    self.db.add(audio_record)
                    self.db.flush()

                # Store transcription row
                tr = Transcription(
                    audio_id=audio_record.id if audio_record else None,
                    transcript_text=transcript_text or "",
                    provider=provider or 'local',
                    confidence=str(event_data.data.get('confidence', '')),
                    details=(json.dumps(segments) if segments else None)
                )
                self.db.add(tr)
            except Exception as e:
                print(f"Error saving audio/transcript to DB: {e}")

            # Run LLM to summarize or extract entities
            try:
                # Build a case_data structure for summary generation
                case_data = {
                    "customer": context.customer,
                    "title": (context.active_application or "Support Call")[:200],
                    "error_code": None,
                    "application": context.active_application
                }
                # Generate summary and troubleshooting steps
                ai_summary = self.llm.generate_case_summary({**case_data, "transcript": transcript_text})
                troubleshooting = self.llm.generate_troubleshooting_steps({**case_data, "transcript": transcript_text}, context.to_dict())

                # If there's an active case, attach summary to it
                if context.crm_case:
                    from app.models.case import Case
                    case_obj = self.db.query(Case).filter(Case.case_id == context.crm_case).first()
                    if case_obj:
                        case_obj.ai_summary = (case_obj.ai_summary or "") + "\n\n" + ai_summary
                        case_obj.troubleshooting_steps = (case_obj.troubleshooting_steps or "") + "\n\n" + troubleshooting
                        self.db.add(case_obj)

                # Optionally store a Memory entry (not implemented fully)
            except Exception as e:
                # Log but don't fail
                print(f"Error processing transcript LLM: {e}")
        
        self.db.add(context)
        self.db.commit()
        
        return {
            "event_id": event.id,
            "context": context.to_dict(),
            "recommendations": self._generate_recommendations(context, event_data)
        }
    
    def _generate_recommendations(self, context: ContextState, event: EventCreate) -> list:
        """Generate recommendations based on current context."""
        recommendations = []
        
        # If case opened, suggest retrieving related documentation
        if event.event_type == "CRM_CASE_OPENED":
            recommendations.append({
                "type": "retrieve_docs",
                "message": f"Searching for relevant documentation for {context.customer}",
                "priority": "high"
            })
        
        # If call started with active case, suggest generating notes after
        if event.event_type == "CALL_STARTED" and context.crm_case:
            recommendations.append({
                "type": "prepare_notes",
                "message": "I'll capture the call and prepare case notes when you're done",
                "priority": "medium"
            })
        
        return recommendations
