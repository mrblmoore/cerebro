"""
The Power Automate bridge.

Power Automate owns Microsoft 365: it authenticates, subscribes to Outlook and
Teams, and writes one JSON file per event into a watched folder. Cerebro reads
that folder. Outbound works the same way in reverse — Cerebro writes an action
file, a second flow picks it up and sends the mail or Teams reply.

The folder is the whole integration surface. That keeps Cerebro local-first and
means it needs no Microsoft credentials, no registered app and no network access
to Microsoft 365 — which is what makes it deployable inside a locked-down
enterprise at all.
"""

import hashlib
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core import logger
from app.core.config import settings
from app.models.enterprise import EnterpriseAction, EnterpriseMessage

PREVIEW_LENGTH = 400

#: Words that mark a message as needing attention now. Deliberately short and
#: readable — this is a first-pass triage, not a classifier.
#:
#: Matched on word boundaries: a substring search rates "download the report" and
#: "shutdown window" as urgent, which floods the widget's inbox with noise and
#: makes the genuinely urgent ones invisible. Entries ending in "*" match a
#: prefix, so "escalat*" still catches escalate/escalated/escalating.
URGENT_PHRASES = (
    "urgent", "asap", "escalat*", "outage", "down", "critical", "p1", "sev1",
    "immediately", "breach", "blocker", "emergency",
)
SOON_PHRASES = (
    "today", "eod", "end of day", "deadline", "waiting on", "follow up",
    "reminder", "overdue", "please advise",
)


def _phrase_pattern(phrase: str) -> "re.Pattern":
    """Word-boundary matcher for a phrase, honouring a trailing ``*``."""
    if phrase.endswith("*"):
        return re.compile(rf"\b{re.escape(phrase[:-1])}\w*", re.IGNORECASE)
    return re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)


_URGENT_PATTERNS = tuple((p.rstrip("*"), _phrase_pattern(p)) for p in URGENT_PHRASES)
_SOON_PATTERNS = tuple((p.rstrip("*"), _phrase_pattern(p)) for p in SOON_PHRASES)

#: Case-number shapes Cerebro recognises in message text, most specific first.
#: The labelled forms ("case 12345") require the reference to contain a digit,
#: so ordinary English — "in case of", "case sensitive" — is not mistaken for a
#: case number.
CASE_PATTERNS = (
    re.compile(r"\b(5\d{2}[A-Za-z0-9]{12,15})\b"),                 # Salesforce 15/18-char
    re.compile(r"\b((?:INC|CS|RITM|SCTASK|CHG)\d{5,})\b", re.I),    # ServiceNow
    re.compile(r"\b(?:case|ticket|incident|ref)\s*(?:number|no\.?|#)?\s*[:#]?\s*"
               r"([A-Za-z0-9][A-Za-z0-9-]{2,17})\b", re.I),
)

#: A labelled reference must look like an identifier, not a word.
_HAS_DIGIT = re.compile(r"\d")

HTML_TAG = re.compile(r"<[^>]+>")
WHITESPACE = re.compile(r"[ \t]*\n[ \t]*")


# --------------------------------------------------------------- normalising
def _strip_html(text: str) -> str:
    """Outlook bodies often arrive as HTML; keep the words, drop the markup."""
    if not text or "<" not in text:
        return text or ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", text)
    text = HTML_TAG.sub(" ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
                .replace("&#39;", "'"))
    text = re.sub(r"[ \t]{2,}", " ", text)
    return WHITESPACE.sub("\n", text).strip()


def parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    text = str(value).strip()
    for candidate in (text.replace("Z", "+00:00"), text):
        try:
            parsed = datetime.fromisoformat(candidate)
            # Store naive UTC so comparisons against func.now() behave.
            if parsed.tzinfo:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        except ValueError:
            continue
    for pattern in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], pattern)
        except ValueError:
            continue
    return None


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[;,]", value) if part.strip()]
    if isinstance(value, (list, tuple)):
        results = []
        for item in value:
            if isinstance(item, dict):
                # Graph-shaped recipients: {"emailAddress": {"address": "..."}}
                address = (item.get("emailAddress") or {}).get("address") or \
                    item.get("address") or item.get("email") or item.get("name")
                if address:
                    results.append(str(address).strip())
            elif item:
                results.append(str(item).strip())
        return results
    return [str(value).strip()]


