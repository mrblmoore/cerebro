"""
RAG Service — document indexing and semantic search.

Two interchangeable backends sit behind one interface:

* **built-in** (default) stores vectors alongside the document row, so search
  works immediately on a fresh install with nothing else running;
* **Qdrant** is used when a URL is configured and the client library is present.

``VECTOR_BACKEND=auto`` picks Qdrant when it is genuinely reachable and quietly
falls back to the built-in store otherwise — an unreachable Qdrant degrades
search quality, it never breaks the app.
"""

import json
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core import logger
from app.core.config import settings
from app.models.document import Document
from app.services import embeddings

COLLECTION_PREFIX = "cerebro_documents"

#: Probing Qdrant costs a network round trip, and a RAGService is constructed
#: per request — so the resolved backend and client are cached here and reused
#: until the relevant settings change.
_CACHE = {"key": None, "backend": None, "client": None}


def reset_backend_cache() -> None:
    """Forget the cached backend. Called when knowledge settings are saved."""
    _CACHE.update({"key": None, "backend": None, "client": None})


class RAGService:
    def __init__(self, db: Session):
        self.db = db
        self._backend = self._resolve_backend()

    @property
    def _cache_key(self) -> tuple:
        return ((settings.VECTOR_BACKEND or "auto").lower(),
                settings.QDRANT_URL, settings.QDRANT_API_KEY, embeddings.signature())

    @property
    def _qdrant(self):
        return _CACHE["client"]

    # ---------------------------------------------------------- backends
    @property
    def backend(self) -> str:
        return self._backend

    @property
    def collection_name(self) -> str:
        # The embedding signature is part of the name so switching embedding
        # providers cannot mix incompatible vectors in one collection.
        suffix = embeddings.signature().replace(":", "_").replace(".", "_")
        return f"{COLLECTION_PREFIX}_{suffix}"

    def _resolve_backend(self) -> str:
        if _CACHE["key"] == self._cache_key:
            return _CACHE["backend"]

        preference = (settings.VECTOR_BACKEND or "auto").lower()
        backend = "local"

        if preference != "local" and settings.QDRANT_URL:
            if self._connect_qdrant() is not None:
                backend = "qdrant"
            elif preference == "qdrant":
                logger.warn("rag_service",
                            "Qdrant requested but unavailable; using built-in store")

        _CACHE.update({"key": self._cache_key, "backend": backend})
        return backend

    def _connect_qdrant(self):
        if _CACHE["client"] is not None and _CACHE["key"] == self._cache_key:
            return _CACHE["client"]
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams
        except ImportError:
            logger.info("rag_service", "qdrant-client not installed; using built-in vector store")
            return None

        try:
            client = QdrantClient(
                url=settings.QDRANT_URL,
                api_key=settings.QDRANT_API_KEY or None,
                timeout=5,
            )
            existing = {c.name for c in client.get_collections().collections}
            if self.collection_name not in existing:
                client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=embeddings.dimension(), distance=Distance.COSINE
                    ),
                )
            _CACHE["client"] = client
            return client
        except Exception as exc:
            logger.warn("rag_service", "Qdrant unreachable; using built-in vector store",
                        {"error": str(exc), "url": settings.QDRANT_URL})
            return None

    # ------------------------------------------------------------ status
    def status(self) -> Dict[str, Any]:
        count = self.db.query(Document).count()
        return {
            "ok": True,
            "backend": self._backend,
            "embeddings": embeddings.signature(),
            "documents": count,
            "detail": (
                f"{'Qdrant' if self._backend == 'qdrant' else 'Built-in store'} · "
                f"{count} document{'s' if count != 1 else ''} indexed"
            ),
        }

    # ----------------------------------------------------------- writing
    def index_document(self, document: Dict[str, Any]) -> Document:
        content = document.get("content") or ""
        title = document.get("title") or "Untitled"
        if not content.strip():
            raise ValueError("Document content is empty — nothing to index.")

        vector_id = str(uuid.uuid4())
        vector, signature = embeddings.embed_with_signature(f"{title}\n\n{content}")

        logger.info("rag_service", "Indexing document",
                    {"title": title, "source": document.get("source"), "backend": self._backend})

        db_document = Document(
            source=document.get("source") or "manual",
            title=title,
            content=content,
            url=document.get("url"),
            vector_id=vector_id,
            embedding=json.dumps(vector) if self._backend == "local" else None,
            embedding_signature=signature,
            tags=",".join(document.get("tags", []) or []),
            indexed=True,
        )
        self.db.add(db_document)
        self.db.commit()
        self.db.refresh(db_document)

        if self._backend == "qdrant" and not self._upsert_qdrant(db_document, vector):
            # The row is already saved; keep the vector locally so it stays findable.
            db_document.embedding = json.dumps(vector)
            self.db.commit()

        return db_document

    def _upsert_qdrant(self, document: Document, vector: List[float]) -> bool:
        """Push one document's vector to Qdrant. False means it did not land."""
        client = self._connect_qdrant()
        if client is None:
            return False
        try:
            from qdrant_client.models import PointStruct

            client.upsert(
                collection_name=self.collection_name,
                points=[PointStruct(
                    id=document.vector_id,
                    vector=vector,
                    payload={
                        "title": document.title,
                        "source": document.source,
                        "url": document.url,
                        "vector_id": document.vector_id,
                    },
                )],
            )
            return True
        except Exception as exc:
            logger.warn("rag_service", "Qdrant upsert failed; storing vector locally",
                        {"error": str(exc), "document": document.id})
            return False

    def delete_document(self, document: Document) -> None:
        """Remove a document from both the database and the vector store."""
        if self._backend == "qdrant" and document.vector_id:
            client = self._connect_qdrant()
            if client is not None:
                try:
                    from qdrant_client.models import PointIdsList

                    client.delete(collection_name=self.collection_name,
                                  points_selector=PointIdsList(points=[document.vector_id]))
                except Exception as exc:
                    logger.warn("rag_service", "Qdrant delete failed",
                                {"error": str(exc), "document": document.id})
        self.db.delete(document)
        self.db.commit()

    def reindex_all(self, force: bool = False) -> Dict[str, Any]:
        """
        Re-embed every document.

        Needed after switching embedding provider: existing vectors belong to the
        previous embedding space and would be ignored by search. When Qdrant is
        the backend the freshly named collection starts empty, so every document
        is pushed there as well — a reindex that only touched the database would
        leave Qdrant search returning nothing.
        """
        documents = self.db.query(Document).all()
        target = embeddings.signature()
        updated = 0

        for document in documents:
            up_to_date = document.embedding_signature == target and (
                document.embedding or self._backend == "qdrant")
            if up_to_date and not force:
                continue

            vector, signature = embeddings.embed_with_signature(
                f"{document.title}\n\n{document.content or ''}")
            document.embedding_signature = signature

            pushed = self._backend == "qdrant" and self._upsert_qdrant(document, vector)
            # Keep a local copy unless Qdrant accepted it, so search always works.
            document.embedding = None if pushed else json.dumps(vector)
            updated += 1

        self.db.commit()
        logger.info("rag_service", "Reindex complete",
                    {"updated": updated, "total": len(documents), "backend": self._backend})
        return {"ok": True, "updated": updated, "total": len(documents),
                "backend": self._backend}

    # ----------------------------------------------------------- reading
    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        if not query or not query.strip():
            return []

        try:
            if self._backend == "qdrant":
                results = self._search_qdrant(query, limit)
                if results:
                    return results
            return self._search_local(query, limit)
        except Exception as exc:
            logger.error("rag_service", "Search failed", {"error": str(exc)})
            return []

    def _search_qdrant(self, query: str, limit: int) -> List[Dict[str, Any]]:
        client = self._connect_qdrant()
        if client is None:
            return []

        hits = client.search(
            collection_name=self.collection_name,
            query_vector=embeddings.embed(query),
            limit=limit,
        )
        results = []
        for hit in hits:
            vector_id = (hit.payload or {}).get("vector_id")
            document = (
                self.db.query(Document).filter(Document.vector_id == vector_id).first()
                if vector_id else None
            )
            if document:
                results.append(self._format(document, hit.score))
        return results

    def _search_local(self, query: str, limit: int) -> List[Dict[str, Any]]:
        # Embed the query first and compare against that same space, so a
        # provider outage degrades the whole search to the built-in embedder
        # rather than silently matching nothing.
        query_vector, signature = embeddings.embed_with_signature(query)

        scored = []
        for document in self.db.query(Document).all():
            vector = self._vector_for(document, signature)
            if vector is None:
                continue
            score = embeddings.cosine(query_vector, vector)
            if score > 0:
                scored.append((score, document))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [self._format(document, score) for score, document in scored[:limit]]

    def _vector_for(self, document: Document, signature: str) -> Optional[List[float]]:
        """Stored vector, re-embedding lazily if it is missing or stale."""
        if document.embedding and document.embedding_signature == signature:
            try:
                return json.loads(document.embedding)
            except (TypeError, ValueError):
                pass

        vector, produced = embeddings.embed_with_signature(
            f"{document.title}\n\n{document.content or ''}")
        document.embedding = json.dumps(vector)
        document.embedding_signature = produced
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
        return vector

    @staticmethod
    def _format(document: Document, score: Optional[float]) -> Dict[str, Any]:
        content = document.content or ""
        return {
            "id": document.id,
            "title": document.title,
            "source": document.source,
            "url": document.url,
            "score": round(float(score), 4) if score is not None else None,
            "excerpt": content[:300] + ("…" if len(content) > 300 else ""),
            "tags": [tag for tag in (document.tags or "").split(",") if tag],
        }
