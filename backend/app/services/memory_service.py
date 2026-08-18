"""
The memory engine — Cerebro's second brain.

Three responsibilities:

1. **Store** durable facts, embedded so they can be recalled by relevance.
2. **Distil** raw activity and resolved cases into those facts, so memory grows
   from what Cerebro watched rather than only what it was told.
3. **Recall** the memories relevant to a task and hand them to the LLM, which is
   what makes a draft or an answer reflect what you have done before instead of
   starting cold every time.

Recall ranks by a blend of semantic similarity, confidence and past usefulness,
so a memory that keeps proving relevant rises and a stale guess fades.
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core import logger
from app.core.config import settings
from app.models.activity import ActivitySnapshot
from app.models.memory import Memory
from app.services import embeddings
from app.services.llm_service import LLMService

MAX_RECALL = 6


class MemoryService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------ writing
    def remember(self, title: str, content: str, memory_type: str = "fact",
                 case_id: str = None, customer: str = None, tags: List[str] = None,
                 source: str = "manual", confidence: float = 0.6,
                 pinned: bool = False, dedupe: bool = True) -> Memory:
        """Store a memory, embedding it for recall. Near-duplicates are merged."""
        content = (content or "").strip()
        if not content:
            raise ValueError("A memory needs content.")

        vector, signature = embeddings.embed_with_signature(f"{title}\n{content}")

        if dedupe:
            existing = self._find_similar(vector, signature, threshold=0.93,
                                          case_id=case_id)
            if existing is not None:
                # Reinforce rather than duplicate: a fact re-observed is a fact
                # we are more sure of.
                existing.confidence = min(1.0, (existing.confidence or 0.6) + 0.1)
                existing.use_count = (existing.use_count or 0) + 1
                existing.last_used_at = datetime.utcnow()
                self.db.commit()
                return existing

        memory = Memory(
            memory_type=memory_type, title=title[:200], content=content,
            case_id=case_id, customer=customer,
            tags=",".join(tags or []) or None,
            embedding=json.dumps(vector), embedding_signature=signature,
            confidence=confidence, source=source, pinned=pinned,
        )
        self.db.add(memory)
        self.db.commit()
        self.db.refresh(memory)
        logger.info("memory", "Stored memory",
                    {"type": memory_type, "title": title[:60], "source": source})
        return memory

    def _find_similar(self, vector: List[float], signature: str, threshold: float,
                      case_id: str = None) -> Optional[Memory]:
        query = self.db.query(Memory).filter(Memory.embedding_signature == signature)
        if case_id:
            query = query.filter(Memory.case_id == case_id)
        best, best_score = None, threshold
        for memory in query.limit(500):
            try:
                score = embeddings.cosine(vector, json.loads(memory.embedding))
            except (TypeError, ValueError):
                continue
            if score >= best_score:
                best, best_score = memory, score
        return best

    # ------------------------------------------------------------ recall
    def recall(self, query: str, limit: int = MAX_RECALL, case_id: str = None,
               customer: str = None) -> List[Dict[str, Any]]:
        """
        The memories most relevant to ``query``, ranked and usage-counted.

        Ranking blends similarity with confidence and a gentle boost for
        memories scoped to the same case or customer, so "how did we fix this
        for Contoso" surfaces the Contoso-specific note above a generic one.
        """
        if not settings.MEMORY_ENABLED or not (query or "").strip():
            return []

        query_vector, signature = embeddings.embed_with_signature(query)
        scored: List[tuple] = []

        for memory in self.db.query(Memory).filter(
                Memory.embedding_signature == signature).limit(2000):
            try:
                similarity = embeddings.cosine(query_vector, json.loads(memory.embedding))
            except (TypeError, ValueError):
                continue
            if similarity <= 0:
                continue

            score = similarity * (0.5 + 0.5 * (memory.confidence or 0.6))
            if case_id and memory.case_id == case_id:
                score += 0.15
            if customer and memory.customer == customer:
                score += 0.1
            if memory.pinned:
                score += 0.1
            scored.append((score, similarity, memory))

        scored.sort(key=lambda item: item[0], reverse=True)
        top = scored[:limit]

        # Recall is a signal of usefulness; record it so ranking improves.
        for _, _, memory in top:
            memory.use_count = (memory.use_count or 0) + 1
            memory.last_used_at = datetime.utcnow()
        if top:
            self.db.commit()

        return [{**memory.to_dict(), "relevance": round(similarity, 3)}
                for _, similarity, memory in top]

    def recall_text(self, query: str, **kwargs) -> str:
        """Relevant memories as a prompt-ready block, or '' when there are none."""
        memories = self.recall(query, **kwargs)
        if not memories:
            return ""
        lines = [f"- {m['content']}" for m in memories]
        return "What Cerebro remembers that may be relevant:\n" + "\n".join(lines)

    # ------------------------------------------------------------ distil
    def distil_case(self, case) -> Optional[Memory]:
        """Turn a resolved case into a reusable memory."""
        if not case or not (case.troubleshooting_steps or case.ai_summary):
            return None
        content = (
            f"Case {case.case_id} ({case.customer or 'unknown customer'}): "
            f"{case.title}. "
            f"{case.ai_summary or ''} "
            f"Resolution: {case.troubleshooting_steps or 'not recorded'}"
        ).strip()
        return self.remember(
            title=f"How we handled {case.title[:80]}",
            content=content, memory_type="case_resolution",
            case_id=case.case_id, customer=case.customer,
            tags=[t for t in [case.system, case.application] if t],
            source="case", confidence=0.75,
        )

    def distil_activity(self, limit: int = 40) -> Dict[str, Any]:
        """
        Summarise recent captured activity into memories.

        Activity is raw and voluminous; a memory is compact and durable. This
        asks the LLM to pull the few facts worth keeping out of a batch of
        snapshots, then discards the snapshots' claim on attention by marking
        them distilled.
        """
        if not settings.MEMORY_ENABLED:
            return {"ok": False, "detail": "memory disabled"}

        llm = LLMService()
        if not llm.enabled:
            return {"ok": False, "detail": "no AI provider configured"}

        snapshots = (self.db.query(ActivitySnapshot)
                     .filter(ActivitySnapshot.distilled.is_(None))
                     .filter(ActivitySnapshot.text.isnot(None))
                     .order_by(ActivitySnapshot.captured_at.asc())
                     .limit(limit).all())
        if not snapshots:
            return {"ok": True, "created": 0, "detail": "nothing new to distil"}

        transcript = "\n".join(
            f"[{s.application or 'app'}] {s.window_title or ''}: {s.text}"
            for s in snapshots if s.text
        )[:8000]

        prompt = f"""From this log of a support engineer's activity, extract the
