"""
Chat service — the conversational layer tasks and nudges do not provide.

Four things happen here that nowhere else in Cerebro does:

1. **General questions get answered**, not turned into a task. "What's the fix
   for 0x80040115?" should get an answer, using the AI provider plus whatever
   the knowledge base and memory already know — not "I'll 0x80040115 — once,
   shortly."
2. **An incomplete instruction gets a clarifying question**, not a task that
   silently cannot run. "Keep the project log updated daily under my name"
   with no document in view is asked "which document?" instead of creating a
   task that fails at 9am with "no document set."
3. **The next message answers that question** rather than starting over: the
   most recent clarification is remembered, so "the one on the shared drive"
   completes the original instruction instead of becoming its own reminder.
4. **Answers also draw on what Cerebro has *seen but not indexed*** — a
   document someone just opened, or a webpage the browser extension captured
   — not only the deliberately-indexed knowledge base. Indexing stays an
   explicit, permanent, opt-in action (``POST /documents/{id}/index``); this
   is a lighter, ephemeral read of whatever is already sitting in
   ``tracked_documents``/``events`` so a fresh document is useful immediately.

Every turn — both sides — is stored in :class:`~app.models.chat.ChatMessage` so
the widget can show a real thread instead of one reply at a time.
"""

import json
import re
from datetime import datetime, timedelta
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
    "reply", "respond", "draft", "summar", "every day", "daily", "each day",
    "weekly", "hourly", "schedule",
)


def _looks_like_instruction(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in INSTRUCTION_MARKERS)