def _first(payload: Dict[str, Any], *keys: str) -> Optional[Any]:
    """Take the first present key — Power Automate field names vary by flow."""
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def detect_case(*texts: str) -> Optional[str]:
    """Find a case or ticket reference in message text, or None."""
    for text in texts:
        if not text:
            continue
        for pattern in CASE_PATTERNS:
            for match in pattern.finditer(text):
                candidate = match.group(1)
                if _HAS_DIGIT.search(candidate):
                    return candidate.upper()
    return None


def assess_urgency(subject: str, body: str, importance: str) -> Tuple[str, str]:
    """
    Rate how much a message needs attention, and say why.

    The reason matters as much as the rating: the widget shows it, and an
    unexplained "high" is not something an engineer can act on or trust.
    """
    haystack = f"{subject or ''}\n{body or ''}"

    for label, pattern in _URGENT_PATTERNS:
        if pattern.search(haystack):
            return "high", f"mentions “{label}”"

    if (importance or "").lower() == "high":
        return "high", "flagged high importance by the sender"

    for label, pattern in _SOON_PATTERNS:
        if pattern.search(haystack):
            return "medium", f"mentions “{label}”"

    if "?" in (subject or "") or "?" in (body or "")[:600]:
        return "medium", "asks a direct question"

    return "normal", ""


def normalise(payload: Dict[str, Any], source_file: str = None) -> Dict[str, Any]:
    """
    Turn one Power Automate payload into Cerebro's message shape.

    Flows get authored by hand and field names drift — ``body`` vs ``bodyPreview``
    vs ``content``, ``from`` vs ``sender``. Accepting the common aliases means a
    slightly different flow still works instead of silently ingesting blanks.
    """
    source = str(_first(payload, "source", "system") or "unknown").lower()
    message_type = str(_first(payload, "type", "eventType", "event_type")
                       or ("email" if source == "outlook" else "message")).lower()

    sender_raw = _first(payload, "sender", "from", "sender_email", "fromAddress")
    sender_name = _first(payload, "sender_name", "senderName", "fromName")
    if isinstance(sender_raw, dict):
        # Graph nests the pair: {"emailAddress": {"address": ..., "name": ...}}.
        # Flatter shapes put both at the top level.
        address_block = sender_raw.get("emailAddress")
        address_block = address_block if isinstance(address_block, dict) else {}
        sender_name = (sender_name or address_block.get("name")
                       or sender_raw.get("name") or sender_raw.get("displayName"))
        sender_raw = (address_block.get("address") or sender_raw.get("address")
                      or sender_raw.get("email") or sender_raw.get("upn"))

    body = _strip_html(str(_first(payload, "body", "text", "content",
                                  "bodyPreview", "message") or ""))
    subject = str(_first(payload, "subject", "title", "topic") or "").strip()

    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {"value": metadata}

    importance = str(_first(metadata, "importance", "priority")
                     or _first(payload, "importance", "priority") or "normal").lower()

    urgency, reason = assess_urgency(subject, body, importance)
    case_id = (_first(payload, "case_id", "caseId")
               or _first(metadata, "case_id", "caseId")
               or detect_case(subject, body))

    thread_id = _first(payload, "thread_id", "threadId", "conversationId",
                       "conversation_id", "replyToId")

    return {
        "source": source,
        "type": message_type,
        "sender": str(sender_raw).strip() if sender_raw else None,
        "sender_name": str(sender_name).strip() if sender_name else None,
        "recipients": ",".join(_as_list(_first(payload, "recipients", "to", "toRecipients"))),
        "chat_or_channel": _first(payload, "chat_or_channel", "channel", "chat",
                                  "channelName", "teamName"),
        "subject": subject or None,
        "body": body or None,
        "preview": (body or subject or "")[:PREVIEW_LENGTH] or None,
        "thread_id": str(thread_id) if thread_id else None,
        "external_id": _external_id(payload, source, source_file),
        "timestamp": parse_timestamp(_first(payload, "timestamp", "receivedDateTime",
                                            "createdDateTime", "sent", "date")),
        "importance": importance,
        "urgency": urgency,
        "urgency_reason": reason,
        "case_id": case_id,
        "customer": _first(payload, "customer") or _first(metadata, "customer"),
        "raw": json.dumps(payload, default=str)[:100_000],
        "source_file": source_file,
    }


