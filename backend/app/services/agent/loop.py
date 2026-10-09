"""
The Ask agent loop.

One user message in; the model may call tools (search, read, draft …) a
bounded number of times; one answer out. Progress is reported through
``emit`` as it happens so the UI can show "Searching knowledge base…" while
the answer is still being worked out.

Why it is shaped this way — each point fixes a way Ask used to go wrong:

* The conversation is sent as real user/assistant turns, and the new message
  comes **last**. Previously the whole history, including the last long
  answer, was flattened into one block with six document excerpts *after* the
  question, and the model tended to re-summarise that document.
* Context is fetched **on demand** by the model (tools) and only offered up
  front when it clears a relevance floor. An open document is no longer
  evidence for an unrelated question.
* If the draft answer is essentially the previous answer again while the
  question is different, the model is told so and asked once more.
"""

import difflib
import json
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from app.core import logger
from app.core.activity_state import activity
from app.core.config import settings
from app.services.agent import tools as _tools  # noqa: F401 - registers the tools
from app.services.agent.registry import REGISTRY, ToolContext, available_tools

ASK_SYSTEM_PROMPT = """You are Cerebro, an AI copilot running on a technical support engineer's computer. Today is {today}.

How to respond:
- Answer the user's LATEST message directly. Treat it as a new request. Do not repeat, restate or re-summarise your earlier answers unless the user asks you to.
- If the answer depends on the user's own information — their knowledge base, open documents, cases, tickets, messages, SharePoint, memory — use the tools to look it up before answering. Search before saying you don't know. Try a second search with different words if the first finds nothing useful.
- For a question the knowledge base might answer (how-to, errors, known issues), never answer from the search titles alone: when rightanswers_research is available, call it with several different phrasings, then answer from the article text it returns and cite those articles. If the articles don't cover it, search again with new words before giving up.
- For general technical knowledge you are confident about, just answer; no tool is needed.
- Context provided with a message is optional evidence. Ignore anything in it that is not about the question.
- When a statement comes from a source, cite its bracketed ID exactly, e.g. [K1] or [S2]. Never invent IDs. Say plainly when nothing you found answers the question, then give your best general guidance.
- Use only the tools the request needs. To message someone, go straight to send_teams_message / send_email: pass the person's name exactly as the user said it (a first name or partial name is fine; the app finds them) and never ask for a full name or email first. Don't search the knowledge base, cases or documents unless the message itself needs them.
- To compare cases, fetch each with compare_cases and point out differences and shared causes. To review a log, use review_log and explain the repeated errors and when they began.
- When given a file path, folder or URL, use list_folder, find_in_files, read_file, review_logs_in_folder or fetch_url. To change a file use write_file (the user approves first). For recurring work use create_task with a schedule. run_command exists only if the user enabled it.
- Anything that sends or changes something outside this computer is prepared as a card for the user to approve — unless the tool result says it was already applied (the user can turn that on for SharePoint). Never claim something was sent, posted or updated unless a tool result says so.
- Be concise and specific. Use short numbered steps for procedures. Markdown is fine."""

MESSAGING_TOOLS = ("send_", "reply_", "draft_", "outlook_", "teams_", "get_message_thread",
                   "get_inbox_briefing", "create_task")
_MESSAGING_VERB = re.compile(
    r"\b(send|message|msg|ping|dm|tell|email|e-mail|reply|respond|write to|let\b.{0,30}\bknow)\b", re.I)
_MESSAGING_CHANNEL = re.compile(r"\b(teams|outlook|email|e-mail|chat|dm|message)\b", re.I)
_RESEARCH_WORDS = re.compile(
    r"\b(search|look up|lookup|find|research|article|kb|knowledge|how (do|to|can)|why|"
    r"troubleshoot|case|ticket|log|compare|summari[sz]e|according)\b", re.I)


def messaging_only(text: str) -> bool:
    """True for a plain "send X a message" request that needs no lookups."""
    text = text or ""
    return bool(_MESSAGING_VERB.search(text) and _MESSAGING_CHANNEL.search(text)
                and not _RESEARCH_WORDS.search(text))


#: Previous answers are shortened in the history the model sees; the model
#: needs to know what it said, not to be handed its old answer to copy.
HISTORY_TURNS = 10
LAST_ANSWER_CHARS = 1_500
OLDER_ANSWER_CHARS = 500
REPEAT_SIMILARITY = 0.85


def _history(db, conversation_id: int = None, limit: int = HISTORY_TURNS) -> List[dict]:
    """The recent turns of one chat (or of every chat, when none is named)."""
    from app.models.chat import ChatMessage

    query = db.query(ChatMessage)
    if conversation_id:
        query = query.filter(ChatMessage.conversation_id == conversation_id)
    rows = query.order_by(ChatMessage.id.desc()).limit(limit).all()
    rows = list(reversed(rows))
    messages: List[dict] = []
    last_assistant = max((i for i, r in enumerate(rows) if r.role == "assistant"), default=-1)
    for index, row in enumerate(rows):
        if row.role not in ("user", "assistant") or not row.content:
            continue
        content = row.content
        if row.role == "assistant":
            cap = LAST_ANSWER_CHARS if index == last_assistant else OLDER_ANSWER_CHARS
            if len(content) > cap:
                content = content[:cap].rsplit(" ", 1)[0] + " …[earlier answer shortened]"
        messages.append({"role": row.role, "content": content})
    # A conversation must open with the user.
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages


