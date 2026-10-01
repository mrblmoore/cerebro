"""
Separate chats in Ask: create, find, list, title, end.

The rules are small but worth having in one place:

* A message with no chat named goes to the most recently active open chat, or
  a new one when there is none — so older clients keep working.
* A new chat is titled from the first thing the user says in it; renaming is
  always possible and is never overwritten.
* "End conversation" archives the chat and returns a fresh one to continue in.
"""

import re
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.chat import ChatMessage
from app.models.conversation import Conversation

DEFAULT_TITLE = "New chat"
TITLE_WORDS = 7
TITLE_CHARS = 48


def create(db: Session, title: str = None, instructions: str = None) -> Conversation:
    chat = Conversation(title=(title or "").strip()[:120] or None,
                        instructions=(instructions or "").strip() or None)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


def get(db: Session, conversation_id: int) -> Optional[Conversation]:
    if not conversation_id:
        return None
    return db.query(Conversation).filter(Conversation.id == int(conversation_id)).first()


def resolve(db: Session, conversation_id: int = None) -> Conversation:
    """The chat a message belongs to: the one named, else the latest open one."""
    chat = get(db, conversation_id)
    if chat is not None:
        return chat
    if conversation_id:
        raise LookupError(f"Chat {conversation_id} does not exist.")
    return _most_recent_open(db) or create(db)


def _most_recent_open(db: Session) -> Optional[Conversation]:
    rows = db.query(Conversation).filter(Conversation.archived.is_(False)).all()
    if not rows:
        return None
    return max(rows, key=lambda c: (c.last_message_at or c.created_at or datetime.min, c.id))


def listing(db: Session, archived: bool = False, query: str = None,
            limit: int = 100) -> List[dict]:
    """Chats for the sidebar: pinned first, then most recently active."""
    rows = db.query(Conversation).filter(Conversation.archived.is_(bool(archived))).all()
    if query:
        needle = query.lower().strip()
        matching_ids = {cid for (cid,) in db.query(ChatMessage.conversation_id)
                        .filter(ChatMessage.content.ilike(f"%{needle}%")).distinct()}
        rows = [c for c in rows if needle in (c.title or "").lower() or c.id in matching_ids]
    rows.sort(key=lambda c: (bool(c.pinned), c.last_message_at or c.created_at or datetime.min,
                             c.id), reverse=True)
    out = []
    for chat in rows[:limit]:
        item = chat.to_dict()
        last = (db.query(ChatMessage).filter(ChatMessage.conversation_id == chat.id)
                .order_by(ChatMessage.id.desc()).first())
        item["preview"] = _preview(last.content) if last else ""
        item["messages"] = db.query(ChatMessage.id) \
            .filter(ChatMessage.conversation_id == chat.id).count()
        out.append(item)
    return out


def touch(db: Session, chat: Conversation, first_user_text: str = None) -> None:
    """Record activity, and title an untitled chat from its first message."""
    # UTC, like the database's own CURRENT_TIMESTAMP on created_at.
    chat.last_message_at = datetime.utcnow()
    if first_user_text and not chat.title:
        chat.title = title_from(first_user_text)
    db.commit()


def title_from(text: str) -> str:
    words = re.sub(r"\s+", " ", (text or "")).strip()
    words = re.sub(r"https?://\S+", "link", words)
    if not words:
        return DEFAULT_TITLE
    title = " ".join(words.split(" ")[:TITLE_WORDS])
    if len(title) > TITLE_CHARS:
        title = title[:TITLE_CHARS].rsplit(" ", 1)[0]
    title = title.rstrip(" ?.!,;:")
    if len(words) > len(title) + 1:
        title += "…"
    return title[:1].upper() + title[1:]


def update(db: Session, chat: Conversation, **fields) -> Conversation:
    for key in ("title", "instructions"):
        if fields.get(key) is not None:
            value = str(fields[key]).strip()
            setattr(chat, key, (value[:120] if key == "title" else value) or None)
    for key in ("pinned", "archived"):
        if fields.get(key) is not None:
            setattr(chat, key, bool(fields[key]))
    db.commit()
    db.refresh(chat)
    return chat


def end(db: Session, chat: Conversation) -> tuple:
    """
    Archive ``chat`` and return ``(fresh chat, tasks stopped)``.

    Ending a conversation is leaving it, so its scheduled tasks stop too —
    otherwise they would keep posting into a chat nobody is looking at.
    """
    from app.models.task import Task

    stopped = db.query(Task).filter(Task.conversation_id == chat.id,
                                    Task.status == "active") \
        .update({Task.status: "cancelled", Task.next_run: None}, synchronize_session=False)
    chat.archived = True
    chat.pinned = False
    db.commit()
    return create(db), stopped


def delete(db: Session, chat: Conversation) -> None:
    """Delete a chat and its messages; tasks assigned to it are cancelled."""
    from app.models.task import Task

    db.query(ChatMessage).filter(ChatMessage.conversation_id == chat.id) \
        .delete(synchronize_session=False)
    db.query(Task).filter(Task.conversation_id == chat.id) \
        .update({Task.status: "cancelled", Task.next_run: None}, synchronize_session=False)
    db.delete(chat)
    db.commit()


def _preview(text: str) -> str:
    text = re.sub(r"\[(?:[KS]\d+)\]", "", text or "")          # citations
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)        # links
    text = re.sub(r"[*_`#>]+", "", text)                        # emphasis, code, headings
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= 90 else text[:89].rstrip() + "…"
