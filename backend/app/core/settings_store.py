"""
Editable-settings registry backing the Setup wizard and Settings screens.

The registry is the single source of truth for *what* a user may configure, how
it is labelled and validated, and whether it holds a secret. The web UI, the
``cerebro doctor`` CLI and the ``.env`` writer all read from it, so adding a new
option means adding one entry here rather than touching four files.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.core import logger
from app.core.config import Settings, settings
from app.core.paths import ENV_FILE

SECRET_MASK = "••••••••"
PASSWORD_MASK = "****"


def mask_url_password(url: str) -> str:
    """Replace the password in a connection URL with a placeholder."""
    if not url:
        return url
    try:
        password = urlparse(url).password
    except ValueError:
        return url
    return url.replace(f":{password}@", f":{PASSWORD_MASK}@") if password else url


def restore_url_password(new_url: str, previous_url: str) -> str:
    """Put the real password back when the UI returns a masked connection URL."""
    if not new_url or f":{PASSWORD_MASK}@" not in new_url:
        return new_url
    try:
        password = urlparse(previous_url or "").password
    except ValueError:
        password = None
    return new_url.replace(f":{PASSWORD_MASK}@", f":{password}@") if password else new_url


@dataclass
class Field:
    key: str
    label: str
    group: str
    help: str = ""
    type: str = "text"  # text | password | number | bool | select | url
    options: List[Dict[str, str]] = field(default_factory=list)
    placeholder: str = ""
    advanced: bool = False
    #: Connection URLs embed credentials; mask the password rather than serving
    #: it to the browser, and restore it when the UI sends the masked value back.
    masks_password: bool = False
    #: Only show this field when another field holds one of these values, e.g.
    #: ``show_if=("LLM_PROVIDER", ["openai"])``. Keeps each provider's settings
    #: out of sight until it is the one selected.
    show_if: Optional[tuple] = None

    @property
    def secret(self) -> bool:
        return self.type == "password"


GROUPS = [
    {
        "id": "general",
        "title": "General",
        "icon": "⚙️",
        "description": "How Cerebro identifies itself and where it listens.",
    },
    {
        "id": "database",
        "title": "Database",
        "icon": "🗄️",
        "description": "Where cases, events and context history are stored.",
    },
    {
        "id": "ai",
        "title": "AI Provider",
        "icon": "🧠",
        "description": "Optional. Powers summaries and troubleshooting suggestions.",
    },
    {
        "id": "knowledge",
        "title": "Knowledge Search",
        "icon": "📚",
        "description": "Vector store used for semantic search over your documents.",
    },
    {
        "id": "desktop",
        "title": "Desktop Capture",
        "icon": "🖥️",
        "description": "Optional Screenpipe integration for activity monitoring.",
    },
    {
        "id": "logging",
        "title": "Logging",
        "icon": "📝",
        "description": "Verbosity and destination of Cerebro's log file.",
    },
]

FIELDS: List[Field] = [
    # General
    Field("APP_NAME", "Application name", "general", "Shown in the UI and API docs."),
    Field("HOST", "Bind address", "general", "Use 127.0.0.1 to keep Cerebro local-only.", advanced=True),
    Field("PORT", "Port", "general", "Port the API listens on.", type="number"),
    Field("ENVIRONMENT", "Environment", "general", type="select", options=[
        {"value": "development", "label": "Development"},
        {"value": "production", "label": "Production"},
    ]),
    Field("DEBUG", "Debug mode", "general", "Verbose errors and auto-reload.", type="bool"),
    Field("CORS_ORIGINS", "Allowed origins", "general",
          "Comma-separated list, or * for any. Needed by the browser extension.",
          placeholder="*", advanced=True),

    # Database
    Field("DATABASE_URL", "Database URL", "database",
          "Leave the SQLite default for a single-machine install, or point at PostgreSQL.",
          type="url", placeholder="sqlite:///./data/cerebro.db", masks_password=True),
    Field("SQLALCHEMY_ECHO", "Log SQL statements", "database", type="bool", advanced=True),

    # AI
    Field("LLM_PROVIDER", "Provider", "ai",
          "Choose 'None' to run Cerebro without AI generation.", type="select", options=[
              {"value": "none", "label": "None — disable AI features"},
              {"value": "openai", "label": "OpenAI (or compatible API)"},
              {"value": "ollama", "label": "Ollama (local models)"},
              {"value": "qwen", "label": "Qwen"},
          ]),
    Field("OPENAI_API_KEY", "OpenAI API key", "ai", "Stored locally in backend/.env.",
          type="password", placeholder="sk-...", show_if=("LLM_PROVIDER", ["openai"])),
    Field("OPENAI_MODEL", "OpenAI model", "ai", placeholder="gpt-4o-mini",
          show_if=("LLM_PROVIDER", ["openai"])),
    Field("OPENAI_BASE_URL", "OpenAI base URL", "ai",
          "Override for Azure OpenAI or any OpenAI-compatible gateway.", type="url",
          advanced=True, show_if=("LLM_PROVIDER", ["openai"])),
    Field("OPENAI_ORG_ID", "OpenAI organisation", "ai", type="text", advanced=True,
          show_if=("LLM_PROVIDER", ["openai"])),
    Field("OLLAMA_URL", "Ollama URL", "ai", type="url", placeholder="http://localhost:11434",
          show_if=("LLM_PROVIDER", ["ollama"])),
    Field("OLLAMA_MODEL", "Ollama model", "ai", placeholder="llama3.1",
          show_if=("LLM_PROVIDER", ["ollama"])),
    Field("QWEN_API_URL", "Qwen API URL", "ai", type="url", show_if=("LLM_PROVIDER", ["qwen"])),
    Field("QWEN_API_KEY", "Qwen API key", "ai", type="password",
          show_if=("LLM_PROVIDER", ["qwen"])),
    Field("QWEN_MODEL", "Qwen model", "ai", placeholder="qwen-plus",
          show_if=("LLM_PROVIDER", ["qwen"])),
    Field("LLM_TEMPERATURE", "Temperature", "ai", type="number", advanced=True,
          show_if=("LLM_PROVIDER", ["openai", "ollama", "qwen"])),
    Field("LLM_MAX_TOKENS", "Max tokens", "ai", type="number", advanced=True,
          show_if=("LLM_PROVIDER", ["openai", "ollama", "qwen"])),
    Field("LLM_TIMEOUT", "Request timeout (s)", "ai", type="number", advanced=True,
          show_if=("LLM_PROVIDER", ["openai", "ollama", "qwen"])),

    # Knowledge
    Field("VECTOR_BACKEND", "Vector backend", "knowledge",
          "'Automatic' uses Qdrant when it is reachable and the built-in store otherwise.",
          type="select", options=[
              {"value": "auto", "label": "Automatic (recommended)"},
              {"value": "local", "label": "Built-in — no extra services"},
              {"value": "qdrant", "label": "Qdrant"},
          ]),
    Field("QDRANT_URL", "Qdrant URL", "knowledge", type="url", placeholder="http://localhost:6333",
          show_if=("VECTOR_BACKEND", ["auto", "qdrant"])),
    Field("QDRANT_API_KEY", "Qdrant API key", "knowledge", type="password",
          show_if=("VECTOR_BACKEND", ["auto", "qdrant"])),
    Field("EMBEDDING_PROVIDER", "Embeddings", "knowledge",
          "Built-in embeddings work offline; OpenAI embeddings match far more accurately.",
          type="select", options=[
              {"value": "local", "label": "Built-in — offline, no key needed"},
              {"value": "openai", "label": "OpenAI embeddings"},
          ]),
    Field("OPENAI_EMBEDDING_MODEL", "Embedding model", "knowledge",
          placeholder="text-embedding-3-small", advanced=True,
          show_if=("EMBEDDING_PROVIDER", ["openai"])),

    # Desktop
    Field("SCREENPIPE_ENABLED", "Enable Screenpipe", "desktop", type="bool"),
    Field("SCREENPIPE_URL", "Screenpipe URL", "desktop", type="url",
          placeholder="http://localhost:3030", show_if=("SCREENPIPE_ENABLED", [True])),

    # Logging
    Field("LOG_LEVEL", "Log level", "logging", type="select", options=[
        {"value": "DEBUG", "label": "Debug"},
        {"value": "INFO", "label": "Info"},
        {"value": "WARN", "label": "Warning"},
        {"value": "ERROR", "label": "Error"},
    ]),
    Field("CEREBRO_LOG_PATH", "Log file path", "logging",
          "Leave blank to use data/logs/cerebro.log.", advanced=True),
    Field("LOG_TO_STDOUT", "Also log to console", "logging", type="bool", advanced=True),
]

FIELDS_BY_KEY = {f.key: f for f in FIELDS}

# Keys that may be written to .env but are not user-facing form fields.
INTERNAL_KEYS = {"SETUP_COMPLETED"}


def _coerce(key: str, raw: Any) -> Any:
    """Convert a form value into the type declared on the Settings model."""
    annotation = Settings.model_fields[key].annotation
    text = str(raw).strip() if raw is not None else ""

    origin_is_optional = "Optional" in str(annotation) or "None" in str(annotation)
    if text == "" and origin_is_optional:
        return None

    if annotation is bool or "bool" in str(annotation):
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if "int" in str(annotation):
        return int(float(text))
    if "float" in str(annotation):
        return float(text)
    return text


def describe(include_values: bool = True) -> Dict[str, Any]:
    """Full description of the editable configuration for the settings UI."""
    payload = {"groups": GROUPS, "fields": [], "env_file": str(ENV_FILE)}

    for f in FIELDS:
        entry = {
            "key": f.key,
            "label": f.label,
            "group": f.group,
            "help": f.help,
            "type": f.type,
            "options": f.options,
            "placeholder": f.placeholder,
            "advanced": f.advanced,
            "secret": f.secret,
            "show_if": ({"key": f.show_if[0], "values": [str(v).lower() if isinstance(v, bool) else v
                                                          for v in f.show_if[1]]}
                        if f.show_if else None),
        }
        if include_values:
            value = getattr(settings, f.key, None)
            if f.secret:
                entry["value"] = SECRET_MASK if value else ""
                entry["is_set"] = bool(value)
            elif f.masks_password:
                entry["value"] = mask_url_password(value or "")
            else:
                entry["value"] = "" if value is None else value
        payload["fields"].append(entry)

    return payload


def _format(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _is_default(key: str, value: Any) -> bool:
    """True when ``value`` matches the built-in default for ``key``."""
    field_info = Settings.model_fields.get(key)
    if field_info is None:
        return False
    default = field_info.default
    if callable(default):  # default_factory-style callables
        return False
    return _format(default) == _format(value)


def _render_env(values: Dict[str, Any]) -> str:
    """
    Render ``.env`` grouped, commented and hand-editable.

    Settings still at their built-in default are written as comments showing that
    default. The result doubles as documentation: uncommented lines are exactly
    the things this install has overridden, and every other option is visible
    right there with its default value if the user wants to change it.
    """
    lines = [
        "# Cerebro configuration",
        "# Written by the Cerebro setup UI — safe to hand-edit.",
        "#",
        "# Commented-out lines show the built-in default. Uncomment to override.",
        "# Delete this file entirely to reset Cerebro to its defaults.",
        "",
    ]

    by_group: Dict[str, List[str]] = {group["id"]: [] for group in GROUPS}
    for f in FIELDS:
        if f.key not in values:
            continue
        entry = f"{f.key}={_format(values[f.key])}"
        if _is_default(f.key, values[f.key]):
            entry = f"# {entry}"
        by_group[f.group].append(entry)

    for group in GROUPS:
        entries = by_group.get(group["id"]) or []
        if not entries:
            continue
        lines.append(f"# --- {group['title']} " + "-" * max(0, 56 - len(group["title"])))
        lines.extend(entries)
        lines.append("")

    for key in sorted(INTERNAL_KEYS):
        if key in values:
            lines.append(f"{key}={_format(values[key])}")

    return "\n".join(lines).rstrip() + "\n"


def current_values() -> Dict[str, Any]:
    values = {f.key: getattr(settings, f.key, None) for f in FIELDS}
    for key in INTERNAL_KEYS:
        values[key] = getattr(settings, key, None)
    return values


def update(changes: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and persist configuration changes.

    Secret fields left at the mask value are treated as "unchanged" so the UI
    never has to round-trip an API key. Returns a report describing what was
    written and whether a restart is needed.
    """
    values = current_values()
    applied: List[str] = []
    errors: Dict[str, str] = {}

    for key, raw in changes.items():
        if key not in FIELDS_BY_KEY and key not in INTERNAL_KEYS:
            continue

        field_def = FIELDS_BY_KEY.get(key)
        if field_def and field_def.secret and raw == SECRET_MASK:
            continue
        if field_def and field_def.masks_password:
            raw = restore_url_password(str(raw or ""), str(values.get(key) or ""))

        try:
            coerced = _coerce(key, raw)
        except (TypeError, ValueError):
            errors[key] = f"'{raw}' is not a valid value for {key}."
            continue

        if values.get(key) != coerced:
            values[key] = coerced
            applied.append(key)

    if errors:
        return {"ok": False, "errors": errors, "applied": [], "restart_required": False}

    # Validate the whole set before touching disk, so a bad value can never
    # leave the user with a .env that refuses to load.
    try:
        Settings(**{k: v for k, v in values.items() if v is not None})
    except Exception as exc:
        return {"ok": False, "errors": {"_": str(exc)}, "applied": [], "restart_required": False}

    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text(_render_env(values), encoding="utf-8")

    # Apply in-memory so AI/knowledge changes take effect without a restart.
    for key in applied:
        try:
            object.__setattr__(settings, key, values[key])
        except Exception:
            pass

    # The vector backend is probed once and cached; knowledge changes must
    # invalidate it or the UI would report a stale backend until a restart.
    if {"VECTOR_BACKEND", "QDRANT_URL", "QDRANT_API_KEY", "EMBEDDING_PROVIDER",
            "OPENAI_EMBEDDING_MODEL"}.intersection(applied):
        try:
            from app.services.rag_service import reset_backend_cache

            reset_backend_cache()
        except Exception:  # pragma: no cover - service layer is optional here
            pass

    restart_keys = {"DATABASE_URL", "HOST", "PORT", "DEBUG", "CORS_ORIGINS", "SQLALCHEMY_ECHO"}
    restart_required = bool(restart_keys.intersection(applied))

    logger.info("settings", "Configuration updated", {"keys": applied, "restart": restart_required})

    return {
        "ok": True,
        "errors": {},
        "applied": applied,
        "restart_required": restart_required,
        "env_file": str(ENV_FILE),
    }


def mark_setup_complete() -> None:
    update({"SETUP_COMPLETED": True})


def ensure_env_file() -> Optional[str]:
    """Create ``backend/.env`` from the current defaults if it does not exist."""
    if ENV_FILE.exists():
        return None
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text(_render_env(current_values()), encoding="utf-8")
    return str(ENV_FILE)