def _looks_like_question(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered.endswith("?"):
        return True
    return any(lowered.startswith(starter) for starter in QUESTION_STARTERS)


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
    def __init__(self, db: Session):
        self.db = db

    # -------------------------------------------------------------- history
    def history(self, limit: int = 50) -> list:
        # ``created_at`` alone is not a safe sort key: it comes from SQLite's
        # ``CURRENT_TIMESTAMP``, which only has one-second resolution, and a
        # fast exchange of several messages can land in the same second. ``id``
        # breaks the tie in true insertion order.
        rows = (self.db.query(ChatMessage)
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                .limit(limit).all())
        return [row.to_dict() for row in reversed(rows)]

    def _last_assistant_message(self) -> Optional[ChatMessage]:
        return (self.db.query(ChatMessage)
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
        )
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message

    # --------------------------------------------------------------- intent
    def handle_message(self, text: str, context: Dict[str, Any] = None,
                       image: str = None) -> Dict[str, Any]:
        """
        The front door: one message in, one reply out — everything else
        (was this a question, an instruction, or the answer to a clarifying
        question) is decided here.

        An attached image always means "look at this" rather than an
        instruction or a clarification reply — a photo of an error screen is
        never mistaken for a reminder.
        """
        text = (text or "").strip()
        context = context or {}

        if image:
            return self._answer_with_image(text, image, context)

        if not text:
            return {"reply": "Say something and I'll take it from there.",
                    "kind": "answer"}

        pending = self._pending_clarification()
        if pending:
            prior, missing = pending
            combined = f"{prior} ({text})"
            extra_spec = {}
            if missing == "document":
                extra_spec["document"] = text
            return self._create_or_clarify(text, combined, context,
                                          allow_clarify=False, extra_spec=extra_spec)

        if _looks_like_instruction(text) or not _looks_like_question(text):
            return self._create_or_clarify(text, text, context, allow_clarify=True)

        return self._answer_question(text, context)

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
            instruction, context=context, source="chat", extra_spec=extra_spec)
        confirmation = describe_confirmation(task)
        self._store("assistant", confirmation, kind="confirmation", task_id=task.id,
                   case_id=context.get("crm_case"))
        return {"reply": confirmation, "kind": "confirmation", "task": task.to_dict()}

    def _ambient_snippets(self, limit: int = 5) -> List[Dict[str, str]]:
        """
        What Cerebro has *seen* recently but never indexed.

        Two sources, both already collected elsewhere for other reasons:
        ``TrackedDocument`` (the desktop watcher / browser extension noticing
        a document is open) and ``Event`` rows of type ``PAGE_CAPTURED`` (the
        browser extension's opt-in readable-page-text capture). Neither
        requires the deliberate "index this" step — that stays reserved for
        material someone wants permanently searchable.
        """
        from app.models.event import Event
        from app.models.tracked_document import TrackedDocument

        snippets: List[Dict[str, str]] = []

        try:
            recent_docs = (self.db.query(TrackedDocument)
                          .filter(TrackedDocument.text_preview.isnot(None))
                          .order_by(TrackedDocument.last_seen.desc())
                          .limit(limit).all())
            for doc in recent_docs:
                snippets.append({
                    "label": f"open document — {doc.name}",
                    "excerpt": (doc.text_preview or "")[:500],
                })
        except Exception as exc:  # noqa: BLE001
            logger.warn("chat", "Tracked-document lookup failed", {"error": str(exc)})

        try:
            cutoff = datetime.utcnow() - timedelta(minutes=30)
            recent_pages = (self.db.query(Event)
                           .filter(Event.event_type == "PAGE_CAPTURED",
                                   Event.ocr_text.isnot(None),
                                   Event.created_at >= cutoff)
                           .order_by(Event.created_at.desc())
                           .limit(limit).all())
            for event in recent_pages:
                data = event.data or {}
                title = data.get("title") or data.get("url") or "a captured page"
                snippets.append({
                    "label": f"webpage — {title}",
                    "excerpt": (event.ocr_text or "")[:500],
                })
        except Exception as exc:  # noqa: BLE001
            logger.warn("chat", "Page-capture lookup failed", {"error": str(exc)})

        return snippets[: limit * 2]

    def _document_images_for_answer(self, limit: int = 2) -> List[Dict[str, str]]:
        """
        Images pulled from whatever document Cerebro most recently saw —
        "the diagram on page 3" is worth showing, not just quoting near.
        """
        from app.models.tracked_document import TrackedDocument
        from app.services import chat_images

        try:
            doc = (self.db.query(TrackedDocument)
                  .filter(TrackedDocument.kind.in_(("docx", "pptx", "pdf")))
                  .order_by(TrackedDocument.last_seen.desc())
                  .first())
            if not doc:
                return []
            from pathlib import Path

            return chat_images.extract_document_images(Path(doc.path), doc.kind, limit=limit)
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
        from app.services.llm_service import LLMService
        from app.services.rag_service import RAGService

        self._store("user", text, kind="question")

        rag = RAGService(self.db)
        try:
            hits = rag.search(text, limit=3)
        except Exception as exc:  # noqa: BLE001 - a search failure must not block a reply
            logger.warn("chat", "Knowledge search failed", {"error": str(exc)})
            hits = []

        ambient = self._ambient_snippets()
        images = self._document_images_for_answer()

        llm = LLMService()
        if not llm.enabled:
            reply = ("AI generation is off, so I can't answer that directly — "
                     "open Settings → AI Provider to connect one. ")
            related = [f"- {hit['title']}" for hit in hits] + \
                [f"- {item['label']}" for item in ambient]
            if related:
                reply += "These looked related:\n" + "\n".join(related)
            else:
                reply += "I didn't find anything indexed or recently seen about it either."
            self._store("assistant", reply, kind="answer",
                       meta={"images": images} if images else None)
            return {"reply": reply, "kind": "answer", "images": images}

        doc_block = "\n".join(
            f"- {hit['title']}: {hit.get('excerpt', hit.get('content', ''))[:200]}"
            for hit in hits) or "(none found)"
        ambient_block = "\n".join(
            f"- {item['label']}: {item['excerpt']}" for item in ambient) or "(none)"
        prompt = f"""Answer the user's question. Use the documents and recently-seen
material below if they are relevant; otherwise answer from what you know. Be
concise and direct.

Question: {text}

Open case: {context.get('crm_case') or 'none'}
Customer: {context.get('customer') or 'none'}
Relevant indexed documents:
{doc_block}

Recently seen (open documents / captured pages — not indexed, but current):
{ambient_block}"""

        prompt = llm.with_memory(prompt, query=text, db=self.db,
                                 case_id=context.get("crm_case"))
        answer = llm._call_llm(prompt)
        self._store("assistant", answer, kind="answer",
                   meta={"images": images} if images else None)
        return {"reply": answer, "kind": "answer", "images": images}
