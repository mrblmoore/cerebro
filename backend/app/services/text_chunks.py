"""Turn long sources into small, attributable retrieval units."""

import re
from typing import Dict, Iterable, List

from app.services import embeddings

TARGET_CHARS = 1_400
OVERLAP_CHARS = 180
_LOCATOR = re.compile(
    r"^#{1,3}\s+(?P<label>(?:Page|Slide|Sheet)\s*:?\s*[^\n]+|[^\n]{1,100})$",
    re.IGNORECASE,
)


def split(text: str, target: int = TARGET_CHARS) -> List[Dict[str, str]]:
    """Split on structural headings first, then bounded paragraph windows."""
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return []

    sections = []
    locator = "Beginning"
    buffer: List[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append((locator, body))
        buffer.clear()

    for line in text.splitlines():
        match = _LOCATOR.match(line.strip())
        if match:
            flush()
            locator = match.group("label").strip().rstrip(":")
        else:
            buffer.append(line)
    flush()

    chunks: List[Dict[str, str]] = []
    for section_locator, body in sections or [("Beginning", text)]:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
        current = ""
        for paragraph in paragraphs or [body]:
            if current and len(current) + len(paragraph) + 2 > target:
                chunks.append({"locator": section_locator, "content": current.strip()})
                current = current[-OVERLAP_CHARS:] + "\n\n" + paragraph
            else:
                current = f"{current}\n\n{paragraph}" if current else paragraph
            while len(current) > target * 2:
                chunks.append({"locator": section_locator, "content": current[:target]})
                current = current[target - OVERLAP_CHARS:]
        if current.strip():
            chunks.append({"locator": section_locator, "content": current.strip()})
    return chunks


def rank(query: str, chunks: Iterable[Dict[str, str]], limit: int = 6) -> List[Dict[str, str]]:
    """Rank transient chunks locally without network calls or new credentials."""
    query_vector = embeddings.local_embedding(query)
    scored = []
    for chunk in chunks:
        content = chunk.get("content") or ""
        score = embeddings.cosine(query_vector, embeddings.local_embedding(content))
        if score > 0:
            scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [{**chunk, "score": round(score, 4)} for score, chunk in scored[:limit]]

