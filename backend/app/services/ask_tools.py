"""Deterministic Ask actions: approvals, and the no-AI fallback.

With an AI provider connected, Ask's tools live in ``app.services.agent`` and
the model chooses them. This module keeps what must not depend on a model:

* approving or discarding a draft card (typed or clicked), and
* keyword routing for installs with no AI provider, where recognising
  "summarise my inbox" by phrase is still better than nothing.

Read operations run immediately. Anything that leaves the computer is created
as a draft and requires an explicit user approval before it reaches the Power
Automate outbox.
"""

import re
from typing import Any, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.enterprise import EnterpriseAction, EnterpriseMessage
from app.models.tracked_document import TrackedDocument
from app.services.enterprise_service import EnterpriseService



APPROVE_RE = re.compile(
    r"^(?:yes[,. ]*)?(?:approve|send|send it|send this|send the draft|go ahead|do it)[.! ]*$",
    re.IGNORECASE,
)
DISCARD_RE = re.compile(
    r"^(?:discard|cancel|delete)(?: it| this| the draft)?[.! ]*$", re.IGNORECASE)
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
MESSAGE_ID_RE = re.compile(r"(?:message|email|item)\s*#?\s*(\d+)\b", re.IGNORECASE)


def _progress(title: str, detail: str = None, status: str = "complete") -> dict:
    return {"type": "progress", "status": status, "title": title, "detail": detail}


def _completion(title: str, detail: str = None, status: str = "complete") -> dict:
    return {"type": "completion", "status": status, "title": title, "detail": detail}


def _draft_card(action: EnterpriseAction) -> dict:
    target = action.chat_or_channel or ", ".join(action.to_dict().get("to") or [])
    from app.services.enterprise_service import browser_transport

    status = "awaiting_approval" if action.status == "draft" else action.status
    return {
        "type": "draft", "status": status, "source": action.source,
        "via": "browser" if browser_transport(action.source) is not None else "power_automate",
        "detail_status": action.status_detail,
        "title": "Email draft" if action.source == "outlook" else "Teams draft",
        "detail": f"To {target}" if target else "Destination needs review",
        "action_id": action.id, "action": action.action,
        "to": action.to_dict().get("to") or [],
        "chat_or_channel": action.chat_or_channel,
        "subject": action.subject, "body": action.body,
        "approve_label": "Approve and send", "discard_label": "Discard",
    }


