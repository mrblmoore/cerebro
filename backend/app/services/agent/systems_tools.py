"""
Ask tools for BeyondTrust Remote Support and Genesys Cloud (read-only).

``link_call_to_remote_session`` ties the two together: given a Genesys
conversation it finds the BeyondTrust sessions that most likely belong to it.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.services.agent.registry import ToolContext, schema, string_param, tool
from app.services.systems import beyondtrust as bt
from app.services.systems import genesys as gc
from app.services.systems.base import NotConfigured, SystemCallError

LINK_WINDOW = timedelta(minutes=45)


def _beyondtrust_on(ctx: ToolContext) -> bool:
    return bt.connector.enabled and not bt.connector.missing()


def _genesys_on(ctx: ToolContext) -> bool:
    return gc.connector.enabled and not gc.connector.missing()


def _both_on(ctx: ToolContext) -> bool:
    return _beyondtrust_on(ctx) and _genesys_on(ctx)


def _guard(connector, fn) -> Dict[str, Any]:
    try:
        return fn()
    except (NotConfigured, SystemCallError) as exc:
        return {"content": str(exc), "summary": "Failed"}
    except Exception as exc:  # noqa: BLE001 - explained to the model and user
        return {"content": f"{connector.label} error: {exc}", "summary": "Failed"}


def _fmt(row: Dict[str, Any]) -> str:
    return " · ".join(f"{key} {value}" for key, value in row.items()
                      if value not in ("", None, []))


def _when(text: Any) -> Optional[datetime]:
    """Parse the timestamps both systems use; naive ones are taken as UTC."""
    if not text:
        return None
    value = str(text).strip().replace("Z", "+00:00")
    for candidate in (value, value.replace(" ", "T")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _digits(text: Any) -> str:
    return "".join(ch for ch in str(text or "") if ch.isdigit())[-10:]


# ============================================================== BeyondTrust
@tool("beyondtrust_list_sessions",
      "List recent BeyondTrust Remote Support sessions (customer, representative, "
      "times, external key). Optionally only those mentioning some text, such as a "
      "customer name, a representative or a case number.",
      schema([], query=string_param("Text to look for in the session. Empty lists all."),
             days=string_param("How many days back, 1-31. Default 1.")),
      label="List remote support sessions", activity="browsing", available=_beyondtrust_on)
def beyondtrust_list_sessions(ctx: ToolContext, query: str = "", days: Any = 1, **_) -> dict:
    def run():
        rows = bt.connector.sessions(days=_int(days, 1), query=query)
        if not rows:
            return {"content": "No BeyondTrust sessions matched.", "summary": "No matches"}
        lines = [_fmt(bt.summary(row)) for row in rows]
        return {"content": "\n".join(lines), "summary": f"{len(rows)} session(s)"}

    return _guard(bt.connector, run)


@tool("beyondtrust_get_session",
      "Read one BeyondTrust Remote Support session in full, by its session ID (LSID).",
      schema(["session"], session=string_param("The session ID (lsid).")),
      label="Read remote support session", activity="browsing", available=_beyondtrust_on)
def beyondtrust_get_session(ctx: ToolContext, session: str = "", **_) -> dict:
    def run():
        row = bt.connector.session(session)
        text = json.dumps(row, indent=1, default=str)
        info = bt.summary(row)
        ref = ctx.cite({"title": f"Remote session {info['id'] or session}",
                        "kind": "beyondtrust", "uri": f"{bt.connector.display_address()}#{session}",
                        "locator": "BeyondTrust", "excerpt": text[:1000]}, "BT")
        return {"content": f"[{ref}]\n{text}", "summary": str(info["id"] or session),
                "max_chars": 12000}

    return _guard(bt.connector, run)


# ================================================================= Genesys
@tool("genesys_search_conversations",
      "List recent Genesys Cloud conversations (calls, chats, emails): customer, "
      "phone number, agent, queue and times. Optionally only those matching a phone "
      "number, name or text.",
      schema([], query=string_param("Phone number, name or other text. Empty lists all."),
             days=string_param("How many days back, 1-31. Default 1.")),
      label="Search Genesys conversations", activity="browsing", available=_genesys_on)
def genesys_search_conversations(ctx: ToolContext, query: str = "", days: Any = 1, **_) -> dict:
    def run():
        rows = gc.connector.search_conversations(days=_int(days, 1), query=query)
        if not rows:
            return {"content": "No Genesys conversations matched.", "summary": "No matches"}
        return {"content": "\n".join(_fmt(gc.summary(row)) for row in rows),
                "summary": f"{len(rows)} conversation(s)"}

    return _guard(gc.connector, run)


@tool("genesys_get_conversation",
      "Read one Genesys Cloud conversation in full by its conversation ID: "
      "participants, queues, hold and talk times, wrap-up codes.",
      schema(["conversation"], conversation=string_param("The conversation ID (UUID).")),
      label="Read Genesys conversation", activity="browsing", available=_genesys_on)
def genesys_get_conversation(ctx: ToolContext, conversation: str = "", **_) -> dict:
    def run():
        row = gc.connector.conversation(conversation)
        text = json.dumps(row, indent=1, default=str)
        ref = ctx.cite({"title": f"Genesys conversation {conversation}", "kind": "genesys",
                        "uri": f"https://apps.{gc.connector.display_address()}/#{conversation}",
                        "locator": "Genesys Cloud", "excerpt": text[:1000]}, "GC")
        return {"content": f"[{ref}]\n{text}", "summary": conversation, "max_chars": 12000}

    return _guard(gc.connector, run)


# =============================================================== the link
def match_sessions(conversation: Dict[str, Any], sessions: List[Dict[str, Any]]):
    """Rank BeyondTrust sessions against a Genesys conversation.

    Evidence, strongest first: the conversation ID in the session's external
    key; the same customer phone number; the same customer name; and a start
    time inside the conversation's window.
    """
    info = gc.summary(conversation)
    start, end = _when(info["start"]), _when(info["end"]) or _when(info["start"])
    phone, name = _digits(info["phone"]), (info["customer"] or "").strip().lower()
    scored = []
    for row in sessions:
        item, reasons, score = bt.summary(row), [], 0
        blob = json.dumps(row, default=str).lower()
        if info["id"] and str(info["id"]).lower() in blob:
            reasons.append("session carries the conversation ID"); score += 10
        if phone and phone in _digits(blob.replace(" ", "")):
            reasons.append("same phone number"); score += 4
        if name and len(name) > 3 and name in blob:
            reasons.append("same customer name"); score += 3
        began = _when(item["start"])
        if start and began and start - LINK_WINDOW <= began <= (end or start) + LINK_WINDOW:
            reasons.append("started around the call"); score += 2
        if score:
            scored.append((score, item, reasons))
    scored.sort(key=lambda entry: entry[0], reverse=True)
    return scored


@tool("link_call_to_remote_session",
      "Find the BeyondTrust remote support session(s) that belong to a Genesys Cloud "
      "conversation (call, chat or email), matched on conversation ID, phone number, "
      "customer name and time.",
      schema(["conversation"], conversation=string_param("The Genesys conversation ID.")),
      label="Link call to remote session", activity="browsing", available=_both_on)
def link_call_to_remote_session(ctx: ToolContext, conversation: str = "", **_) -> dict:
    def run():
        record = gc.connector.conversation(conversation)
        began = _when(record.get("conversationStart")) or datetime.now(timezone.utc)
        days = max(1, (datetime.now(timezone.utc) - began).days + 2)
        sessions = bt.connector.sessions(days=days, limit=500)
        ranked = match_sessions(record, sessions)
        call = _fmt(gc.summary(record))
        if not ranked:
            return {"content": f"Call: {call}\nNo BeyondTrust session matched it.",
                    "summary": "No linked session"}
        lines = [f"{_fmt(item)}  (confidence {score}; {', '.join(why)})"
                 for score, item, why in ranked[:5]]
        return {"content": f"Call: {call}\nPossible remote sessions:\n" + "\n".join(lines),
                "summary": f"{len(lines)} possible session(s)"}

    return _guard(gc.connector, run)


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