def _external_id(payload: Dict[str, Any], source: str, source_file: str) -> str:
    """
    A stable identity for a message, so re-runs cannot duplicate it.

    Power Automate re-runs a flow after a transient failure, and a user copying
    files back into the folder is a normal way to replay a day. Preferring the
    provider's own id and falling back to a content hash makes both safe.
    """
    provided = _first(payload, "external_id", "id", "messageId", "message_id",
                      "internetMessageId")
    if provided:
        return f"{source}:{provided}"

    fingerprint = json.dumps({
        "source": source,
        "sender": _first(payload, "sender", "from"),
        "subject": _first(payload, "subject", "title"),
        "body": _first(payload, "body", "text", "content"),
        "timestamp": _first(payload, "timestamp", "receivedDateTime"),
    }, sort_keys=True, default=str)
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:32]
    return f"{source}:sha:{digest}"


# ------------------------------------------------------------------ service
class EnterpriseService:
    def __init__(self, db: Session):
        self.db = db

    # ----------------------------------------------------------- inbound
    def ingest_payload(self, payload: Dict[str, Any],
                       source_file: str = None) -> Dict[str, Any]:
        """Normalise and store one message. Returns what happened."""
        fields = normalise(payload, source_file)

        existing = self.db.query(EnterpriseMessage).filter(
            EnterpriseMessage.external_id == fields["external_id"]).first()
        if existing:
            return {"status": "duplicate", "id": existing.id,
                    "message": existing.to_dict()}

        message = EnterpriseMessage(**fields)
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)

        logger.info("enterprise", "Ingested message", {
            "source": message.source, "urgency": message.urgency,
            "case_id": message.case_id, "file": source_file,
        })
        return {"status": "ingested", "id": message.id, "message": message.to_dict()}

    def ingest_file(self, path: Path) -> Dict[str, Any]:
        """Read one JSON file from the inbox folder and ingest its contents."""
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            return {"status": "error", "file": path.name, "detail": str(exc)}

        try:
            payload = json.loads(text)
        except ValueError as exc:
            return {"status": "invalid", "file": path.name, "detail": f"Not valid JSON: {exc}"}

        # A flow that batches events writes a list; accept either shape.
        payloads = payload if isinstance(payload, list) else [payload]
        results = []
        for item in payloads:
            if isinstance(item, dict):
                results.append(self.ingest_payload(item, source_file=path.name))
            else:
                results.append({"status": "invalid", "file": path.name,
                                "detail": "Expected a JSON object"})

        if not results:
            # A flow that found nothing writes "[]". That is a successful empty
            # batch, not a failure — treat it as handled so the file is archived
            # rather than retried on every sweep forever.
            return {"status": "empty", "file": path.name, "count": 0, "results": []}

        statuses = [r["status"] for r in results]
        return {
            "status": "ingested" if "ingested" in statuses else statuses[0],
            "file": path.name,
            "count": len(results),
            "results": results,
        }

    # ---------------------------------------------------------- querying
    def list_messages(self, source: str = None, urgency: str = None,
                      case_id: str = None, unhandled_only: bool = False,
                      limit: int = 50) -> List[EnterpriseMessage]:
        query = self.db.query(EnterpriseMessage)
        if source:
            query = query.filter(EnterpriseMessage.source == source)
        if urgency:
            query = query.filter(EnterpriseMessage.urgency == urgency)
        if case_id:
            query = query.filter(EnterpriseMessage.case_id == case_id)
        if unhandled_only:
            query = query.filter(EnterpriseMessage.handled.is_(False))
        return (query.order_by(EnterpriseMessage.timestamp.desc().nullslast(),
                               EnterpriseMessage.ingested_at.desc())
                .limit(limit).all())

    def thread(self, thread_id: str, limit: int = 50) -> List[EnterpriseMessage]:
        return (self.db.query(EnterpriseMessage)
                .filter(EnterpriseMessage.thread_id == thread_id)
                .order_by(EnterpriseMessage.timestamp.asc().nullsfirst())
                .limit(limit).all())

    def briefing(self, hours: int = 12) -> Dict[str, Any]:
        """
        "What changed since this morning" — the question the bridge exists for.

        Grouped rather than listed: an engineer coming back from a call wants to
        know what is on fire and who is waiting, not to re-read their inbox.
        """
        since = datetime.utcnow() - timedelta(hours=hours)
        messages = (self.db.query(EnterpriseMessage)
                    .filter(EnterpriseMessage.ingested_at >= since)
                    .order_by(EnterpriseMessage.ingested_at.desc()).all())

        urgent = [m for m in messages if m.urgency == "high"]
        waiting = [m for m in messages if m.urgency == "medium" and not m.handled]
        by_case: Dict[str, int] = {}
        for message in messages:
            if message.case_id:
                by_case[message.case_id] = by_case.get(message.case_id, 0) + 1

        return {
            "since": since.isoformat(),
            "hours": hours,
            "total": len(messages),
            "outlook": sum(1 for m in messages if m.source == "outlook"),
            "teams": sum(1 for m in messages if m.source == "teams"),
            "unhandled": sum(1 for m in messages if not m.handled),
            "urgent": [m.to_dict() for m in urgent[:10]],
            "waiting": [m.to_dict() for m in waiting[:10]],
            "cases_mentioned": [{"case_id": case, "messages": count}
                                for case, count in sorted(by_case.items(),
                                                          key=lambda kv: -kv[1])[:10]],
        }

    # --------------------------------------------------------- outbound
    def create_action(self, action: str, body: str, source: str = None,
                      in_reply_to: int = None, to: List[str] = None,
                      chat_or_channel: str = None, thread_id: str = None,
                      subject: str = None, send: bool = False) -> EnterpriseAction:
        """
        Record an outbound action, optionally writing it straight to the outbox.

        Drafts stay in the database until someone approves them: writing to the
        outbox *is* sending, because a Power Automate flow is watching that
        folder, so it must take a deliberate act.
        """
        original = None
        if in_reply_to:
            original = self.db.query(EnterpriseMessage).get(in_reply_to)

        if original is not None:
            source = source or original.source
            thread_id = thread_id or original.thread_id
            chat_or_channel = chat_or_channel or original.chat_or_channel
            if not to and original.sender:
                to = [original.sender]
            if not subject and original.subject:
                subject = (original.subject if original.subject.lower().startswith("re:")
                           else f"Re: {original.subject}")

        record = EnterpriseAction(
            action=action,
            source=source or "outlook",
            in_reply_to=in_reply_to,
            to=",".join(to or []),
            chat_or_channel=chat_or_channel,
            thread_id=thread_id,
            subject=subject,
            body=body,
            status="draft",
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)

        # An approved outbound reply is a sample of how the user writes.
        try:
            from app.services.style_service import capture_user_writing

            capture_user_writing(self.db, body,
                                 channel="teams" if (source or "").startswith("teams") else "email")
        except Exception:
            pass

        if send:
            self.dispatch_action(record)
        return record

    def dispatch_action(self, record: EnterpriseAction) -> EnterpriseAction:
        """Write the action to the outbox folder for Power Automate to collect."""
        outbox = outbox_dir()
        if outbox is None:
            record.status = "failed"
            record.status_detail = ("No outbox folder configured — set it in "
                                    "Settings → Enterprise Bridge.")
            self.db.commit()
            return record

        try:
            outbox.mkdir(parents=True, exist_ok=True)
            stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            filename = f"{record.source}-{record.action}-{stamp}-{record.id}.json"
            path = outbox / filename

            payload = {
                "action": record.action,
                "source": record.source,
                "to": [r for r in (record.to or "").split(",") if r],
                "chat_or_channel": record.chat_or_channel,
                "thread_id": record.thread_id,
                "subject": record.subject,
                "body": record.body,
                "cerebro_action_id": record.id,
                "created_at": datetime.utcnow().isoformat() + "Z",
            }
            # Write beside the target then rename: a flow polling the folder must
            # never see a half-written file.
            temporary = path.with_suffix(".json.part")
            temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            temporary.replace(path)

            record.status = "queued"
            record.outbox_file = str(path)
            record.status_detail = None
            logger.info("enterprise", "Queued outbound action",
                        {"action": record.action, "file": filename})
        except OSError as exc:
            record.status = "failed"
            record.status_detail = str(exc)
            logger.error("enterprise", "Could not write outbound action", {"error": str(exc)})

        self.db.commit()
        self.db.refresh(record)
        return record


