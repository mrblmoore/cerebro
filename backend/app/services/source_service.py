"""Unified source registry and query-time context selection."""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.source import Source
from app.services import text_chunks


def stable_key(kind: str, identity: str) -> str:
    digest = hashlib.sha256(str(identity).encode("utf-8", errors="replace")).hexdigest()
    return f"{kind}:{digest}"


class SourceService:
    def __init__(self, db: Session):
        self.db = db

    def observe(self, kind: str, identity: str, title: str, *, uri: str = None,
                local_path: str = None, mime_type: str = None, content: str = None,
                metadata: Dict[str, Any] = None, readable: Optional[bool] = None,
                error: str = None, active: bool = True, exclusive: bool = False,
                commit: bool = True) -> Source:
        key = stable_key(kind, identity)
        if exclusive:
            self.db.query(Source).filter(Source.kind == kind, Source.active.is_(True)) \
                .update({Source.active: False}, synchronize_session=False)

        record = self.db.query(Source).filter(Source.stable_key == key).first()
        if record is None:
            record = Source(kind=kind, stable_key=key, title=title or identity)
            self.db.add(record)

        record.title = title or record.title
        record.uri = uri or record.uri
        record.local_path = local_path or record.local_path
        record.mime_type = mime_type or record.mime_type
        if content is not None:
            record.content = content[:300_000]
            record.content_hash = hashlib.sha256(
                record.content.encode("utf-8", errors="replace")).hexdigest()
        if metadata is not None:
            record.metadata_json = json.dumps(metadata, default=str)
        record.readable = bool(content) if readable is None else bool(readable)
        record.active = active
        if record.excluded is None:
            record.excluded = False
        record.error = error
        record.last_seen = datetime.utcnow()
        if commit:
            self.db.commit()
            self.db.refresh(record)
        else:
            self.db.flush()
        return record

    def list(self, limit: int = 30, kind: str = None,
             active: Optional[bool] = None) -> List[Source]:
        query = self.db.query(Source)
        if kind:
            query = query.filter(Source.kind == kind)
        if active is not None:
            query = query.filter(Source.active.is_(active))
        return query.order_by(Source.active.desc(), Source.last_seen.desc()).limit(limit).all()

    def set_active(self, source_id: int, active: bool) -> Optional[Source]:
        record = self.db.query(Source).get(source_id)
        if not record:
            return None
        record.active = active
        record.excluded = not active
        self.db.commit()
        self.db.refresh(record)
        return record

    def forget(self, source_id: int) -> bool:
        record = self.db.query(Source).get(source_id)
        if not record:
            return False
        self.db.delete(record)
        self.db.commit()
        return True

    def context_for_query(self, query: str, limit: int = 6) -> List[Dict[str, Any]]:
        cutoff = datetime.utcnow() - timedelta(hours=8)
        sources = (self.db.query(Source)
                   .filter(Source.readable.is_(True), Source.content.isnot(None))
                   .filter(Source.excluded.is_(False))
                   .filter((Source.active.is_(True)) | (Source.last_seen >= cutoff))
                   .order_by(Source.active.desc(), Source.last_seen.desc())
                   .limit(12).all())
        candidates = []
        by_id = {source.id: source for source in sources}
        for source in sources:
            for chunk in text_chunks.split(source.content or ""):
                candidates.append({**chunk, "source_id": source.id})

        ranked = text_chunks.rank(query, candidates, limit=limit)
        results = []
        for index, item in enumerate(ranked, start=1):
            source = by_id[item["source_id"]]
            results.append({
                "ref": f"S{index}", "source_id": source.id,
                "title": source.title, "kind": source.kind,
                "uri": source.uri, "locator": item.get("locator") or "Beginning",
                "excerpt": item["content"][:1_000], "score": item.get("score"),
            })
        return results
