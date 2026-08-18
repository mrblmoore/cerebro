"""
RAG Service - Retrieval-Augmented Generation for knowledge search.
Indexes documents and performs semantic search.
"""

from sqlalchemy.orm import Session
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from app.models.document import Document
from app.core.config import settings
from typing import List, Dict, Any
import uuid


class RAGService:
    def __init__(self, db: Session):
        self.db = db
        self.qdrant = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)
        self.collection_name = "cerebrus_documents"
        self.embedding_dim = 1536  # OpenAI embedding dimension
        self._init_collection()
    
    def _init_collection(self):
        """Initialize Qdrant collection if it doesn't exist."""
        try:
            self.qdrant.get_collection(self.collection_name)
        except Exception:
            # Collection doesn't exist, create it
            self.qdrant.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=self.embedding_dim, distance=Distance.COSINE),
            )
    
    def index_document(self, document: Dict[str, Any]) -> Document:
        """
        Index a document in Qdrant and store metadata in PostgreSQL.
        """
        from app.core import logger
        vector_id = str(uuid.uuid4())
        logger.info('rag_service', 'Indexing document', {'title': document.get('title'), 'source': document.get('source')})
        try:
            # In production, use OpenAI embeddings
            # For MVP, use a dummy embedding
            embedding = self._get_embedding(document.get("content", ""))
            
            # Store in Qdrant
            point = PointStruct(
                id=hash(vector_id) % (2**31),
                vector=embedding,
                payload={
                    "title": document.get("title"),
                    "source": document.get("source"),
                    "content": document.get("content")[:500],  # Truncate for payload
                    "vector_id": vector_id
                }
            )
            self.qdrant.upsert(
                collection_name=self.collection_name,
                points=[point]
            )
            logger.info('rag_service', 'Upserted point to Qdrant', {'vector_id': vector_id})
            
            # Store metadata in PostgreSQL
            db_document = Document(
                source=document.get("source"),
                title=document.get("title"),
                content=document.get("content"),
                url=document.get("url"),
                vector_id=vector_id,
                tags=",".join(document.get("tags", [])),
                indexed=True
            )
            self.db.add(db_document)
            self.db.commit()
            
            logger.info('rag_service', 'Stored document metadata', {'doc_id': db_document.id})
            return db_document
        except Exception as e:
            logger.error('rag_service', 'Failed to index document', {'error': str(e)})
            raise
    
    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Search for relevant documents using semantic search.
        """
        from app.core import logger
        logger.info('rag_service', 'Search requested', {'query_preview': query[:200], 'limit': limit})
        try:
            query_embedding = self._get_embedding(query)
            
            results = self.qdrant.search(
                collection_name=self.collection_name,
                query_vector=query_embedding,
                limit=limit
            )
            logger.info('rag_service', 'Qdrant search completed', {'num_results': len(results)})
            
            documents = []
            for result in results:
                # result.payload might contain vector_id if we stored it
                vector_id = result.payload.get('vector_id') or result.payload.get('id')
                doc = None
                if vector_id:
                    doc = self.db.query(Document).filter(Document.vector_id == vector_id).first()
                if not doc:
                    # best-effort: look by title
                    title = result.payload.get('title')
                    if title:
                        doc = self.db.query(Document).filter(Document.title == title).first()
                
                if doc:
                    documents.append({
                        "title": doc.title,
                        "source": doc.source,
                        "url": doc.url,
                        "score": getattr(result, 'score', None),
                        "excerpt": doc.content[:300]
                    })
            return documents
        except Exception as e:
            logger.error('rag_service', 'Search failed', {'error': str(e)})
            return []
    
    def _get_embedding(self, text: str) -> List[float]:
        """
        Get embedding for text. In production, use OpenAI API.
        For MVP, return a simple hash-based embedding.
        """
        # TODO: Replace with actual OpenAI embeddings in production
        import hashlib
        hash_val = int(hashlib.md5(text.encode()).hexdigest(), 16)
        
        # Generate deterministic embeddings for MVP
        embedding = []
        for i in range(self.embedding_dim):
            embedding.append(((hash_val >> (i % 32)) & 1) * 2.0 - 1.0)
        
        return embedding