# ------------------------------------------------------------------ folders
def _resolve(raw: str) -> Optional[Path]:
    if not raw:
        return None
    return Path(raw).expanduser()


def inbox_dir() -> Optional[Path]:
    return _resolve(settings.ENTERPRISE_INBOX_DIR)


def outbox_dir() -> Optional[Path]:
    return _resolve(settings.ENTERPRISE_OUTBOX_DIR)


def archive_dir() -> Optional[Path]:
    explicit = _resolve(settings.ENTERPRISE_ARCHIVE_DIR)
    if explicit:
        return explicit
    inbox = inbox_dir()
    return (inbox / "processed") if inbox else None


def drain_inbox(db: Session, limit: int = 200) -> Dict[str, Any]:
    """
    Ingest every JSON file waiting in the inbox, then move it to the archive.

    Files are only archived once they are safely in the database, so a crash
    mid-run replays rather than loses. Skipping files younger than a second
    avoids reading one Power Automate is still writing — the same reason the
    outbound path writes to a temporary name and renames.
    """
    inbox = inbox_dir()
    if inbox is None:
        return {"ok": False, "detail": "No inbox folder configured.", "ingested": 0}
    if not inbox.exists():
        return {"ok": False, "detail": f"Inbox folder does not exist: {inbox}",
                "ingested": 0}

    service = EnterpriseService(db)
    archive = archive_dir()
    now = datetime.now().timestamp()

    ingested = duplicates = failed = 0
    details: List[Dict[str, Any]] = []

    for path in sorted(inbox.glob("*.json"))[:limit]:
        try:
            if now - path.stat().st_mtime < 1.0:
                continue  # still being written
        except OSError:
            continue

        result = service.ingest_file(path)
        details.append(result)

        if result["status"] in ("ingested", "duplicate", "empty"):
            ingested += sum(1 for r in result.get("results", [])
                            if r.get("status") == "ingested")
            duplicates += sum(1 for r in result.get("results", [])
                              if r.get("status") == "duplicate")
            _archive(path, archive)
        else:
            failed += 1
            _archive(path, archive / "failed" if archive else None)

    return {"ok": True, "ingested": ingested, "duplicates": duplicates,
            "failed": failed, "files": len(details), "details": details[-20:]}


