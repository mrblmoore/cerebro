"""
The tools Ask can use.

Each one wraps a service Cerebro already has; this module only adapts them to
the shape the model sees (a name, a description, JSON arguments, and plain
text back). External writes never happen here — they are prepared as drafts
and only run when the user approves the card.
"""

import re
from typing import List

from app.core.config import settings
from app.services.agent.registry import ToolContext, bool_param, schema, string_param, tool
from app.services import text_chunks

#: How much of one tool result the model reads; long results are trimmed.
RESULT_CHARS = 6_000


def _min_score() -> float:
    return float(settings.ASK_MIN_SOURCE_SCORE or 0.0)


def _excerpt(text: str, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def _lines(ctx: ToolContext, items: List[dict], prefix: str) -> str:
    lines = []
    for item in items:
        ref = ctx.cite(item, prefix)
        where = item.get("locator") or item.get("kind") or "source"
        lines.append(f"[{ref}] {item.get('title') or 'Untitled'} ({where}): "
                     f"{_excerpt(item.get('excerpt'))}")
    return "\n".join(lines)


# --------------------------------------------------------------- knowledge
@tool("search_knowledge",
      "Search the indexed knowledge base (KB articles, runbooks, documents the "
      "user chose to index). Use for how-to, error codes and known fixes.",
      schema(["query"], query=string_param("What to search for — keywords, error codes, product names.")),
      label="Search knowledge base")
def search_knowledge(ctx: ToolContext, query: str = "", **_) -> dict:
    from app.services.rag_service import RAGService

    hits = RAGService(ctx.db).search(query, limit=5, min_score=_min_score())
    items = [{"title": hit.get("title"), "kind": "knowledge", "uri": hit.get("url"),
              "locator": hit.get("locator"), "excerpt": hit.get("excerpt", "")}
             for hit in hits]
    if not items:
        return {"content": f"No knowledge-base sections matched “{query}”.",
                "summary": "No matches"}
    return {"content": _lines(ctx, items, "K"), "summary": f"{len(items)} section(s)"}


@tool("search_sources",
      "Search what the user has open or recently viewed: open documents, "
      "captured web pages, recent messages. Use when the question is about "
      "something they are looking at.",
      schema(["query"], query=string_param("What to look for.")),
      label="Search open and recent sources")
def search_sources(ctx: ToolContext, query: str = "", **_) -> dict:
    from app.services.source_service import SourceService

    found = SourceService(ctx.db).context_for_query(query, limit=6, min_score=_min_score())
    if not found:
        return {"content": f"Nothing open or recently viewed is about “{query}”.",
                "summary": "No matches"}
    return {"content": _lines(ctx, found, "S"), "summary": f"{len(found)} excerpt(s)"}


@tool("search_local",
      "Search Cerebro's local records: support cases, Outlook/Teams messages "
      "and documents it has seen on this computer.",
      schema(["query"], query=string_param("Names, case numbers, subjects or keywords.")),
      label="Search local records")
def search_local(ctx: ToolContext, query: str = "", **_) -> dict:
    from app.models.case import Case
    from app.models.enterprise import EnterpriseMessage
    from app.models.tracked_document import TrackedDocument

    candidates = []
    for case in ctx.db.query(Case).order_by(Case.updated_at.desc()).limit(300):
        body = " ".join(filter(None, (case.case_id, case.customer, case.title,
                                      case.description, case.ai_summary,
                                      case.troubleshooting_steps)))
        candidates.append({"content": body, "title": f"Case {case.case_id} — {case.title or ''}",
                           "kind": "case", "locator": case.status or "case",
                           "uri": None})
    for message in (ctx.db.query(EnterpriseMessage)
                    .order_by(EnterpriseMessage.ingested_at.desc()).limit(300)):
        body = " ".join(filter(None, (message.sender_name, message.sender,
                                      message.subject, message.body)))
        candidates.append({"content": body,
                           "title": message.subject or f"{message.source} message #{message.id}",
                           "kind": message.source or "message",
                           "locator": f"message #{message.id}", "uri": None})
    for document in (ctx.db.query(TrackedDocument)
                     .order_by(TrackedDocument.last_seen.desc()).limit(200)):
        body = " ".join(filter(None, (document.name, document.summary,
                                      (document.text_preview or "")[:4000])))
        candidates.append({"content": body, "title": document.name, "kind": "document",
                           "locator": document.path, "uri": document.web_url})

    ranked = text_chunks.rank(query, candidates, limit=6, min_score=_min_score())
    items = [{**item, "excerpt": item["content"]} for item in ranked]
    if not items:
        return {"content": f"No local cases, messages or documents matched “{query}”.",
                "summary": "No matches"}
    return {"content": _lines(ctx, items, "L"), "summary": f"{len(items)} record(s)"}


@tool("recall_memory",
      "Recall what Cerebro remembers from earlier work: past resolutions, "
      "preferences, decisions.",
      schema(["query"], query=string_param("What to remember.")),
      label="Recall memory", available=lambda ctx: settings.MEMORY_ENABLED)
def recall_memory(ctx: ToolContext, query: str = "", **_) -> dict:
    from app.services.memory_service import MemoryService

    memories = [m for m in MemoryService(ctx.db).recall(
        query, case_id=ctx.context.get("crm_case"), customer=ctx.context.get("customer"))
        if (m.get("relevance") or 0) >= _min_score()]
    if not memories:
        return {"content": "Nothing relevant in memory.", "summary": "Nothing relevant"}
    items = [{"title": m.get("title") or "Memory", "kind": "memory",
              "locator": m.get("memory_type") or "memory", "excerpt": m.get("content", "")}
             for m in memories]
    return {"content": _lines(ctx, items, "M"), "summary": f"{len(items)} memory(ies)"}


# ------------------------------------------------------------------ context
@tool("get_current_context",
      "What the user is working on right now: open case, customer, active "
      "application, call state and the documents or pages in view.",
      label="Check current context", activity="searching")
def get_current_context(ctx: ToolContext, **_) -> dict:
    from app.services.source_service import SourceService

    context = ctx.context or {}
    lines = [
        f"Open case: {context.get('crm_case') or 'none'}"
        + (f" ({context.get('crm_system')})" if context.get("crm_system") else ""),
        f"Customer: {context.get('customer') or 'unknown'}",
        f"Active application: {context.get('active_application') or 'unknown'}",
        f"Window: {context.get('window_title') or 'unknown'}",
        f"On a call: {'yes' if context.get('call_active') else 'no'}",
    ]
    in_view = SourceService(ctx.db).in_view(limit=5)
    if in_view:
        lines.append("In view: " + "; ".join(f"{s.title} ({s.kind})" for s in in_view))
    return {"content": "\n".join(lines), "summary": context.get("crm_case") or "No open case"}


@tool("read_document",
      "Read a document the user has open or Cerebro has seen, and answer a "
      "question about it (or summarise it when no question is given).",
      schema([], name=string_param("Document name or path. Omit for the one in view."),
             question=string_param("What to find out from it.")),
      label="Read document", activity="searching")
def read_document(ctx: ToolContext, name: str = "", question: str = "", **_) -> dict:
    from sqlalchemy import or_

    from app.models.tracked_document import TrackedDocument
    from app.services.document_readers import DocumentError
    from app.services.document_service import DocumentService

    query = ctx.db.query(TrackedDocument)
    record = None
    target = (name or ctx.context.get("active_document") or "").strip()
    if target:
        record = query.filter(or_(TrackedDocument.path == target,
                                  TrackedDocument.name == target)).first()
        if record is None:
            record = (query.filter(or_(TrackedDocument.name.ilike(f"%{target}%"),
                                       TrackedDocument.path.ilike(f"%{target}%")))
                      .order_by(TrackedDocument.last_seen.desc()).first())
    if record is None and not name:
        record = query.order_by(TrackedDocument.last_seen.desc()).first()
    if record is None:
        return {"content": f"No document matching “{name or 'the current document'}” "
                           "has been seen on this computer.", "summary": "Not found"}
    try:
        answer = DocumentService(ctx.db).summarise(record, question=question or None)
    except DocumentError as exc:
        return {"content": f"Could not read {record.name}: {exc}", "summary": "Read failed"}
    ref = ctx.cite({"title": record.name, "kind": "document", "uri": record.web_url,
                    "locator": record.path, "excerpt": answer}, "D")
    return {"content": f"[{ref}] {record.name}:\n{answer}", "summary": record.name}


@tool("search_sharepoint",
      "Search SharePoint and OneDrive for files by name or content.",
      schema(["query"], query=string_param("What to search for.")),
      label="Search SharePoint",
      # The hidden-browser connector has its own, fuller SharePoint tools;
      # offering both would only make the model choose between duplicates.
      available=lambda ctx: settings.SHAREPOINT_GRAPH_ENABLED
      and not (settings.BROWSER_AUTOMATION_ENABLED and settings.SHAREPOINT_BROWSER_ENABLED))
def search_sharepoint(ctx: ToolContext, query: str = "", **_) -> dict:
    from app.services.sharepoint_service import SharePointService

    try:
        results = SharePointService().search(query, limit=8)
    except Exception as exc:  # noqa: BLE001 - connection state is reported to the model
        return {"content": f"SharePoint search failed: {exc}", "summary": "Failed"}
    items = [{"title": item.get("name") or item.get("title") or "SharePoint item",
              "kind": "sharepoint", "uri": item.get("web_url") or item.get("webUrl"),
              "locator": item.get("path") or "SharePoint",
              "excerpt": item.get("description") or item.get("text") or ""}
             for item in results]
    if not items:
        return {"content": f"No SharePoint items matched “{query}”.", "summary": "No matches"}
    return {"content": _lines(ctx, items, "SP"), "summary": f"{len(items)} item(s)"}


# ------------------------------------------------------------ Outlook/Teams
def _enterprise_on(ctx: ToolContext) -> bool:
    """Mail and Teams tools: through Power Automate or the browser connectors."""
    if settings.ENTERPRISE_ENABLED:
        return True
    from app.services.enterprise_service import browser_transport

    return browser_transport("outlook") is not None or browser_transport("teams") is not None


def _offer(ctx: ToolContext, action, original=None) -> str:
    """Show a draft card — or, for a reply the user lets Cerebro send on its
    own, send it now. Returns what the model should be told."""
    from app.services.ask_tools import _draft_card
    from app.services.enterprise_service import EnterpriseService, can_auto_send

    if can_auto_send(action, original):
        action = EnterpriseService(ctx.db).dispatch_action(action)
        card = _draft_card(action)
        card["automatic"] = action.status == "sent"
        ctx.drafts.append(card)
        if action.status == "sent":
            return (f"Sent automatically ({action.status_detail}) — the user has "
                    "“send replies without asking” on.")
        return f"Sending failed: {action.status_detail}. The draft is waiting for the user."
    ctx.drafts.append(_draft_card(action))
    return f"Draft #{action.id} prepared; it is NOT sent until the user approves it."


@tool("get_inbox_briefing",
      "Summarise recent Outlook and Teams messages: totals, urgent items and "
      "what is waiting for a reply.",
      schema([], hours={"type": "integer", "description": "Look-back window in hours (default 24)."}),
      label="Check Outlook and Teams", available=_enterprise_on)
def get_inbox_briefing(ctx: ToolContext, hours: int = 24, **_) -> dict:
    from app.services.enterprise_service import EnterpriseService

    hours = max(1, min(int(hours or 24), 24 * 14))
    data = EnterpriseService(ctx.db).briefing(hours=hours)
    lines = [f"{data['total']} message(s) in the last {hours}h "
             f"({data['outlook']} Outlook, {data['teams']} Teams); "
             f"{data['unhandled']} unhandled."]
    for label, key in (("Urgent", "urgent"), ("Waiting", "waiting")):
        for item in (data.get(key) or [])[:6]:
            lines.append(f"{label}: #{item.get('id')} {item.get('subject') or item.get('preview') or 'Message'}"
                         f" — {item.get('sender_name') or item.get('sender') or 'unknown'}")
    return {"content": "\n".join(lines), "summary": f"{data['unhandled']} need attention"}


@tool("get_message_thread",
      "Read one Outlook or Teams message in full, found by message number, "
      "sender or subject words.",
      schema(["query"], query=string_param("Message number (e.g. 'message 12'), sender or subject.")),
      label="Read message", available=_enterprise_on)
def get_message_thread(ctx: ToolContext, query: str = "", **_) -> dict:
    from app.services.ask_tools import AskToolService

    message = AskToolService(ctx.db)._find_message(query)
    if message is None:
        return {"content": "No matching message found.", "summary": "Not found"}
    body = message.body or message.preview or ""
    ref = ctx.cite({"title": message.subject or f"Message #{message.id}",
                    "kind": message.source or "message", "uri": None,
                    "locator": f"message #{message.id}", "excerpt": body}, "S")
    return {"content": (f"[{ref}] #{message.id} from {message.sender_name or message.sender} "
                        f"via {message.source}, subject “{message.subject or ''}”:\n{body[:4000]}"),
            "summary": message.subject or f"#{message.id}"}


@tool("draft_reply",
      "Draft a reply to an Outlook or Teams message in the user's writing "
      "style. Creates a draft for the user to approve; nothing is sent.",
      schema(["message"], message=string_param("Which message: number, sender or subject."),
             instruction=string_param("What the reply should say or do.")),
      mode="draft", label="Draft a reply", activity="writing", available=_enterprise_on)
def draft_reply(ctx: ToolContext, message: str = "", instruction: str = "", **_) -> dict:
    from app.services.ask_tools import AskToolService, _draft_card

    service = AskToolService(ctx.db)
    target = service._find_message(message)
    if target is None:
        return {"content": "No matching message to reply to.", "summary": "Not found"}
    try:
        draft = service.enterprise.draft_reply(target, instruction=instruction or None)
    except RuntimeError as exc:
        return {"content": f"Drafting failed: {exc}", "summary": "Failed"}
    action = service.enterprise.create_action(
        "reply_email" if target.source == "outlook" else "reply_teams_message",
        body=draft["draft"], source=target.source, in_reply_to=target.id,
        to=draft.get("to"), chat_or_channel=draft.get("chat_or_channel"),
        thread_id=draft.get("thread_id"), subject=draft.get("subject"), send=False)
    outcome = _offer(ctx, action, target)
    return {"content": f"{outcome}\nReply:\n{draft['draft']}",
            "summary": "Sent" if outcome.startswith("Sent") else "Draft ready for approval",
            "action": action.to_dict()}


@tool("reply_to_message",
      "Reply to an Outlook email or Teams message Cerebro has seen, with the reply text you "
      "wrote. It waits for the user's approval unless they let replies send without asking.",
      schema(["message", "body"], message=string_param("Which message: its number (e.g. 12), "
                                                       "sender or subject."),
             body=string_param("The reply, ready to send, in the user's voice."),
             reply_all=bool_param("Email only: reply to everyone on the thread.")),
      mode="approval", label="Prepare a reply", activity="writing", available=_enterprise_on)
def reply_to_message(ctx: ToolContext, message: str = "", body: str = "",
                     reply_all: bool = False, **_) -> dict:
    from app.services.ask_tools import AskToolService

    service = AskToolService(ctx.db)
    target = service._find_message(str(message))
    if target is None:
        return {"content": "No matching message to reply to.", "summary": "Not found"}
    if not body.strip():
        return {"content": "Write the reply first.", "summary": "Needs details"}
    if target.source == "outlook":
        kind = "reply_all_email" if reply_all else "reply_email"
    else:
        kind = "reply_teams_message"
    action = service.enterprise.create_action(kind, body=body.strip(), source=target.source,
                                              in_reply_to=target.id, send=False)
    outcome = _offer(ctx, action, target)
    return {"content": outcome, "summary": "Sent" if outcome.startswith("Sent") else
            "Waiting for approval", "action": action.to_dict()}


@tool("send_email",
      "Prepare an email for the user to approve. It is NOT sent until they "
      "approve the card.",
      schema(["to", "body"], to=string_param("Recipient email address(es), comma-separated."),
             subject=string_param("Subject line."), body=string_param("The email body.")),
      mode="approval", label="Prepare an email", activity="writing", available=_enterprise_on)
def send_email(ctx: ToolContext, to: str = "", subject: str = "", body: str = "", **_) -> dict:
    from app.services.ask_tools import EMAIL_RE, AskToolService, _draft_card

    recipients = EMAIL_RE.findall(to or "")
    if not recipients or not body.strip():
        return {"content": "An email needs at least one valid address and a body. "
                           "Ask the user for whichever is missing.", "summary": "Needs details"}
    service = AskToolService(ctx.db)
    action = service.enterprise.create_action(
        "send_email", body=body.strip(), source="outlook", to=recipients,
        subject=subject or None, send=False)
    return {"content": _offer(ctx, action), "summary": "Waiting for approval",
            "action": action.to_dict()}


@tool("send_teams_message",
      "Prepare a Teams message for the user to approve. It is NOT posted until "
      "they approve the card.",
      schema(["channel", "body"], channel=string_param("The chat's name as it appears in Teams, "
                                                       "or the person's email address."),
             body=string_param("The message.")),
      mode="approval", label="Prepare a Teams post", activity="writing", available=_enterprise_on)
def send_teams_message(ctx: ToolContext, channel: str = "", body: str = "", **_) -> dict:
    from app.services.ask_tools import AskToolService, _draft_card

    if not channel.strip() or not body.strip():
        return {"content": "A Teams post needs a destination and a message. "
                           "Ask the user for whichever is missing.", "summary": "Needs details"}
    from app.services.browser.teams import EMAIL_RE

    service = AskToolService(ctx.db)
    action = service.enterprise.create_action(
        "send_teams_message", body=body.strip(), source="teams",
        to=EMAIL_RE.findall(channel), chat_or_channel=channel.strip(), send=False)
    return {"content": _offer(ctx, action), "summary": "Waiting for approval",
            "action": action.to_dict()}


# -------------------------------------------------------------------- tasks
@tool("create_task",
      "Create a reminder or scheduled task from a plain-language instruction, "
      "e.g. 'every weekday at 9 summarise my inbox' or 'remind me at 3pm to "
      "call Contoso'. Only use when the user asks for something to happen "
      "later or repeatedly. Work that needs looking things up (cases, KB, "
      "SharePoint, inbox) runs with these same tools and posts its result "
      "back into this chat.",
      schema(["instruction"], instruction=string_param("The full instruction, including timing."),
             document=string_param("Document path or name, for document-update tasks.")),
      mode="write", label="Create a task", activity="writing",
      # A task that is running must not schedule itself again.
      available=lambda ctx: settings.TASKS_ENABLED and not ctx.context.get("task_run"))
def create_task(ctx: ToolContext, instruction: str = "", document: str = "", **_) -> dict:
    from app.services.task_service import TaskService, describe_confirmation

    if not instruction.strip():
        return {"content": "No instruction given.", "summary": "Needs details"}
    extra = {"document": document} if document else None
    task = TaskService(ctx.db).create_from_instruction(
        instruction, context=ctx.context, source="chat", extra_spec=extra,
        conversation_id=ctx.context.get("conversation_id"))
    confirmation = describe_confirmation(task)
    return {"content": f"Task #{task.id} created: {confirmation}",
            "summary": confirmation, "task": task.to_dict()}