def _context_note(ctx: ToolContext) -> str:
    """Short, factual notes about the moment — not evidence to be summarised."""
    context = ctx.context or {}
    notes = []
    if context.get("crm_case"):
        notes.append(f"Open case: {context['crm_case']}"
                     + (f" ({context.get('customer')})" if context.get("customer") else ""))
    if context.get("active_document"):
        notes.append(f"Document in view: {context['active_document']}")
    return "; ".join(notes)


def _sharepoint_links(text: str) -> List[str]:
    from app.services.agent.registry import REGISTRY as tools
    from app.services.browser.sharepoint import find_links

    if "sharepoint_read" not in tools:
        return []
    return find_links(text)[:5]


def _prefetch(ctx: ToolContext, text: str) -> str:
    """Up-front context, offered only when it is genuinely about the question.

    Models that are weak at tool use still get the obviously relevant
    material; everything else is left for the model to search for.
    """
    from app.services.rag_service import RAGService
    from app.services.source_service import SourceService
    from app.services.agent.tools import _lines

    floor = float(settings.ASK_MIN_SOURCE_SCORE or 0.0)
    blocks = []
    with activity("searching", "Looking for relevant sources"):
        try:
            hits = RAGService(ctx.db).search(text, limit=3, min_score=floor)
        except Exception as exc:  # noqa: BLE001 - search failure must not block a reply
            logger.warn("agent", "Knowledge prefetch failed", {"error": str(exc)})
            hits = []
        if hits:
            blocks.append(_lines(ctx, [{
                "title": hit.get("title"), "kind": "knowledge", "uri": hit.get("url"),
                "locator": hit.get("locator"), "excerpt": hit.get("excerpt", "")}
                for hit in hits], "K"))
        try:
            found = SourceService(ctx.db).context_for_query(text, limit=3, min_score=floor)
        except Exception as exc:  # noqa: BLE001
            logger.warn("agent", "Source prefetch failed", {"error": str(exc)})
            found = []
        if found:
            blocks.append(_lines(ctx, found, "S"))
    return "\n".join(blocks)


def _user_turn(text: str, note: str, prefetched: str) -> str:
    parts = []
    if note:
        parts.append(f"(Context: {note})")
    if prefetched:
        parts.append("Possibly relevant sources — use only if they help:\n" + prefetched)
    parts.append(text if not parts else f"Message:\n{text}")
    return "\n\n".join(parts)


def _repeats(answer: str, previous: Optional[str]) -> bool:
    if not answer or not previous:
        return False
    a, b = answer.strip().lower(), previous.strip().lower()
    if len(a) < 80:
        return False
    return difflib.SequenceMatcher(None, a[:2000], b[:2000]).ratio() >= REPEAT_SIMILARITY


def _run_tool(ctx: ToolContext, name: str, arguments: Dict[str, Any],
              done: Dict[str, str]) -> str:
    item = REGISTRY.get(name)
    if item is None or not item.is_available(ctx):
        return f"Tool {name} is not available. Use one of the listed tools or answer directly."
    key = f"{name}:{json.dumps(arguments, sort_keys=True)}"
    if key in done:
        return "(Already done above — use that result rather than repeating the call.)"

    detail = arguments.get("query") or arguments.get("name") or arguments.get("message") \
        or arguments.get("instruction") or ""
    ctx.progress(item.label, str(detail)[:120] or None, "running")
    try:
        with activity(item.activity, f"{item.label}{': ' + str(detail)[:60] if detail else ''}"):
            result = item.handler(ctx, **(arguments or {}))
    except TypeError as exc:
        result = {"content": f"Bad arguments for {name}: {exc}", "summary": "Bad arguments"}
    except Exception as exc:  # noqa: BLE001 - a failing tool is reported, not fatal
        logger.error("agent", "Tool failed", {"tool": name, "error": str(exc)})
        result = {"content": f"{name} failed: {exc}", "summary": "Failed"}

    ctx.progress(item.label, result.get("summary") or None,
                 "warning" if result.get("summary") in ("Failed", "Bad arguments") else "complete")
    content = str(result.get("content") or "(no result)")
    cap = max(_tools.RESULT_CHARS, int(result.get("max_chars") or 0))
    if len(content) > cap:
        content = content[:cap] + "\n…[truncated]"
    done[key] = content
    for extra in ("action", "task"):
        if result.get(extra):
            ctx.context.setdefault("_results", {})[extra] = result[extra]
    return content


