"""
Tool registry for Ask.

A tool is an ordinary function plus a description the model can read. Tools
register themselves with :func:`tool`; the agent loop offers every tool whose
``available`` check passes and calls the handler with the arguments the model
chose.

Every tool declares a *mode*, which is the safety contract:

``read``      looks something up; runs immediately.
``write``     changes only Cerebro's own local state (e.g. creates a task).
``draft``     prepares something for review; nothing leaves the machine.
``approval``  would change an external system — it is only ever *prepared*
              as a draft card, and runs when the user approves it.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

MODES = ("read", "write", "draft", "approval")


@dataclass
class ToolContext:
    """What a tool may use while it runs."""

    db: Any
    context: Dict[str, Any] = field(default_factory=dict)
    #: Called with a card dict to show progress in the UI as it happens.
    emit: Optional[Callable[[dict], None]] = None
    #: Sources gathered so far in this turn, in citation order.
    sources: List[dict] = field(default_factory=list)
    #: Draft/approval cards produced in this turn.
    drafts: List[dict] = field(default_factory=list)
    _counters: Dict[str, int] = field(default_factory=dict)

    def cite(self, source: dict, prefix: str = "S") -> str:
        """Register a source and return its citation ID, e.g. ``K2``.

        The same URI/title is cited once per turn, however many tools find it.
        """
        identity = (source.get("uri") or "", source.get("title") or "",
                    source.get("locator") or "")
        for existing in self.sources:
            if (existing.get("uri") or "", existing.get("title") or "",
                    existing.get("locator") or "") == identity:
                return existing["ref"]
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        ref = f"{prefix}{self._counters[prefix]}"
        self.sources.append({**source, "ref": ref})
        return ref

    def progress(self, title: str, detail: str = None, status: str = "running") -> None:
        if self.emit:
            self.emit({"type": "progress", "status": status,
                       "title": title, "detail": detail})


@dataclass
class Tool:
    name: str
    description: str
    handler: Callable[..., dict]
    parameters: Dict[str, Any]
    mode: str = "read"
    label: str = ""
    #: Activity state shown while it runs (see app.core.activity_state).
    activity: str = "searching"
    available: Optional[Callable[[ToolContext], bool]] = None
    #: Who added it at runtime ("mcp:jira", "site:wiki", …); "" for built-ins.
    owner: str = ""
    #: Short words find_tools matches on, besides the name and description.
    keywords: str = ""

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description,
                "parameters": self.parameters}

    def is_available(self, ctx: ToolContext) -> bool:
        if self.available is None:
            return True
        try:
            return bool(self.available(ctx))
        except Exception:  # noqa: BLE001 - an availability probe must never break Ask
            return False


REGISTRY: Dict[str, Tool] = {}


def tool(name: str, description: str, parameters: Dict[str, Any] = None,
         mode: str = "read", label: str = "", activity: str = "searching",
         available: Callable[[ToolContext], bool] = None):
    """Register ``handler(ctx, **arguments) -> dict`` as an Ask tool.

    The handler returns ``{"content": str}`` — the text the model reads —
    and may add ``"cards"`` (shown to the user) or ``"draft"`` (an approval
    card).
    """
    if mode not in MODES:
        raise ValueError(f"Unknown tool mode: {mode}")

    def register(handler: Callable[..., dict]) -> Callable[..., dict]:
        REGISTRY[name] = Tool(
            name=name, description=description, handler=handler,
            parameters=parameters or {"type": "object", "properties": {}},
            mode=mode, label=label or name.replace("_", " ").capitalize(),
            activity=activity, available=available)
        return handler

    return register


def string_param(description: str) -> dict:
    return {"type": "string", "description": description}


def bool_param(description: str) -> dict:
    return {"type": "boolean", "description": description}


def schema(required: List[str] = None, **properties: dict) -> dict:
    """Shorthand for an object JSON schema."""
    return {"type": "object", "properties": properties, "required": required or []}


def register_dynamic(item: Tool, owner: str) -> Tool:
    """Add a tool at runtime (an MCP server's, a website's) under ``owner``.

    Built-in tools are never replaced: an add-on tool whose name is taken gets
    the owner's name in front instead.
    """
    if item.mode not in MODES:
        raise ValueError(f"Unknown tool mode: {item.mode}")
    item.owner = owner
    existing = REGISTRY.get(item.name)
    if existing is not None and existing.owner != owner:
        item.name = safe_name(f"{owner.split(':', 1)[-1]}_{item.name}")
    REGISTRY[item.name] = item
    return item


def unregister_owner(owner: str) -> int:
    """Remove every tool ``owner`` added. Returns how many went."""
    names = [name for name, item in list(REGISTRY.items()) if item.owner == owner]
    for name in names:
        REGISTRY.pop(name, None)
    return len(names)


def owned_by(owner: str) -> List[Tool]:
    return [item for item in list(REGISTRY.values()) if item.owner == owner]


def safe_name(text: str, limit: int = 64) -> str:
    """A tool name every provider accepts: letters, digits, ``_`` and ``-``."""
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", text or "").strip("_") or "tool"
    return name[:limit]


def available_tools(ctx: ToolContext) -> List[Tool]:
    return [item for item in list(REGISTRY.values()) if item.is_available(ctx)]


_WORD = re.compile(r"[a-z0-9]+")


def search(query: str, tools: List[Tool], limit: int = 8) -> List[Tool]:
    """The tools best matching ``query`` by name, keywords and description."""
    words = {w for w in _WORD.findall((query or "").lower()) if len(w) > 1 or w.isdigit()}
    if not words:
        return []
    scored = []
    for item in tools:
        name = set(_WORD.findall(item.name.lower().replace("__", " ")))
        extra = set(_WORD.findall((item.keywords + " " + item.owner).lower()))
        text = set(_WORD.findall(item.description.lower()))
        score = 0.0
        for word in words:
            stem = word.rstrip("s")
            if word in name or any(n.startswith(stem) for n in name):
                score += 3
            if word in extra or any(e.startswith(stem) for e in extra):
                score += 2
            if word in text or any(t.startswith(stem) for t in text if len(stem) > 3):
                score += 1
        if score:
            scored.append((score, item.name, item))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [row[2] for row in scored[:limit]]


def catalog() -> List[dict]:
    return [{"name": item.name, "mode": item.mode, "label": item.label,
             "description": item.description, "owner": item.owner or None}
            for item in list(REGISTRY.values())]