class AskToolService:
    """Select and execute high-confidence Ask tools."""

    def __init__(self, db: Session):
        self.db = db
        self.enterprise = EnterpriseService(db)

    @staticmethod
    def catalog() -> List[dict]:
        """Every tool Ask can use, with the safety mode applied to each."""
        from app.services.agent import catalog

        return catalog()

    def try_decision(self, text: str) -> Optional[dict]:
        """Approve or discard the newest pending draft when the message says so.

        Drafts are either outbound mail/Teams messages or changes to an
        external system (Dynamics, RightAnswers); the newest of either kind
        is the one on screen.
        """
        text = (text or "").strip()
        approving, discarding = APPROVE_RE.match(text), DISCARD_RE.match(text)
        if not approving and not discarding:
            return None
        from app.services.agent import actions

        message_draft = self.pending_action()
        change = actions.pending(self.db)
        if change and (message_draft is None or
                       (change.created_at or 0, change.id) >= (message_draft.created_at or 0, 0)):
            return (actions.approve(self.db, change.id) if approving
                    else actions.discard(self.db, change.id))
        if message_draft:
            return self.approve(message_draft.id) if approving else self.discard(message_draft.id)
        return None

    def pending_action(self) -> Optional[EnterpriseAction]:
        """The newest draft waiting on the user — or a send that just failed,
        so "send it again" retries rather than being refused."""
        from datetime import datetime, timedelta

        recent = datetime.utcnow() - timedelta(hours=1)
        return (self.db.query(EnterpriseAction)
                .filter(or_(EnterpriseAction.status == "draft",
                            (EnterpriseAction.status == "failed") & (EnterpriseAction.updated_at > recent)))
                .order_by(EnterpriseAction.created_at.desc(), EnterpriseAction.id.desc())
                .first())

    def try_execute(self, text: str, context: Dict[str, Any] = None) -> Optional[dict]:
        """Return a tool result for a recognised request, or ``None``."""
        text = (text or "").strip()
        lowered = text.lower()
        context = context or {}

        decision = self.try_decision(text)
        if decision is not None:
            return decision

        if self._is_inbox_briefing(lowered):
            return self._inbox_briefing(text)

        if self._is_draft_reply(lowered):
            return self._draft_reply(text)

        if "remind" not in lowered and self._is_direct_email(lowered):
            return self._draft_email(text)

        if "remind" not in lowered and self._is_direct_teams(lowered):
            return self._draft_teams(text)

        if "sharepoint" in lowered and any(word in lowered for word in ("search", "find", "look")):
            return self._search_sharepoint(text)

        if self._is_document_request(lowered):
            result = self._read_document(text, context)
            if result:
                return result

        return None

    @staticmethod
    def _is_inbox_briefing(text: str) -> bool:
        phrases = (
            "inbox briefing", "summarize my inbox", "summarise my inbox",
            "what did i miss", "what's new in my inbox", "whats new in my inbox",
            "new emails", "unread emails", "urgent emails", "email briefing",
            "teams briefing", "new teams messages",
        )
        return any(phrase in text for phrase in phrases)

    @staticmethod
    def _is_draft_reply(text: str) -> bool:
        return ("draft" in text and any(word in text for word in ("reply", "response"))) or \
            text.startswith("reply to ")

    @staticmethod
    def _is_direct_email(text: str) -> bool:
        return bool(EMAIL_RE.search(text)) and any(
            phrase in text for phrase in ("email ", "send an email", "send email", "write to "))

    @staticmethod
    def _is_direct_teams(text: str) -> bool:
        return "teams" in text and any(
            phrase in text for phrase in ("post ", "send ", "message ", "write "))

    @staticmethod
    def _is_document_request(text: str) -> bool:
        return any(phrase in text for phrase in (
            "summarize this document", "summarise this document",
            "summarize the document", "summarise the document",
            "read this document", "read the current document",
            "summarize the latest document", "summarise the latest document",
        ))

    def _inbox_briefing(self, text: str) -> dict:
        hours = 24 if any(word in text.lower() for word in ("today", "day", "inbox")) else 12
        data = self.enterprise.briefing(hours=hours)
        lines = [
            f"I found {data['total']} message(s) in the last {hours} hours: "
            f"{data['outlook']} Outlook and {data['teams']} Teams. "
            f"{data['unhandled']} still need handling."
        ]
        if data["urgent"]:
            lines.append("\nUrgent:")
            for item in data["urgent"][:5]:
                who = item.get("sender_name") or item.get("sender") or "Unknown sender"
                lines.append(f"- {item.get('subject') or item.get('preview') or 'Message'} — {who}")
        if data["waiting"]:
            lines.append("\nWaiting for you:")
            for item in data["waiting"][:5]:
                lines.append(f"- {item.get('subject') or item.get('preview') or 'Message'}")
        if not data["total"]:
            lines.append("The bridge has not ingested anything in that window.")

        return {
            "reply": "\n".join(lines), "kind": "tool_result", "tool": "get_inbox_briefing",
            "cards": [
                _progress("Checked Power Automate inbox", f"Last {hours} hours"),
                _completion("Inbox briefing ready", f"{data['unhandled']} message(s) need attention"),
            ],
            "data": data,
        }

    def _find_message(self, text: str) -> Optional[EnterpriseMessage]:
        bare = (text or "").strip().lstrip("#").strip()
        if bare.isdigit():                       # "12" or "#12", as tools list them
            return self.db.query(EnterpriseMessage).get(int(bare))
        match = MESSAGE_ID_RE.search(text)
        if match:
            return self.db.query(EnterpriseMessage).get(int(match.group(1)))

        query = self.db.query(EnterpriseMessage)
        lowered = text.lower()
        tokens = [token for token in re.findall(r"[a-z0-9@._-]+", lowered)
                  if len(token) > 2 and token not in {
                      "draft", "reply", "response", "email", "message", "latest",
                      "last", "the", "from", "about", "please", "teams",
                  }]
        if tokens:
            conditions = []
            for token in tokens[:6]:
                like = f"%{token}%"
                conditions.extend((EnterpriseMessage.sender.ilike(like),
                                   EnterpriseMessage.sender_name.ilike(like),
                                   EnterpriseMessage.subject.ilike(like)))
            candidate = query.filter(or_(*conditions)).order_by(
                EnterpriseMessage.timestamp.desc().nullslast(),
                EnterpriseMessage.ingested_at.desc()).first()
            if candidate:
                return candidate

        return query.order_by(
            EnterpriseMessage.handled.asc(),
            EnterpriseMessage.timestamp.desc().nullslast(),
            EnterpriseMessage.ingested_at.desc()).first()

    def _draft_reply(self, text: str) -> dict:
        message = self._find_message(text)
        if not message:
            return {
                "reply": "I couldn't find an Outlook or Teams message to reply to. "
                         "Let the bridge sync, then ask for a draft by sender, subject, or message ID.",
                "kind": "tool_result", "tool": "draft_reply",
                "cards": [_completion("No message found", "Nothing was drafted", "warning")],
            }
        try:
            draft = self.enterprise.draft_reply(message, instruction=text)
        except RuntimeError as exc:
            return {
                "reply": str(exc), "kind": "tool_result", "tool": "draft_reply",
                "cards": [_completion("Drafting unavailable", str(exc), "warning")],
            }

        action_name = "reply_email" if message.source == "outlook" else "reply_teams_message"
        action = self.enterprise.create_action(
            action_name, body=draft["draft"], source=message.source,
            in_reply_to=message.id, to=draft.get("to"),
            chat_or_channel=draft.get("chat_or_channel"),
            thread_id=draft.get("thread_id"), subject=draft.get("subject"), send=False)
        return {
            "reply": "I drafted the reply. Review it below; nothing will be sent until you approve it.",
            "kind": "draft", "tool": "draft_reply", "action": action.to_dict(),
            "cards": [
                _progress("Read message thread", message.subject or message.sender or "Message"),
                _progress("Prepared reply", "Using your saved writing style"),
                _draft_card(action),
            ],
        }

    @staticmethod
    def _body_after_cue(text: str) -> str:
        for pattern in (r"\bsaying\s+(.+)$", r"\bmessage\s*:\s*(.+)$", r":\s*(.+)$"):
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()
        return ""

    def _draft_email(self, text: str) -> dict:
        recipient = EMAIL_RE.search(text)
        body = self._body_after_cue(text)
        if not recipient or not body:
            return {
                "reply": "Tell me the recipient and message, for example: "
                         "“Email alex@example.com: The deployment is complete.”",
                "kind": "clarification", "tool": "send_email",
                "cards": [_completion("Email needs details", "Recipient and message are required", "warning")],
            }
        action = self.enterprise.create_action(
            "send_email", body=body, source="outlook", to=[recipient.group(0)], send=False)
        return {
            "reply": "I prepared the email. Review it below; it has not been sent.",
            "kind": "draft", "tool": "send_email", "action": action.to_dict(),
            "cards": [_progress("Prepared Power Automate email"), _draft_card(action)],
        }

    def _draft_teams(self, text: str) -> dict:
        body = self._body_after_cue(text)
        channel_match = re.search(
            r"teams(?:\s+channel)?\s+([^:]+?)(?:\s+saying|\s+message\s*:|:)",
            text, re.IGNORECASE)
        channel = channel_match.group(1).strip() if channel_match else None
        if not channel or not body:
            return {
                "reply": "Tell me the Teams destination and message, for example: "
                         "“Post to Teams channel Support Escalations: The issue is resolved.”",
                "kind": "clarification", "tool": "send_teams_message",
                "cards": [_completion("Teams post needs details", "Channel and message are required", "warning")],
            }
        action = self.enterprise.create_action(
            "send_teams_message", body=body, source="teams",
            chat_or_channel=channel, send=False)
        return {
            "reply": "I prepared the Teams post. Review it below; it has not been sent.",
            "kind": "draft", "tool": "send_teams_message", "action": action.to_dict(),
            "cards": [_progress("Prepared Power Automate Teams post"), _draft_card(action)],
        }

    def _search_sharepoint(self, text: str) -> dict:
        from app.services.sharepoint_service import SharePointService

        query = re.sub(r"^.*?sharepoint(?:\s+for)?\s*", "", text,
                       flags=re.IGNORECASE).strip(" ?.:")
        if len(query) < 2:
            return {
                "reply": "What should I search for in SharePoint?",
                "kind": "clarification", "tool": "search_sharepoint",
            }
        try:
            results = SharePointService().search(query, limit=8)
        except Exception as exc:  # connection state must be actionable in chat
            return {
                "reply": f"I couldn't search SharePoint: {exc}",
                "kind": "tool_result", "tool": "search_sharepoint",
                "cards": [_completion("SharePoint search failed", str(exc), "error")],
            }
        sources = [{
            "ref": f"SP{index}", "title": item.get("name") or item.get("title") or "SharePoint item",
            "kind": "sharepoint", "uri": item.get("web_url") or item.get("webUrl"),
            "locator": item.get("path") or "SharePoint",
            "excerpt": item.get("description") or item.get("text") or "",
        } for index, item in enumerate(results, start=1)]
        reply = (f"I found {len(results)} SharePoint item(s) for “{query}”." if results
                 else f"I didn't find any SharePoint items for “{query}”.")
        return {
            "reply": reply, "kind": "tool_result", "tool": "search_sharepoint",
            "sources": sources,
            "cards": [_progress("Searched SharePoint", query),
                      _completion("Search complete", f"{len(results)} result(s)")],
        }

    def _read_document(self, text: str, context: Dict[str, Any]) -> Optional[dict]:
        from app.services.document_service import DocumentService
        from app.services.document_readers import DocumentError

        active = str(context.get("active_document") or "")
        query = self.db.query(TrackedDocument)
        record = None
        if active:
            record = query.filter(or_(TrackedDocument.path == active,
                                      TrackedDocument.name == active)).first()
        record = record or query.order_by(TrackedDocument.last_seen.desc()).first()
        if not record:
            return None
        try:
            answer = DocumentService(self.db).summarise(record)
        except DocumentError as exc:
            return {
                "reply": f"I couldn't read {record.name}: {exc}",
                "kind": "tool_result", "tool": "read_document",
                "cards": [_completion("Document read failed", str(exc), "error")],
            }
        return {
            "reply": answer, "kind": "tool_result", "tool": "read_document",
            "cards": [_progress("Read document", record.name),
                      _completion("Summary ready", record.name)],
        }

    def approve(self, action_id: int, destination: str = None) -> dict:
        action = self.db.query(EnterpriseAction).get(action_id)
        if not action:
            return {"reply": "That draft no longer exists.", "kind": "completion",
                    "cards": [_completion("Draft not found", status="error")]}
        if action.status == "failed":
            # A failed send can be tried again — usually with a corrected destination.
            action.status, action.status_detail = "draft", None
        if destination and action.status == "draft":
            emails = EMAIL_RE.findall(destination)
            action.to = ",".join(emails)
            action.chat_or_channel = destination.strip()
            action.thread_id = None
            self.db.commit()
        if action.status == "queued":
            return {"reply": "That action is already queued for Power Automate.",
                    "kind": "completion", "action": action.to_dict(),
                    "cards": [_completion("Already queued", action.outbox_file)]}
        if action.status != "draft":
            return {"reply": f"That action cannot be approved because it is {action.status}.",
                    "kind": "completion", "action": action.to_dict(),
                    "cards": [_completion("Action not sent", action.status_detail, "error")]}

        action = self.enterprise.dispatch_action(action)
        if action.status == "sent":
            app = "Outlook" if action.source == "outlook" else "Teams"
            return {"reply": f"Sent — {action.status_detail or app}.", "kind": "completion",
                    "tool": action.action, "action": action.to_dict(), "notify": True,
                    "cards": [_progress("Approval recorded"),
                              _completion(f"Sent through {app}", action.status_detail)]}
        if action.status == "draft" and action.status_detail:
            # Not signed in to Outlook/Teams: the draft waits for another try.
            return {"reply": action.status_detail, "kind": "completion",
                    "action": action.to_dict(),
                    "cards": [{"type": "signin", "integration": action.source,
                               "title": f"Sign in to {'Outlook' if action.source == 'outlook' else 'Teams'}",
                               "detail": "Then approve the draft again."}]}
        if action.status == "failed":
            return {"reply": f"I couldn't send that: {action.status_detail}",
                    "kind": "completion", "action": action.to_dict(),
                    "cards": [_completion("Not sent", action.status_detail, "error")]}
        return {
            "reply": "Approved and queued for Power Automate.",
            "kind": "completion", "tool": action.action, "action": action.to_dict(),
            "cards": [_progress("Approval recorded"),
                      _completion("Queued for Power Automate",
                                  action.subject or action.chat_or_channel or action.action)],
            "notify": True,
        }

    def discard(self, action_id: int) -> dict:
        action = self.db.query(EnterpriseAction).get(action_id)
        if not action:
            return {"reply": "That draft no longer exists.", "kind": "completion"}
        if action.status not in ("draft", "failed"):
            return {"reply": "That action has already left the draft stage and cannot be discarded here.",
                    "kind": "completion", "action": action.to_dict(),
                    "cards": [_completion("Draft not discarded", action.status, "warning")]}
        self.db.delete(action)
        self.db.commit()
        return {
            "reply": "Draft discarded. Nothing was sent.", "kind": "completion",
            "cards": [_completion("Draft discarded", "Nothing was sent")],
        }