def run(db, text: str, context: Dict[str, Any] = None,
        emit: Callable[[dict], None] = None,
        history: List[dict] = None,
        instructions: str = None) -> Dict[str, Any]:
    """Answer one Ask message. Returns the reply payload; never raises.

    ``history`` is the conversation *before* this message; it is read from
    the database when not given. ``instructions`` are the chat's standing
    instructions, followed for every message in that chat.
    """
    from app.services.llm_service import LLMService

    llm = LLMService()
    ctx = ToolContext(db=db, context=dict(context or {}), emit=emit)
    history = _history(db, ctx.context.get("conversation_id")) if history is None else history
    previous_answer = next((m["content"] for m in reversed(history)
                            if m["role"] == "assistant"), None)

    focused = messaging_only(text)
    prefetched = "" if focused else _prefetch(ctx, text)
    note = _context_note(ctx)
    links = _sharepoint_links(text)
    if links:
        # Pasted links are the most direct evidence there is: point the model
        # straight at them rather than hoping it notices.
        note = "; ".join(filter(None, [note, "SharePoint link(s) in the message — open with "
                                             "sharepoint_read: " + ", ".join(links)]))
    messages = history + [{"role": "user", "content": _user_turn(text, note, prefetched)}]
    tool_specs = [item.spec() for item in available_tools(ctx)
                  if not focused or item.name.startswith(MESSAGING_TOOLS)]
    system = ASK_SYSTEM_PROMPT.format(today=datetime.now().strftime("%A %d %B %Y"))
    if (instructions or "").strip():
        system += ("\n\nThis chat's standing instructions from the user (follow them unless "
                   "they conflict with the rules above):\n" + instructions.strip())
    memory = _memory_block(db, text, ctx.context)
    if memory:
        system += "\n\n" + memory

    max_steps = max(1, int(settings.ASK_MAX_STEPS or 6))
    max_tokens = int(settings.ASK_MAX_TOKENS or settings.LLM_MAX_TOKENS)
    done: Dict[str, str] = {}
    used_tools: List[str] = []
    answer = ""
    retried_repeat = False

    try:
        step = 0
        while True:
            offer = tool_specs if step < max_steps else []
            result = llm.chat(messages, tools=offer, system=system, max_tokens=max_tokens)
            calls = result.get("tool_calls") or []
            if calls and offer:
                step += 1
                messages.append({"role": "assistant", "content": result.get("content") or "",
                                 "tool_calls": calls})
                for call in calls:
                    used_tools.append(call["name"])
                    output = _run_tool(ctx, call["name"], call.get("arguments") or {}, done)
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "name": call["name"], "content": output})
                if step >= max_steps:
                    messages.append({"role": "user", "content":
                                     "That is enough research — answer now with what you have."})
                continue

            answer = (result.get("content") or "").strip()
            if not retried_repeat and _repeats(answer, previous_answer):
                retried_repeat = True
                messages.append({"role": "assistant", "content": answer})
                messages.append({"role": "user", "content": (
                    "That repeats your previous answer. Answer my latest message "
                    "itself, directly — do not restate the earlier answer.")})
                continue
            break
    except Exception as exc:  # noqa: BLE001 - explained to the user below
        logger.error("agent", "Ask failed", {"error": str(exc)})
        answer = f"I couldn't reach the AI provider: {exc}"

    if not answer:
        answer = ("I looked but couldn't put an answer together. Try rephrasing, "
                  "or tell me where the information lives.")

    cited = [source for source in ctx.sources if f"[{source['ref']}]" in answer]
    cards = _summary_cards(used_tools, cited) + ctx.drafts
    payload = {
        "reply": answer,
        "kind": "draft" if any(c.get("type") == "draft" or (
            c.get("type") == "approval" and c.get("status") == "awaiting_approval")
            for c in ctx.drafts) else "answer",
        "sources": cited, "cards": cards, "images": [],
        "tool": used_tools[-1] if used_tools else None,
        "tools_used": used_tools,
    }
    results = ctx.context.get("_results") or {}
    if results.get("action"):
        payload["action"] = results["action"]
    if results.get("task"):
        payload["task"] = results["task"]
    return payload


def _memory_block(db, text: str, context: Dict[str, Any]) -> str:
    """Relevant memories, as background the model can draw on."""
    if not settings.MEMORY_ENABLED:
        return ""
    try:
        from app.services.memory_service import MemoryService

        floor = float(settings.ASK_MIN_SOURCE_SCORE or 0.0)
        memories = [m for m in MemoryService(db).recall(
            text, limit=4, case_id=context.get("crm_case"), customer=context.get("customer"))
            if (m.get("relevance") or 0) >= floor]
    except Exception:  # noqa: BLE001 - memory is optional
        return ""
    if not memories:
        return ""
    return "Things you remember from earlier work (use only if relevant):\n" + "\n".join(
        f"- {m.get('content')}" for m in memories)


def _summary_cards(used_tools: List[str], cited: List[dict]) -> List[dict]:
    cards = []
    if used_tools:
        labels = []
        for name in used_tools:
            label = REGISTRY[name].label if name in REGISTRY else name
            if label not in labels:
                labels.append(label)
        cards.append({"type": "progress", "status": "complete",
                      "title": "Looked things up", "detail": " · ".join(labels)})
    cards.append({"type": "completion", "status": "complete", "title": "Answer ready",
                  "detail": f"{len(cited)} source(s) cited" if cited else "No sources needed"})
    return cards
