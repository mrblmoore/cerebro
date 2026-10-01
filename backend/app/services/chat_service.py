"""
Chat service — the conversational layer tasks and nudges do not provide.

With an AI provider connected, every message (other than a typed "approve"
or "discard" for a pending draft) goes to the agent in
:mod:`app.services.agent`. The model answers the question itself and decides
when to search the knowledge base, open sources, local records, SharePoint or
the inbox, read a document, draft a reply or create a task — so a general
question gets an answer instead of a template, and an incomplete instruction
gets a natural follow-up question.

Without an AI provider the older deterministic path still applies:

1. **Questions get an honest reply** listing only sources that are actually
   relevant to what was asked.
2. **An incomplete instruction gets a clarifying question**, not a task that
   silently cannot run ("keep the project log updated" → "which document?").
3. **The next message answers that question** rather than starting over.

Every turn — both sides — is stored in :class:`~app.models.chat.ChatMessage` so
the widget can show a real thread instead of one reply at a time. Each turn
belongs to one chat (:class:`~app.models.conversation.Conversation`): history,
"what did I just say" and clarifications are all scoped to it, and the chat's
standing instructions go to the agent with every message.
"""

import json
import re
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core import logger
from app.models.chat import ChatMessage
from app.services.task_service import TaskService, describe_confirmation, parse_instruction

#: Words that open a question rather than an instruction. A message can still
#: contain one of these and be an instruction ("can you keep this updated
#: daily") — _looks_like_instruction below takes priority when both are
#: present, because acting on a clear instruction is safer than answering it
#: as if it were idle curiosity.
QUESTION_STARTERS = (
    "what", "who", "when", "where", "why", "how", "which", "is ", "are ",
    "do you", "does ", "can you tell", "could you tell", "should i", "will ",
)

#: Verbs that mark a message as something to *do*, not something to *answer*.
INSTRUCTION_MARKERS = (
    "remind me", "keep ", "update ", "add to", "maintain", "log ",
    "reply", "respond", "draft", "every day", "daily", "each day",
    "weekly", "hourly", "schedule",
)

IMMEDIATE_REQUESTS = (
    "summarize", "summarise", "explain", "analyze", "analyse", "compare",
    "read this", "look at", "find in", "tell me about",
)

VISUAL_REQUEST_MARKERS = (
    "image", "picture", "photo", "screenshot", "diagram", "figure",
    "chart", "graph", "visual", "illustration", "looks like",
)

DOCUMENT_REFERENCE_MARKERS = (
    "this document", "the document", "this file", "the file", "this slide",
    "the slide", "this page", "the page", "this deck", "the deck",
    "this pdf", "the pdf", "this report", "the report",
)

QUERY_STOPWORDS = {
    "about", "again", "also", "and", "are", "can", "could", "does",
    "chart", "deck", "diagram", "document", "figure", "file", "for",
    "from", "give", "graph", "have", "how", "illustration", "image",
    "into", "look", "looks", "make", "page", "pdf", "photo", "picture",
    "please", "reference", "report", "screenshot", "show", "slide", "that",
    "the", "this", "visual", "what", "when", "where", "which", "with",
    "would", "you",
}


def _looks_like_instruction(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in INSTRUCTION_MARKERS)


def _looks_like_immediate_request(text: str) -> bool:
    lowered = text.strip().lower()
    return any(marker in lowered for marker in IMMEDIATE_REQUESTS)


