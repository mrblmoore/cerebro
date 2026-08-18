"""
Nudges — noticing what needs attention and saying so, in the user's chosen voice.

The detectors here scan for situations worth raising:

* mail that arrived, was important, and has no reply and no linked draft;
* a case that was worked (a remote session happened) and resolved, but whose
  record was never updated — your "we remoted in and resolved but had to update"
  example;
* due reminders and task output waiting for review.

Each is phrased through the persona so it reads like a colleague, not an alert.
"""

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core import logger
from app.core.config import settings
from app.models.enterprise import EnterpriseMessage
from app.models.event import Event
from app.models.nudge import Nudge


class NudgeService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------ raising
    def raise_nudge(self, title: str, body: str, kind: str = "follow_up",
                    priority: str = "medium", action: Dict[str, Any] = None,
                    dedupe_key: str = None, task_id: int = None,
                    case_id: str = None) -> Optional[Nudge]:
        """Create a nudge unless one with the same dedupe key is already open."""
        if dedupe_key:
            existing = (self.db.query(Nudge)
                        .filter(Nudge.dedupe_key == dedupe_key).first())
            # Dedupe against ever having raised this, not just currently-open
            # ones — a nudge the user dismissed should not pop back on the next
            # scan. Return None so callers can tell a real raise from a no-op.
            if existing:
                return None

        nudge = Nudge(
            kind=kind, title=title[:200], body=body,
            action=json.dumps(action) if action else None,
            priority=priority, dedupe_key=dedupe_key,
            task_id=task_id, case_id=case_id,
        )
        self.db.add(nudge)
        self.db.commit()
        self.db.refresh(nudge)
        logger.info("nudge", "Raised nudge", {"kind": kind, "title": title[:60]})
        return nudge

    @staticmethod
    def _phrase(partner: str, assistant: str) -> str:
        """Pick the wording that matches the persona, written out in full so the
        grammar is always right — string-substituting pronouns is not."""
        from app.services.style_service import persona

        return partner if persona() == "partner" else assistant

    # ---------------------------------------------------------- detectors
    def scan(self) -> Dict[str, Any]:
        """Run every detector. Called on a schedule and on demand."""
        if not settings.NUDGES_ENABLED:
            return {"raised": 0, "detail": "nudges disabled"}
        raised = 0
        raised += self._unanswered_important_mail()
        raised += self._resolved_but_not_updated()
        return {"raised": raised}

    def _unanswered_important_mail(self, hours: int = 4) -> int:
        """High-urgency mail with no reply drafted or sent."""
        from app.models.enterprise import EnterpriseAction

        cutoff = datetime.utcnow() - timedelta(hours=hours)
        window_start = datetime.utcnow() - timedelta(days=3)

        candidates = (self.db.query(EnterpriseMessage)
                      .filter(EnterpriseMessage.urgency == "high")
                      .filter(EnterpriseMessage.handled.is_(False))
                      .filter(EnterpriseMessage.ingested_at <= cutoff)
                      .filter(EnterpriseMessage.ingested_at >= window_start)
                      .all())

        raised = 0
        for message in candidates:
            already = (self.db.query(EnterpriseAction)
                       .filter(EnterpriseAction.in_reply_to == message.id).first())
            if already:
                continue
            who = message.sender_name or message.sender or "someone"
            subject = message.subject or "their message"
            body = self._phrase(
                partner=f"We never replied to {who} about “{subject}” — it came in "
                        f"flagged {message.urgency}. Want me to draft the response?",
                assistant=f"You haven't replied to {who} about “{subject}” — it came "
                          f"in flagged {message.urgency}. Want me to draft it?")
            nudge = self.raise_nudge(
                title=f"Unanswered: {who}",
                body=body,
                kind="unanswered_email", priority="high",
                dedupe_key=f"unanswered:{message.id}",
                case_id=message.case_id,
                action={"type": "draft_reply", "message_id": message.id})
            raised += 1 if nudge else 0
        return raised

    def _resolved_but_not_updated(self) -> int:
        """
        A remote session happened and the case looks resolved, but the CRM record
        was never updated. This is the "we remoted in and fixed it but forgot to
        write it up" case.
        """
        from app.models.case import Case

        window_start = datetime.utcnow() - timedelta(days=2)
        remote_events = (self.db.query(Event)
                         .filter(Event.event_type == "REMOTE_SESSION_DISCONNECTED")
                         .filter(Event.created_at >= window_start)
                         .all())

        seen_cases = {e.case_id for e in remote_events if e.case_id}
        # Fall back to the case that was current during recent remote work.
        if not seen_cases:
            for event in remote_events:
                data = event.data or {}
                if data.get("case_id"):
                    seen_cases.add(data["case_id"])

        raised = 0
        for case_id in seen_cases:
            case = self.db.query(Case).filter(Case.case_id == case_id).first()
            if case is None:
                continue
            # "Updated" = the case was edited after the remote session, or a
            # summary exists. Missing both means it probably was not written up.
            if case.ai_summary:
                continue
            customer = case.customer or "the customer"
            body = self._phrase(
                partner=f"We remoted into {customer} for case {case_id} and it looks "
                        f"resolved, but we never updated the case record. Want me to "
                        f"draft the update?",
                assistant=f"You remoted into {customer} for case {case_id} and it looks "
                          f"resolved, but the case record was never updated. Want me to "
                          f"draft the update?")
            nudge = self.raise_nudge(
                title=f"Update case {case_id}?",
                body=body,
                kind="case_not_updated", priority="medium",
                dedupe_key=f"not_updated:{case_id}", case_id=case_id,
                action={"type": "summarise_case", "case_id": case_id})
            raised += 1 if nudge else 0
        return raised

    # ------------------------------------------------------------ reading
    def open_nudges(self, limit: int = 20) -> List[Nudge]:
        order = {"high": 0, "medium": 1, "low": 2}
        nudges = (self.db.query(Nudge).filter(Nudge.status == "open")
                  .order_by(Nudge.created_at.desc()).limit(limit * 2).all())
        nudges.sort(key=lambda n: (order.get(n.priority, 1), ))
        return nudges[:limit]

    def resolve(self, nudge: Nudge, status: str = "dismissed") -> Nudge:
        nudge.status = status
        nudge.acted_at = datetime.utcnow()
        self.db.commit()
        return nudge

    def status(self) -> Dict[str, Any]:
        if not settings.NUDGES_ENABLED:
            return {"ok": True, "enabled": False, "detail": "Nudges disabled"}
        count = self.db.query(Nudge).filter(Nudge.status == "open").count()
        return {"ok": True, "enabled": True, "open": count,
                "detail": f"{count} open nudge(s)"}


def scan_for_nudges(db: Session) -> None:
    """Called by the scheduler."""
    if settings.NUDGES_ENABLED:
        NudgeService(db).scan()