def _archive(path: Path, archive: Optional[Path]) -> None:
    """Move a processed file out of the way, keeping the folder small."""
    if archive is None:
        try:
            path.unlink()
        except OSError:
            pass
        return

    try:
        archive.mkdir(parents=True, exist_ok=True)
        target = archive / path.name
        if target.exists():
            stamp = datetime.utcnow().strftime("%H%M%S%f")
            target = archive / f"{path.stem}-{stamp}{path.suffix}"
        shutil.move(str(path), str(target))
    except OSError as exc:
        logger.warn("enterprise", "Could not archive file",
                    {"file": path.name, "error": str(exc)})


def status() -> Dict[str, Any]:
    """Folder health, shown in diagnostics and the settings screen."""
    inbox, outbox = inbox_dir(), outbox_dir()

    if not settings.ENTERPRISE_ENABLED:
        return {"ok": True, "enabled": False,
                "detail": "Outlook/Teams bridge disabled"}
    if inbox is None:
        return {"ok": False, "enabled": True,
                "detail": "Enabled but no inbox folder is configured"}

    if not inbox.exists():
        return {"ok": False, "enabled": True,
                "detail": f"Inbox folder not found: {inbox}"}

    pending = len(list(inbox.glob("*.json")))
    outbox_note = ""
    if outbox is None:
        outbox_note = " · no outbox configured (replies disabled)"
    elif not outbox.exists():
        outbox_note = f" · outbox will be created at {outbox}"

    return {
        "ok": True, "enabled": True,
        "inbox": str(inbox), "outbox": str(outbox) if outbox else None,
        "pending": pending,
        "detail": f"Watching {inbox} · {pending} file(s) waiting{outbox_note}",
    }
