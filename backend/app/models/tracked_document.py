"""
Documents Cerebro has seen you working on.

Distinct from ``Document`` in the knowledge base: that is reference material you
deliberately indexed, this is the live working set — the spreadsheet open on your
second monitor, the Word doc a customer just sent, the SharePoint file you opened
from a browser tab. Cerebro reads these to understand what you are doing now.
"""

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class TrackedDocument(Base):
    __tablename__ = "tracked_documents"

    id = Column(Integer, primary_key=True, index=True)

    #: Absolute local path. The identity of the document as far as Cerebro is
    #: concerned — a SharePoint file synced by OneDrive has one of these too.
    path = Column(String, unique=True, index=True)
    name = Column(String, index=True)
    #: docx | xlsx | pptx | pdf | csv | text
    kind = Column(String, index=True)
    size_bytes = Column(Integer, nullable=True)

    #: How Cerebro learned about it: desktop_watcher, browser, api, agent.
    discovered_by = Column(String, nullable=True)
    #: Original SharePoint/OneDrive web URL, when it came from a browser tab.
    web_url = Column(String, nullable=True)

    #: Extracted plain text, capped. The structured form is re-read on demand so
    #: the database does not become a second copy of every spreadsheet.
    text_preview = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    #: Shape of the content: page/sheet/paragraph counts, as JSON.
    outline = Column(Text, nullable=True)

    case_id = Column(String, index=True, nullable=True)
    #: True once its text has been pushed into the searchable knowledge base.
    indexed = Column(Boolean, default=False)

    #: Modification time when last read, so a stale extract can be detected.
    content_mtime = Column(Float, nullable=True)
    read_error = Column(String, nullable=True)

    first_seen = Column(DateTime, default=func.now())
    last_seen = Column(DateTime, default=func.now(), onupdate=func.now(), index=True)
    last_edited_by_cerebro = Column(DateTime, nullable=True)

    def to_dict(self, include_preview: bool = False) -> dict:
        import json

        payload = {
            "id": self.id,
            "path": self.path,
            "name": self.name,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "discovered_by": self.discovered_by,
            "web_url": self.web_url,
            "summary": self.summary,
            "case_id": self.case_id,
            "indexed": bool(self.indexed),
            "read_error": self.read_error,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "last_edited_by_cerebro": (self.last_edited_by_cerebro.isoformat()
                                       if self.last_edited_by_cerebro else None),
        }
        try:
            payload["outline"] = json.loads(self.outline) if self.outline else None
        except ValueError:
            payload["outline"] = None
        if include_preview:
            payload["text_preview"] = self.text_preview
        return payload

    def __repr__(self):
        return f"<TrackedDocument {self.name}>"