def _looks_like_question(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered.endswith("?"):
        return True
    return any(lowered.startswith(starter) for starter in QUESTION_STARTERS)


def _query_terms(text: str) -> set:
    return {
        token for token in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(token) > 2 and token not in QUERY_STOPWORDS
    }


def _requests_visual(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in VISUAL_REQUEST_MARKERS)


def _references_document(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in DOCUMENT_REFERENCE_MARKERS)


def _missing_field(parsed: Dict[str, Any], context: Dict[str, Any]) -> Optional[str]:
    """What Cerebro would need to actually run this task, if anything."""
    kind = parsed.get("kind")
    spec = parsed.get("spec") or {}
    if kind == "document_update" and not spec.get("document") \
            and not (context or {}).get("active_document"):
        return "document"
    if kind == "draft_reply" and not spec.get("message_id"):
        return "message"
    return None


def _clarifying_question(missing: str) -> str:
    return {
        "document": "Which document should I keep updated? A path, or the "
                    "name Cerebro already tracks it under, both work.",
        "message": "Which message should I draft a reply to? Open it in "
                  "Outlook or Teams, or tell me the case it's about.",
    }.get(missing, "Can you give me a bit more detail?")


class ChatService:
    def __init__(self, db: Session, conversation_id: int = None):
        from app.services import conversations

        self.db = db
        #: Raises LookupError for a chat id that does not exist.
        self.conversation = conversations.resolve(db, conversation_id)
        self.conversation_id = self.conversation.id

    def _in_chat(self, query):
        return query.filter(ChatMessage.conversation_id == self.conversation_id)

    # -------------------------------------------------------------- history
    def history(self, limit: int = 50) -> list:
        # ``created_at`` alone is not a safe sort key: it comes from SQLite's
        # ``CURRENT_TIMESTAMP``, which only has one-second resolution, and a
        # fast exchange of several messages can land in the same second. ``id``
        # breaks the tie in true insertion order.
        rows = (self._in_chat(self.db.query(ChatMessage))
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                .limit(limit).all())
        return [row.to_dict() for row in reversed(rows)]

    def _last_assistant_message(self) -> Optional[ChatMessage]:
        return (self._in_chat(self.db.query(ChatMessage))
                .filter(ChatMessage.role == "assistant")
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                .first())

    def _store(self, role: str, content: str, kind: str = None,
              meta: Dict[str, Any] = None, task_id: int = None,
              case_id: str = None, image_path: str = None) -> ChatMessage:
        message = ChatMessage(
            role=role, content=content, kind=kind,
            meta=json.dumps(meta) if meta else None,
            task_id=task_id, case_id=case_id, image_path=image_path,
            conversation_id=self.conversation_id,
        )
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        from app.services import conversations

        conversations.touch(self.db, self.conversation,
                            first_user_text=content if role == "user" else None)
        return message

    # --------------------------------------------------------------- intent
    def handle_message(self, text: str, context: Dict[str, Any] = None,
                       image: str = None, emit=None) -> Dict[str, Any]:
        """
        The front door: one message in, one reply out — everything else
        (was this a question, an instruction, or the answer to a clarifying
        question) is decided here.

        An attached image always means "look at this" rather than an
        instruction or a clarification reply — a photo of an error screen is
        never mistaken for a reminder.
        """
        text = (text or "").strip()
        context = dict(context or {})
        # Tools that create tasks file them under this chat.
        context["conversation_id"] = self.conversation_id

        if image:
            return self._answer_with_image(text, image, context)

        if not text:
            return {"reply": "Say something and I'll take it from there.",
                    "kind": "answer"}

        from app.services.ask_tools import AskToolService
        from app.services.llm_service import LLMService

        tools = AskToolService(self.db)

        # "Approve" / "discard" act on the pending draft card. That is a
        # button press typed out, so it is handled exactly, never interpreted.
        decision = tools.try_decision(text)
        if decision is not None:
            return self._store_tool_result(text, decision, context)

        # With an AI provider, every other message goes to the agent, which
        # decides for itself whether to answer, look something up, draft a
        # reply or create a task. Keyword routing below is only the no-AI
        # fallback: it used to catch ordinary questions ("how do I send a
        # Teams message?") and answer them with fixed templates.
        if LLMService().enabled:
            return self.answer_with_agent(text, context, emit=emit)

        tool_result = tools.try_execute(text, context)
        if tool_result is not None:
            return self._store_tool_result(text, tool_result, context)

        pending = self._pending_clarification()
        if pending:
            prior, missing = pending
            combined = f"{prior} ({text})"
            extra_spec = {}
            if missing == "document":
                extra_spec["document"] = text
            return self._create_or_clarify(text, combined, context,
                                          allow_clarify=False, extra_spec=extra_spec)

        # Ordinary statements are conversation, not latent scheduled work.
        # The old default treated every non-question ("Outlook is broken") as
        # a task, then often confirmed it would run "when you ask" even though
        # the user had just asked.  Only explicit task language enters the task
        # engine; everything else receives a direct conversational answer.
        if _looks_like_instruction(text) and not _looks_like_immediate_request(text):
            return self._create_or_clarify(text, text, context, allow_clarify=True)

        return self._answer_question(text, context)

    def _store_tool_result(self, text: str, tool_result: Dict[str, Any],
                           context: Dict[str, Any]) -> Dict[str, Any]:
        self._store("user", text, kind="instruction")
        meta = {
            key: tool_result[key] for key in
            ("tool", "cards", "sources", "images", "action", "notify")
            if tool_result.get(key) not in (None, [], {})
        }
        self._store("assistant", tool_result.get("reply") or "Done.",
                    kind=tool_result.get("kind") or "tool_result", meta=meta,
                    case_id=context.get("crm_case"))
        return tool_result

    def answer_with_agent(self, text: str, context: Dict[str, Any],
                          emit=None) -> Dict[str, Any]:
        """Answer through the tool-using agent (AI provider required)."""
        from app.services.agent import loop

        history = loop._history(self.db, self.conversation_id)
        self._store("user", text, kind="question")
        result = loop.run(self.db, text, context, emit=emit, history=history,
                          instructions=self.conversation.instructions)
        images = self._document_images_for_answer(text, context)
        if images:
            result["images"] = images
        meta = {key: result[key] for key in
                ("tool", "cards", "sources", "images", "action", "tools_used")
                if result.get(key) not in (None, [], {})}
        stored = self._store("assistant", result["reply"], kind=result.get("kind") or "answer",
                             meta=meta, task_id=(result.get("task") or {}).get("id"),
                             case_id=context.get("crm_case"))
        result["id"] = stored.id
        return result

    def _pending_clarification(self):
        last = self._last_assistant_message()
        if not last or last.kind != "clarification" or not last.meta:
            return None
        try:
            meta = json.loads(last.meta)
        except ValueError:
            return None
        prior = meta.get("prior_instruction")
        missing = meta.get("missing")
        if not prior:
            return None
        return prior, missing

    def _create_or_clarify(self, raw_text: str, instruction: str,
                           context: Dict[str, Any], allow_clarify: bool,
                           extra_spec: Dict[str, Any] = None) -> Dict[str, Any]:
        self._store("user", raw_text, kind="instruction")

        parsed = parse_instruction(instruction, context)
        missing = _missing_field(parsed, context) if allow_clarify else None
        if missing:
            question = _clarifying_question(missing)
            self._store("assistant", question, kind="clarification",
                       meta={"prior_instruction": instruction, "missing": missing})
            return {"reply": question, "kind": "clarification"}

        task = TaskService(self.db).create_from_instruction(
            instruction, context=context, source="chat", extra_spec=extra_spec,
            conversation_id=self.conversation_id)
        confirmation = describe_confirmation(task)
        self._store("assistant", confirmation, kind="confirmation", task_id=task.id,
                   case_id=context.get("crm_case"))
        return {"reply": confirmation, "kind": "confirmation", "task": task.to_dict()}

    def _recent_answer_image_names(self, limit: int = 12) -> set:
        """Images already shown recently, so a follow-up does not repeat them."""
        rows = (self._in_chat(self.db.query(ChatMessage))
                .filter(ChatMessage.role == "assistant",
                        ChatMessage.meta.isnot(None))
                .order_by(ChatMessage.id.desc()).limit(limit).all())
        seen = set()
        for row in rows:
            try:
                meta = json.loads(row.meta or "{}")
            except (TypeError, ValueError):
                continue
            for item in meta.get("images") or []:
                if isinstance(item, dict) and item.get("image"):
                    seen.add(item["image"])
        return seen

    def _document_images_for_answer(self, text: str,
                                    context: Dict[str, Any] = None,
                                    limit: int = 2) -> List[Dict[str, str]]:
        """
        Return document images only when the question actually asks for a
        visual/document reference, from a document relevant to that question.

        Previously every answer received the first images from the most recent
        document. That made an unrelated old diagram appear beside a general
        question and let the same image repeat on consecutive turns.
        """
        from app.models.tracked_document import TrackedDocument
        from app.services import chat_images

        visual_request = _requests_visual(text)
        document_reference = _references_document(text)
        if not visual_request and not document_reference:
            return []

        context = context or {}
        active_document = str(context.get("active_document") or "").lower()
        terms = _query_terms(text)

        try:
            docs = (self.db.query(TrackedDocument)
                    .filter(TrackedDocument.kind.in_(("docx", "pptx", "pdf")))
                    .order_by(TrackedDocument.last_seen.desc(),
                              TrackedDocument.id.desc())
                    .limit(12).all())
            from pathlib import Path

            candidates = []
            for recency, doc in enumerate(docs):
                path_name = str(doc.path or "").lower()
                doc_name = str(doc.name or "").lower()
                active = bool(active_document and (
                    active_document == path_name
                    or (doc_name and active_document.endswith(doc_name))
                    or (doc_name and doc_name == active_document)
                ))
                searchable = " ".join(filter(None, (
                    doc.name, doc.summary, (doc.text_preview or "")[:8000]
                )))
                document_terms = _query_terms(searchable)
                overlap = terms.intersection(document_terms)

                # A topical visual request must match the document unless the
                # UI explicitly says that document is active. A bare request
                # such as "show the diagram" may use the freshest document.
                if terms and not overlap and not active:
                    continue
                score = (100 if active else 0) + len(overlap) * 10 - recency
                candidates.append((score, doc))

            seen = self._recent_answer_image_names()
            selected = []
            selected_names = set(seen)
            for _, doc in sorted(candidates, key=lambda item: item[0], reverse=True):
                extracted = chat_images.extract_document_images(
                    Path(doc.path), doc.kind, limit=max(6, limit * 3))
                for item in extracted:
                    name = item.get("image") if isinstance(item, dict) else None
                    if not name or name in selected_names:
                        continue
                    selected.append(item)
                    selected_names.add(name)
                    if len(selected) >= limit:
                        return selected
            return selected
        except Exception as exc:  # noqa: BLE001 - illustrating an answer is optional
            logger.warn("chat", "Document image lookup failed", {"error": str(exc)})
            return []

    def _answer_with_image(self, text: str, image: str,
                           context: Dict[str, Any]) -> Dict[str, Any]:
        from app.services import chat_images
        from app.services.llm_service import LLMService

        self._store("user", text or "(sent an image)", kind="question", image_path=image)

        try:
            path = chat_images.resolve(image)
        except chat_images.ImageError as exc:
            reply = str(exc)
            self._store("assistant", reply, kind="answer")
            return {"reply": reply, "kind": "answer"}

        llm = LLMService()
        answer = llm.describe_image(path, question=text or None)
        self._store("assistant", answer, kind="answer")
        return {"reply": answer, "kind": "answer"}

    def _answer_question(self, text: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Answer without an AI provider: say so, and list what is relevant.

        Only sources that clear the relevance floor are listed. Previously the
        most recent document was listed for every question, so this reply was
        the same whatever was asked.
        """
        from app.core.config import settings
        from app.services.rag_service import RAGService
        from app.services.source_service import SourceService

        self._store("user", text, kind="question")
        floor = float(settings.ASK_MIN_SOURCE_SCORE or 0.0)

        try:
            hits = RAGService(self.db).search(text, limit=3, min_score=floor)
        except Exception as exc:  # noqa: BLE001 - a search failure must not block a reply
            logger.warn("chat", "Knowledge search failed", {"error": str(exc)})
            hits = []
        sources = SourceService(self.db).context_for_query(text, limit=4, min_score=floor)
        images = self._document_images_for_answer(text, context)
        citations = [{
            "ref": hit.get("citation") or f"K{index}",
            "title": hit.get("title"), "kind": "knowledge",
            "uri": hit.get("url"), "locator": hit.get("locator"),
            "excerpt": hit.get("excerpt", ""),
        } for index, hit in enumerate(hits, start=1)] + sources

        reply = ("AI generation is off, so I can't answer that directly — "
                 "open Settings → AI Provider to connect one.")
        if citations:
            reply += "\n\nThese look relevant to “" + text[:80] + "”:\n" + "\n".join(
                f"- [{item['ref']}] {item['title']} · {item.get('locator') or 'source'}"
                for item in citations)
        else:
            reply += " Nothing indexed or recently seen is about that either."
        cards = [{
            "type": "progress", "status": "complete",
            "title": "Searched connected sources",
            "detail": f"{len(citations)} relevant source(s)",
        }]
        meta = {"images": images, "sources": citations, "cards": cards,
                "tool": "search_sources"}
        self._store("assistant", reply, kind="answer", meta=meta)
        return {"reply": reply, "kind": "answer", "images": images,
                "sources": citations, "cards": cards, "tool": "search_sources"}

    def handle_change(self, action_id: int, decision: str) -> Dict[str, Any]:
        """Approve or discard a proposed change to an external system."""
        from app.services.agent import actions

        result = (actions.approve(self.db, action_id) if decision == "approve"
                  else actions.discard(self.db, action_id))
        self._store("user", "Approve change" if decision == "approve" else "Discard change",
                    kind="instruction")
        meta = {key: result[key] for key in ("cards", "notify", "agent_action")
                if result.get(key) not in (None, [], {})}
        self._store("assistant", result.get("reply") or "Done.",
                    kind=result.get("kind") or "completion", meta=meta)
        return result

    def handle_action(self, action_id: int, decision: str) -> Dict[str, Any]:
        """Approve or discard a preview shown in Ask and record the outcome."""
        from app.services.ask_tools import AskToolService

        service = AskToolService(self.db)
        result = (service.approve(action_id) if decision == "approve"
                  else service.discard(action_id))
        self._store("user", "Approve and send" if decision == "approve" else "Discard draft",
                    kind="instruction")
        meta = {
            key: result[key] for key in ("tool", "cards", "action", "notify")
            if result.get(key) not in (None, [], {})
        }
        self._store("assistant", result.get("reply") or "Done.",
                    kind=result.get("kind") or "completion", meta=meta)
        return result
