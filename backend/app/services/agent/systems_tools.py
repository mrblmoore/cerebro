"""
Ask tools for BeyondTrust Remote Support and Genesys Cloud (read-only), run
through the hidden browser with the user's own sign-in.

``link_call_to_remote_session`` ties the two together: given a Genesys
conversation it looks for the BeyondTrust sessions that most likely belong to it.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.services.agent.integration_tools import _guard
from app.services.agent.registry import ToolContext, schema, string_param, tool
from app.services.browser import get

LINK_WINDOW = timedelta(minutes=45)


def _beyondtrust():
    return get("beyondtrust")


def _genesys():
    return get("genesys")


def _beyondtrust_on(ctx: ToolContext) -> bool:
    return _beyondtrust().enabled


def _genesys_on(ctx: ToolContext) -> bool:
    return _genesys().enabled


def _both_on(ctx: ToolContext) -> bool:
    return _beyondtrust().enabled and _genesys().enabled


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _fmt(row: Dict[str, Any]) -> str:
    return " · ".join(f"{key} {value}" for key, value in row.items()
                      if value not in ("", None, []))


def _when(text: Any) -> Optional[datetime]:
    if not text:
        return None
    value = str(text).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _digits(text: Any) -> str:
    return re.sub(r"\D", "", str(text or ""))


# ============================================================== BeyondTrust
@tool("beyondtrust_search",
      "Look up BeyondTrust Remote Support sessions in the console: rows from the "
      "sessions page matching a customer name, representative, session ID, case number "
      "or other text. Empty lists what the page shows.",
      schema([], query=string_param("Text to look for. Empty lists recent rows.")),
      label="Search remote support sessions", activity="browsing", available=_beyondtrust_on)
def beyondtrust_search(ctx: ToolContext, query: str = "", **_) -> dict:
    connector = _beyondtrust()

    def run():
        result = connector.search(query)
        rows = result["sessions"]
        if not rows:
            ref = ctx.cite({"title": "BeyondTrust console", "kind": "beyondtrust",
                            "uri": result["url"], "locator": "BeyondTrust",
                            "excerpt": result["text"][:800]}, "BT")
            return {"content": f"[{ref}] No session rows were recognised on the page "
                               f"(it may need a search_url or row selector). Page text:\n"
                               f"{result['text'][:4000]}", "summary": "No rows"}
        lines = [f"{r['id'] or '-'} · {r['text']}" + (f" · {r['url']}" if r["url"] else "")
                 for r in rows]
        ref = ctx.cite({"title": "BeyondTrust sessions", "kind": "beyondtrust",
                        "uri": result["url"], "locator": "BeyondTrust",
                        "excerpt": "\n".join(lines)[:1000]}, "BT")
        return {"content": f"[{ref}]\n" + "\n".join(lines), "summary": f"{len(rows)} session(s)",
                "max_chars": 12000}

    return _guard(ctx, connector, run)


@tool("beyondtrust_read_page",
      "Open a BeyondTrust console page or session/report link (on the user's BeyondTrust "
      "site) and read it.",
      schema(["url"], url=string_param("A BeyondTrust page address, or a path on the site.")),
      label="Read BeyondTrust page", activity="browsing", available=_beyondtrust_on)
def beyondtrust_read_page(ctx: ToolContext, url: str = "", **_) -> dict:
    connector = _beyondtrust()

    def run():
        page = connector.read_page(url)
        ref = ctx.cite({"title": page["title"] or "BeyondTrust page", "kind": "beyondtrust",
                        "uri": page["url"], "locator": "BeyondTrust",
                        "excerpt": page["text"][:1000]}, "BT")
        return {"content": f"[{ref}]\n{page['text']}", "summary": page["title"] or url,
                "max_chars": 12000}

    return _guard(ctx, connector, run)


# ================================================================= Genesys
@tool("genesys_search_conversations",
      "List recent Genesys Cloud conversations (calls, chats, emails): customer, phone "
      "number, agent, queue and times. Optionally only those matching a phone number, "
      "name or text.",
      schema([], query=string_param("Phone number, name or other text. Empty lists all."),
             days=string_param("How many days back, 1-31. Default 1.")),
      label="Search Genesys conversations", activity="browsing", available=_genesys_on)
def genesys_search_conversations(ctx: ToolContext, query: str = "", days: Any = 1, **_) -> dict:
    connector = _genesys()

    def run():
        rows = connector.search(query, days=_int(days, 1))
        if not rows:
            return {"content": "No Genesys conversations matched.", "summary": "No matches"}
        return {"content": "\n".join(_fmt(row) for row in rows),
                "summary": f"{len(rows)} conversation(s)", "max_chars": 12000}

    return _guard(ctx, connector, run)


@tool("genesys_get_conversation",
      "Read one Genesys Cloud conversation in full by its conversation ID: participants, "
      "queues, timings and wrap-up.",
      schema(["conversation"], conversation=string_param("The conversation ID.")),
      label="Read Genesys conversation", activity="browsing", available=_genesys_on)
def genesys_get_conversation(ctx: ToolContext, conversation: str = "", **_) -> dict:
    connector = _genesys()

    def run():
        record = connector.conversation(conversation)
        text = (json.dumps(record["raw"], indent=1, default=str) if record.get("raw")
                else record.get("text", ""))
        ref = ctx.cite({"title": f"Genesys conversation {conversation}", "kind": "genesys",
                        "uri": f"{connector.base_url}/directory/#/engage/admin/interactions/"
                               f"{conversation}",
                        "locator": "Genesys Cloud", "excerpt": text[:1000]}, "GC")
        return {"content": f"[{ref}]\n{text}", "summary": conversation, "max_chars": 12000}

    return _guard(ctx, connector, run)


# =============================================================== the link
def match_sessions(call: Dict[str, Any], sessions: List[Dict[str, Any]]):
    """Rank BeyondTrust session rows against a Genesys conversation summary.

    Evidence, strongest first: the conversation ID in the row; the same
    customer phone number; the same customer name; the same agent name.
    Returns ``[(score, row, reasons)]`` best first.
    """
    phone, name = _digits(call.get("phone"))[-10:], (call.get("customer") or "").strip().lower()
    agent = (call.get("agent") or "").strip().lower()
    scored = []
    for row in sessions:
        text = row.get("text", "").lower()
        reasons, score = [], 0
        if call.get("id") and str(call["id"]).lower() in text:
            reasons.append("row carries the conversation ID"); score += 10
        if len(phone) >= 7 and phone in _digits(text):
            reasons.append("same phone number"); score += 4
        if len(name) > 3 and name in text:
            reasons.append("same customer name"); score += 3
        if len(agent) > 3 and agent in text:
            reasons.append("same agent"); score += 1
        if score:
            scored.append((score, row, reasons))
    scored.sort(key=lambda entry: entry[0], reverse=True)
    return scored


@tool("link_call_to_remote_session",
      "Find the BeyondTrust remote support session(s) that belong to a Genesys Cloud "
      "conversation (call, chat or email), matched on conversation ID, phone number and "
      "customer name.",
      schema(["conversation"], conversation=string_param("The Genesys conversation ID.")),
      label="Link call to remote session", activity="browsing", available=_both_on)
def link_call_to_remote_session(ctx: ToolContext, conversation: str = "", **_) -> dict:
    genesys, beyondtrust = _genesys(), _beyondtrust()

    def run():
        record = genesys.conversation(conversation)
        call = {k: record.get(k) for k in ("id", "customer", "phone", "agent", "start")}
        call["id"] = call["id"] or conversation
        sessions: List[Dict[str, Any]] = []
        for key in (call["customer"], call["phone"], ""):
            if key or not sessions:
                sessions = beyondtrust.search(str(key or ""), limit=60)["sessions"]
            if sessions and key:
                break
        ranked = match_sessions(call, sessions)
        header = f"Call: {_fmt({k: v for k, v in call.items()})}"
        if not ranked:
            return {"content": f"{header}\nNo BeyondTrust session matched it.",
                    "summary": "No linked session"}
        lines = [f"{row['id'] or '-'} · {row['text']}  (score {score}: {', '.join(why)})"
                 for score, row, why in ranked[:5]]
        return {"content": f"{header}\nPossible remote sessions:\n" + "\n".join(lines),
                "summary": f"{len(lines)} possible session(s)"}

    return _guard(ctx, genesys, run)
