"""
Text embeddings.

The built-in embedder is a hashed bag-of-words projection: no model download, no
API key, no network — and, unlike a plain content hash, it produces vectors whose
cosine similarity actually tracks word overlap. It is good enough for "find the
KB article about this error code" and it makes knowledge search work on a fresh
install. Users who want real semantic matching can switch to OpenAI embeddings
in Settings → Knowledge Search.
"""

import hashlib
import math
import re
from typing import List, Tuple

from app.core import logger
from app.core.config import settings

_TOKEN_RE = re.compile(r"[a-z0-9_]+")

# Words carrying no retrieval signal in support content.
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "has",
    "have", "in", "is", "it", "its", "of", "on", "or", "that", "the", "this",
    "to", "was", "were", "when", "which", "with", "you", "your",
}

LOCAL_DIM = 512


def _tokenize(text: str) -> List[str]:
    tokens = [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS]
    # Character trigrams for short/identifier-like queries (error codes, case ids)
    # so "0x80040115" still matches a document that mentions it inside a sentence.
    extra = []
    for token in tokens:
        if len(token) >= 6 and any(ch.isdigit() for ch in token):
            extra.extend(token[i:i + 3] for i in range(len(token) - 2))
    return tokens + extra


def _bucket(token: str) -> int:
    return int(hashlib.blake2b(token.encode("utf-8"), digest_size=4).hexdigest(), 16) % LOCAL_DIM


def local_embedding(text: str) -> List[float]:
    """Hashed bag-of-words vector with sublinear term frequency, L2-normalised."""
    vector = [0.0] * LOCAL_DIM
    counts = {}
    for token in _tokenize(text):
        counts[token] = counts.get(token, 0) + 1

    for token, count in counts.items():
        vector[_bucket(token)] += 1.0 + math.log(count)

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def _openai_embedding(text: str) -> List[float]:
    from openai import OpenAI

    client = OpenAI(
        api_key=settings.OPENAI_API_KEY,
        organization=settings.OPENAI_ORG_ID or None,
        base_url=settings.OPENAI_BASE_URL or None,
        timeout=settings.LLM_TIMEOUT,
    )
    response = client.embeddings.create(
        model=settings.OPENAI_EMBEDDING_MODEL,
        input=text[:8000] or " ",
    )
    return list(response.data[0].embedding)


def active_provider() -> str:
    """Which embedder will actually be used, accounting for missing credentials."""
    if settings.EMBEDDING_PROVIDER.lower() == "openai" and settings.OPENAI_API_KEY:
        return "openai"
    return "local"


def dimension() -> int:
    if active_provider() == "openai":
        # text-embedding-3-small = 1536, -3-large = 3072, ada-002 = 1536
        return 3072 if "large" in settings.OPENAI_EMBEDDING_MODEL else 1536
    return LOCAL_DIM


def signature() -> str:
    """Identifies the embedding space; changing it invalidates stored vectors."""
    provider = active_provider()
    model = settings.OPENAI_EMBEDDING_MODEL if provider == "openai" else "hashed-bow"
    return f"{provider}:{model}:{dimension()}"


def local_signature() -> str:
    return f"local:hashed-bow:{LOCAL_DIM}"


def embed_with_signature(text: str) -> Tuple[List[float], str]:
    """
    Embed ``text`` and report which embedding space the result belongs to.

    The signature is derived from what actually produced the vector, not from
    what was configured: a transient OpenAI failure falls back to the built-in
    embedder, and the vector must be labelled — and later matched — as local, or
    it would be silently compared against 1536-dimension vectors and score zero.
    """
    if active_provider() == "openai":
        try:
            return _openai_embedding(text), signature()
        except Exception as exc:
            logger.warn("embeddings", "OpenAI embedding failed, using built-in",
                        {"error": str(exc)})
    return local_embedding(text), local_signature()


def embed(text: str) -> List[float]:
    """Embed ``text``, falling back to the built-in embedder on any failure."""
    return embed_with_signature(text)[0]


def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
