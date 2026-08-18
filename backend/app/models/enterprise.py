"""
Enterprise context — Outlook mail and Teams messages arriving from Power Automate.

Cerebro never talks to Microsoft 365 directly. Power Automate owns the auth and
the connectors, and hands work over as JSON files in a watched folder; these
tables are where those files land once normalised.
"""

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class EnterpriseMessage(Base):
    """One inbound Outlook email or Teams message."""

    __tablename__ = "enterprise_messages"

    id = Column(Integer, primary_key=True, index=True)

    #: "outlook" | "teams"
    source = Column(String, index=True)
    #: "email" | "message" | "mention" | "meeting" …
    type = Column(String, default="message")

    sender = Column(String, index=True)
    sender_name = Column(String, nullable=True)
    #: Comma-separated; Teams messages use ``chat_or_channel`` instead.
    recipients = Column(Text, nullable=True)
    chat_or_channel = Column(String, nullable=True)

    subject = Column(String, nullable=True)
    body = Column(Text, nullable=True)
    #: First ~400 characters, kept separate so lists never load whole bodies.
    preview = Column(String, nullable=True)

    thread_id = Column(String, index=True, nullable=True)
    #: Provider-side identifier. Used to reject the same message twice when a
    #: Power Automate flow re-runs or a file is copied back into the folder.
    external_id = Column(String, unique=True, index=True, nullable=True)

    timestamp = Column(DateTime, index=True, nullable=True)
    importance = Column(String, default="normal")   # low | normal | high
    #: Cerebro's own read of how much this needs attention.
    urgency = Column(String, default="normal", index=True)
    urgency_reason = Column(String, nullable=True)

    #: Case this message appears to belong to, matched from its text.
    case_id = Column(String, index=True, nullable=True)
    customer = Column(String, nullable=True)

    #: Raw payload as delivered, so nothing is lost in normalisation.
    raw = Column(Text, nullable=True)
    #: Name of the JSON file this came from, for tracing back to the flow.
    source_file = Column(String, nullable=True)

    handled = Column(Boolean, default=False, index=True)
    ingested_at = Column(DateTime, default=func.now(), index=True)

    def to_dict(self, include_body: bool = False) -> dict:
        payload = {
            "id": self.id,
            "source": self.source,
            "type": self.type,
            "sender": self.sender,
            "sender_name": self.sender_name,
            "recipients": [r for r in (self.recipients or "").split(",") if r],
            "chat_or_channel": self.chat_or_channel,
            "subject": self.subject,
            "preview": self.preview,
            "thread_id": self.thread_id,
            "external_id": self.external_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "importance": self.importance,
            "urgency": self.urgency,
            "urgency_reason": self.urgency_reason,
            "case_id": self.case_id,
            "customer": self.customer,
            "handled": bool(self.handled),
            "ingested_at": self.ingested_at.isoformat() if self.ingested_at else None,
        }
        if include_body:
            payload["body"] = self.body
        return payload

    def __repr__(self):
        return f"<EnterpriseMessage {self.source} {self.subject or self.preview!r}>"


class EnterpriseAction(Base):
    """
    An outbound action Cerebro wants Power Automate to perform.

    Written to the outbox folder as JSON; a second flow picks it up and sends
    the mail or Teams reply. The row is the record of what Cerebro asked for and
    whether it was picked up.
    """

    __tablename__ = "enterprise_actions"

    id = Column(Integer, primary_key=True, index=True)

    #: "send_email" | "reply_email" | "send_teams_message" | "reply_teams_message"
    action = Column(String, index=True)
    source = Column(String)                       # outlook | teams
    in_reply_to = Column(Integer, nullable=True)  # EnterpriseMessage.id

    to = Column(Text, nullable=True)              # comma-separated
    chat_or_channel = Column(String, nullable=True)
    thread_id = Column(String, nullable=True)
    subject = Column(String, nullable=True)
    body = Column(Text, nullable=True)

    #: draft   — written to disk, waiting on the user to approve
    #: queued  — file written to the outbox for Power Automate
    #: sent    — flow reported completion
    #: failed  — flow reported an error
    status = Column(String, default="draft", index=True)
    status_detail = Column(String, nullable=True)
    outbox_file = Column(String, nullable=True)

    created_at = Column(DateTime, default=func.now(), index=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "action": self.action,
            "source": self.source,
            "in_reply_to": self.in_reply_to,
            "to": [r for r in (self.to or "").split(",") if r],
            "chat_or_channel": self.chat_or_channel,
            "thread_id": self.thread_id,
            "subject": self.subject,
            "body": self.body,
            "status": self.status,
            "status_detail": self.status_detail,
            "outbox_file": self.outbox_file,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<EnterpriseAction {self.action} {self.status}>"