few durable facts worth remembering — how a problem was solved, a customer
preference, a procedure, a decision. Ignore idle chatter and anything transient.

Return a JSON array; each item {{"title": "...", "content": "...", "type": "..."}}
where type is one of case_resolution, customer_fact, preference, procedure, fact.
Return [] if nothing is worth keeping.

Activity:
{transcript}"""

        raw = llm._call_llm(prompt)
        created = 0
        for item in _parse_json_array(raw):
            title = (item.get("title") or "").strip()
            content = (item.get("content") or "").strip()
            if not content:
                continue
            self.remember(title=title or content[:60], content=content,
                          memory_type=item.get("type", "fact"),
                          source="activity", confidence=0.6)
            created += 1

        now = datetime.utcnow()
        for snapshot in snapshots:
            snapshot.distilled = now
        self.db.commit()

        logger.info("memory", "Distilled activity",
                    {"snapshots": len(snapshots), "memories": created})
        return {"ok": True, "created": created, "snapshots": len(snapshots)}

    # ------------------------------------------------------------ manage
    def list_memories(self, limit: int = 50, memory_type: str = None,
                      case_id: str = None) -> List[Memory]:
        query = self.db.query(Memory)
        if memory_type:
            query = query.filter(Memory.memory_type == memory_type)
        if case_id:
            query = query.filter(Memory.case_id == case_id)
        return query.order_by(Memory.pinned.desc(),
                              Memory.last_used_at.desc().nullslast(),
                              Memory.created_at.desc()).limit(limit).all()

    def forget(self, memory: Memory) -> None:
        self.db.delete(memory)
        self.db.commit()

    def status(self) -> Dict[str, Any]:
        if not settings.MEMORY_ENABLED:
            return {"ok": True, "enabled": False, "detail": "Memory disabled"}
        total = self.db.query(Memory).count()
        return {"ok": True, "enabled": True, "memories": total,
                "detail": f"{total} {'memories' if total != 1 else 'memory'} learned"}


def _parse_json_array(text: str) -> List[Dict[str, Any]]:
    """Pull a JSON array out of an LLM reply, tolerating surrounding prose."""
    text = (text or "").strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
        return [item for item in data if isinstance(item, dict)]
    except ValueError:
        return []
