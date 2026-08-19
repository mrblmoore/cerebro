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
    type: str = "text"  # text | password | number | bool | select | url | model
    options: List[Dict[str, str]] = field(default_factory=list)
    placeholder: str = ""
    #: For ``type="model"``: which provider's catalogue to offer. The browser
    #: renders a dropdown and asks /api/system/models to replace the built-in
    #: list with the models this account can actually use.
    model_provider: str = ""
    advanced: bool = False
    #: Connection URLs embed credentials; mask the password rather than serving
    #: it to the browser, and restore it when the UI sends the masked value back.
    masks_password: bool = False
    #: Only show this field when another field holds one of these values, e.g.
    #: ``show_if=("LLM_PROVIDER", ["openai"])``. Keeps each provider's settings
    #: out of sight until it is the one selected.
    show_if: Optional[tuple] = None
    #: All conditions must match. Used for provider-specific fields that also
    #: depend on a second choice, such as Bedrock's credential mode.
    show_if_all: List[tuple] = field(default_factory=list)

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
        "id": "enterprise",
        "title": "Outlook & Teams",
        "icon": "📨",
        "description": "Power Automate drops Outlook and Teams messages into a folder; "
                       "Cerebro reads it. No Microsoft credentials are stored here.",
    },
    {
        "id": "documents",
        "title": "Documents",
        "icon": "📄",
        "description": "Which documents Cerebro may read, and how it handles edits.",
    },
    {
        "id": "brain",
        "title": "Second Brain",
        "icon": "🧠",
        "description": "Memory, writing voice, and how Cerebro speaks to you.",
    },
    {
        "id": "secretary",
        "title": "Secretary",
        "icon": "🗒️",
        "description": "Tasks Cerebro runs for you, and the nudges it raises.",
    },
    {
        "id": "copilot",
        "title": "Microsoft Copilot",
        "icon": "🤝",
        "description": "Optional. Share what Cerebro knows with a Copilot Studio "
                       "agent, so you can reach it from Teams or your phone. "
                       "Cerebro works fully without this.",
    },
    {
        "id": "capture",
        "title": "Activity Capture",
        "icon": "👁️",
        "description": "Optional. Screenshots and typed text, so Cerebro learns "
                       "from what it watches. Off by default; talk to IT before enabling.",
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
              {"value": "bedrock", "label": "Amazon Bedrock"},
          ]),
    Field("OPENAI_API_KEY", "OpenAI API key", "ai", "Stored locally in backend/.env.",
          type="password", placeholder="sk-...", show_if=("LLM_PROVIDER", ["openai"])),
    Field("OPENAI_MODEL", "OpenAI model", "ai",
          "Pick from the list, or choose Custom to type an ID.",
          type="model", model_provider="openai", placeholder="gpt-4o-mini",
          show_if=("LLM_PROVIDER", ["openai"])),
    Field("OPENAI_BASE_URL", "OpenAI base URL", "ai",
          "Override for Azure OpenAI or any OpenAI-compatible gateway.", type="url",
          advanced=True, show_if=("LLM_PROVIDER", ["openai"])),
    Field("OPENAI_ORG_ID", "OpenAI organisation", "ai", type="text", advanced=True,
          show_if=("LLM_PROVIDER", ["openai"])),
    Field("OLLAMA_URL", "Ollama URL", "ai", type="url", placeholder="http://localhost:11434",
          show_if=("LLM_PROVIDER", ["ollama"])),
    Field("OLLAMA_MODEL", "Ollama model", "ai",
          "Refresh to list the models you have pulled locally.",
          type="model", model_provider="ollama", placeholder="llama3.1",
          show_if=("LLM_PROVIDER", ["ollama"])),
    Field("QWEN_API_URL", "Qwen API URL", "ai", type="url", show_if=("LLM_PROVIDER", ["qwen"])),
    Field("QWEN_API_KEY", "Qwen API key", "ai", type="password",
          show_if=("LLM_PROVIDER", ["qwen"])),
    Field("QWEN_MODEL", "Qwen model", "ai",
          "Pick from the list, or choose Custom to type an ID.",
          type="model", model_provider="qwen", placeholder="qwen-plus",
          show_if=("LLM_PROVIDER", ["qwen"])),
    Field("BEDROCK_REGION", "AWS Region", "ai",
          "The Region where Cerebro sends Bedrock Runtime requests.",
          placeholder="us-east-1", show_if=("LLM_PROVIDER", ["bedrock"])),
    Field("BEDROCK_MODEL_ID", "Model", "ai",
          "Press Refresh to list the models your AWS account has enabled in this "
          "Region — every entry in that list is guaranteed to work.",
          type="model", model_provider="bedrock",
          placeholder="us.anthropic.claude-sonnet-4-20250514-v1:0",
          show_if=("LLM_PROVIDER", ["bedrock"])),
    Field("BEDROCK_AUTH_MODE", "How to sign in to AWS", "ai",
          "AWS has no single API key like OpenAI — it signs each request with an "
          "identity. Pick 'Bedrock API key' for the simplest setup, or use an "
          "identity this computer already has.",
          type="select", options=[
              {"value": "default",
               "label": "Use the AWS sign-in already on this computer (recommended)"},
              {"value": "api_key", "label": "Bedrock API key — paste a single key"},
              {"value": "profile", "label": "Named AWS profile"},
              {"value": "keys", "label": "AWS access key ID and secret"},
          ], show_if=("LLM_PROVIDER", ["bedrock"])),
    Field("BEDROCK_API_KEY", "Bedrock API key", "ai",
          "Create one in the Amazon Bedrock console under API keys. Stored locally "
          "in backend/.env and never returned by the settings API.",
          type="password", placeholder="ABSK...",
          show_if_all=[("LLM_PROVIDER", ["bedrock"]),
                       ("BEDROCK_AUTH_MODE", ["api_key"])]),
    Field("BEDROCK_AWS_PROFILE", "AWS profile name", "ai",
          "Profile from your shared AWS config or credentials file.", placeholder="default",
          show_if_all=[("LLM_PROVIDER", ["bedrock"]),
                       ("BEDROCK_AUTH_MODE", ["profile"])]),
    Field("BEDROCK_AWS_ACCESS_KEY_ID", "AWS access key ID", "ai",
          "Use temporary credentials where possible. Stored locally in backend/.env.",
          type="password", placeholder="AKIA...",
          show_if_all=[("LLM_PROVIDER", ["bedrock"]),
                       ("BEDROCK_AUTH_MODE", ["keys"])]),
    Field("BEDROCK_AWS_SECRET_ACCESS_KEY", "AWS secret access key", "ai",
          "Stored locally in backend/.env and never returned by the settings API.",
          type="password", show_if_all=[("LLM_PROVIDER", ["bedrock"]),
                                         ("BEDROCK_AUTH_MODE", ["keys"])]),
    Field("BEDROCK_AWS_SESSION_TOKEN", "AWS session token", "ai",
          "Required only for temporary access-key credentials.", type="password",
          show_if_all=[("LLM_PROVIDER", ["bedrock"]),
                       ("BEDROCK_AUTH_MODE", ["keys"])]),
    Field("BEDROCK_ENDPOINT_URL", "Bedrock Runtime endpoint override", "ai",
          "Optional custom or VPC endpoint URL.", type="url", advanced=True,
          show_if=("LLM_PROVIDER", ["bedrock"])),
    Field("LLM_TEMPERATURE", "Temperature", "ai", type="number", advanced=True,
          show_if=("LLM_PROVIDER", ["openai", "ollama", "qwen", "bedrock"])),
    Field("LLM_MAX_TOKENS", "Max tokens", "ai", type="number", advanced=True,
          show_if=("LLM_PROVIDER", ["openai", "ollama", "qwen", "bedrock"])),
    Field("LLM_TIMEOUT", "Request timeout (s)", "ai", type="number", advanced=True,
          show_if=("LLM_PROVIDER", ["openai", "ollama", "qwen", "bedrock"])),

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
    Field("EMBEDDING_PROVIDER", "Knowledge-search embedding engine", "knowledge",
          "Separate from your chosen AI model. The built-in engine works offline; an OpenAI-compatible embedding API is optional.",
          type="select", options=[
              {"value": "local", "label": "Built-in — offline, no key needed"},
              {"value": "openai", "label": "OpenAI-compatible embedding API"},
          ]),
    Field("OPENAI_EMBEDDING_MODEL", "Embedding model", "knowledge",
          "Embedding models are a separate family — a chat model ID is rejected here.",
          type="model", model_provider="openai_embedding",
          placeholder="text-embedding-3-small", advanced=True,
          show_if=("EMBEDDING_PROVIDER", ["openai"])),

    # Enterprise bridge
    Field("ENTERPRISE_ENABLED", "Enable the Outlook/Teams bridge", "enterprise",
          "Cerebro watches the inbox folder and ingests each message Power Automate writes.",
          type="bool"),
    Field("ENTERPRISE_INBOX_DIR", "Inbox folder", "enterprise",
          "Where your Power Automate flow writes message JSON files. Usually inside OneDrive.",
          placeholder=r"C:\Users\you\OneDrive\Cerebro\enterprise-inbox",
          show_if=("ENTERPRISE_ENABLED", [True])),
    Field("ENTERPRISE_OUTBOX_DIR", "Outbox folder", "enterprise",
          "Where Cerebro writes replies for a second flow to send. Leave blank to "
          "disable outbound entirely.",
          placeholder=r"C:\Users\you\OneDrive\Cerebro\enterprise-outbox",
          show_if=("ENTERPRISE_ENABLED", [True])),
    Field("ENTERPRISE_ARCHIVE_DIR", "Processed folder", "enterprise",
          "Where ingested files are moved. Defaults to a 'processed' folder inside the inbox.",
          advanced=True, show_if=("ENTERPRISE_ENABLED", [True])),
    Field("ENTERPRISE_POLL_SECONDS", "Check every (seconds)", "enterprise",
          type="number", advanced=True, show_if=("ENTERPRISE_ENABLED", [True])),
    Field("ENTERPRISE_AUTO_SEND", "Send replies without approval", "enterprise",
          "Off by default. Writing to the outbox is the moment a message actually "
          "goes out, so replies wait for you to approve them.",
          type="bool", show_if=("ENTERPRISE_ENABLED", [True])),

    # Documents
    Field("DOCUMENTS_ENABLED", "Read documents you open", "documents",
          "Lets Cerebro extract text from Word, Excel, PowerPoint and PDF files "
          "so it can answer questions about them.", type="bool"),
    Field("DOCUMENT_WATCH_DIRS", "Watched folders", "documents",
          "Folders the document watcher may scan, one per line. Leave blank and "
          "Cerebro only reads documents you point it at.",
          show_if=("DOCUMENTS_ENABLED", [True])),
    Field("SHAREPOINT_SYNC_ROOTS", "SharePoint / OneDrive sync roots", "documents",
          "Local folders where your SharePoint libraries are synced, one per line. "
          "Cerebro uses these to open a SharePoint link as a real file.",
          placeholder=r"C:\Users\you\Contoso Ltd",
          show_if=("DOCUMENTS_ENABLED", [True])),
    Field("DOCUMENT_BACKUP_ON_EDIT", "Back up before editing", "documents",
          "Keeps a timestamped copy beside any file Cerebro changes.",
          type="bool", show_if=("DOCUMENTS_ENABLED", [True])),
    Field("DOCUMENT_MAX_MB", "Largest document (MB)", "documents",
          type="number", advanced=True, show_if=("DOCUMENTS_ENABLED", [True])),
    Field("BROWSER_TRACK_ALL_TABS", "Track all browser tabs", "documents",
          "Off by default: only recognised CRM and document pages are reported. "
          "Turn on to have the extension report every page you visit.", type="bool"),
    Field("BROWSER_EXCLUDED_DOMAINS", "Never report these domains", "documents",
          "One per line. Applies whatever the tracking setting is.",
          placeholder="mybank.com\npayroll.company.com"),

    # Second brain
    Field("PERSONA", "How Cerebro speaks to you", "brain", type="select", options=[
        {"value": "assistant", "label": "As my assistant (you / I)"},
        {"value": "partner", "label": "As my second brain (we / us)"},
    ]),
    Field("MEMORY_ENABLED", "Remember what it learns", "brain",
          "Distil durable facts from cases and activity, and recall them when drafting.",
          type="bool"),
    Field("STYLE_LEARNING_ENABLED", "Learn my writing voice", "brain",
          "Study your sent replies and transcripts so drafts sound like you.",
          type="bool"),

    # Secretary
    Field("TASKS_ENABLED", "Run tasks for me", "secretary",
          "Let Cerebro carry out scheduled tasks like maintaining a document.",
          type="bool"),
    Field("NUDGES_ENABLED", "Raise nudges", "secretary",
          "Point out unanswered mail, cases resolved but not written up, and due reminders.",
          type="bool"),
    Field("TASK_TICK_SECONDS", "Scheduler interval (s)", "secretary",
          type="number", advanced=True),

    # Microsoft Copilot bridge
    Field("COPILOT_BRIDGE_ENABLED", "Connect a Copilot Studio agent", "copilot",
          "Shares a folder with your agent so it can see what you're working on.",
          type="bool"),
    Field("COPILOT_BRIDGE_DIR", "Shared folder", "copilot",
          "A folder inside OneDrive. Create one called Cerebro and point here — "
          "your agent reads it, so it must be somewhere OneDrive syncs.",
          placeholder=r"C:\Users\you\OneDrive - Contoso\Cerebro\copilot",
          show_if=("COPILOT_BRIDGE_ENABLED", [True])),
    Field("COPILOT_PUBLISH_CONTEXT", "Share what I'm working on", "copilot",
          "Current case, customer, whether you're on a call, documents you have open.",
          type="bool", show_if=("COPILOT_BRIDGE_ENABLED", [True])),
    Field("COPILOT_PUBLISH_MEMORY", "Share what Cerebro has learned", "copilot",
          "Distilled facts only. Screenshots and typed text never leave this machine.",
          type="bool", show_if=("COPILOT_BRIDGE_ENABLED", [True])),
    Field("COPILOT_PUBLISH_STYLE", "Share my writing voice", "copilot",
          "So the agent drafts in your voice too, not a generic one.",
          type="bool", show_if=("COPILOT_BRIDGE_ENABLED", [True])),
    Field("COPILOT_ACCEPT_COMMANDS", "Let the agent ask Cerebro to do things", "copilot",
          "Local actions only — search, read a document, add to your log. It can "
          "never send mail or messages through Cerebro.",
          type="bool", show_if=("COPILOT_BRIDGE_ENABLED", [True])),
    Field("COPILOT_COMMAND_MODE", "When the agent asks for a change", "copilot",
          type="select", options=[
              {"value": "approve", "label": "Ask me first (recommended)"},
              {"value": "auto", "label": "Just do it"},
          ], show_if=("COPILOT_BRIDGE_ENABLED", [True])),
    Field("COPILOT_SYNC_SECONDS", "Update the folder every (seconds)", "copilot",
          type="number", advanced=True, show_if=("COPILOT_BRIDGE_ENABLED", [True])),
    Field("COPILOT_MEMORY_LIMIT", "Memories to share", "copilot",
          type="number", advanced=True, show_if=("COPILOT_BRIDGE_ENABLED", [True])),

    # Activity capture
    Field("ACTIVITY_CAPTURE_ENABLED", "Enable activity capture", "capture",
          "The master switch. Nothing is captured unless this is on.", type="bool"),
    Field("ACTIVITY_SCREENSHOTS", "Capture screenshots", "capture",
          "Periodic downscaled screenshots of the active window.",
          type="bool", show_if=("ACTIVITY_CAPTURE_ENABLED", [True])),
    Field("ACTIVITY_SCREENSHOT_SECONDS", "Screenshot interval (s)", "capture",
          type="number", show_if=("ACTIVITY_CAPTURE_ENABLED", [True])),
    Field("ACTIVITY_KEYSTROKES", "Capture typed text", "capture",
          "Words you type, assembled and redacted. Never raw keystrokes for passwords.",
          type="bool", show_if=("ACTIVITY_CAPTURE_ENABLED", [True])),
    Field("ACTIVITY_REDACT_PII", "Redact names, emails, phone numbers", "capture",
          "Secrets are always redacted; this also removes personal identifiers.",
          type="bool", show_if=("ACTIVITY_CAPTURE_ENABLED", [True])),
    Field("ACTIVITY_RETENTION_DAYS", "Delete capture after (days)", "capture",
          "0 keeps it indefinitely.", type="number",
          show_if=("ACTIVITY_CAPTURE_ENABLED", [True])),
    Field("ACTIVITY_EXCLUDED_APPS", "Never capture these apps", "capture",
          "One window title or app name per line. Login and banking windows are "
          "always skipped automatically.",
          show_if=("ACTIVITY_CAPTURE_ENABLED", [True])),

    # Desktop
    Field("SCREENPIPE_ENABLED", "Connect to Screenpipe automatically", "desktop",
          "Enabled by default and harmless when Screenpipe is not installed or running.", type="bool"),
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
            "model_provider": f.model_provider,
            "show_if": ({"key": f.show_if[0], "values": [str(v).lower() if isinstance(v, bool) else v
                                                          for v in f.show_if[1]]}
                        if f.show_if else None),
            "show_if_all": [
                {"key": condition[0],
                 "values": [str(v).lower() if isinstance(v, bool) else v
                            for v in condition[1]]}
                for condition in f.show_if_all
            ],
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

        # Seed a model dropdown with the curated list so the page is usable
        # before any credentials exist. The browser replaces this with the
        # account's real models the moment Refresh is pressed.
        if f.type == "model":
            from app.core import model_catalog
            entry["options"] = model_catalog.options(
                f.model_provider, str(entry.get("value") or "")
            )
        payload["fields"].append(entry)

    return payload


def _format(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


#: Characters that stop a value from being written bare in a .env file.
_NEEDS_QUOTING = ("\n", "\r", "#", '"', "'")


def _encode(value: Any) -> str:
    """
    Render a value for a ``.env`` file, quoting and escaping only when needed.

    Two dotenv behaviours make this fiddly, and both bite Windows users:

    * an unquoted value stops at the first newline, so a multi-line setting
      (watched folders, excluded domains) would silently lose everything after
      its first line — and the orphaned lines parse as junk keys;
    * inside double quotes, backslash sequences are interpreted, so a quoted
      ``C:\notes\team`` comes back as ``C:`` + newline + ``otes`` + tab.

    So: write bare when the value is simple, and when quoting is unavoidable,
    escape the backslashes first.
    """
    text = _format(value)
    needs_quotes = (
        any(char in text for char in _NEEDS_QUOTING)
        or text != text.strip()
    )
    if not needs_quotes:
        return text

    escaped = (text.replace("\\", "\\\\")
                   .replace('"', '\\"')
                   .replace("\n", "\\n")
                   .replace("\r", ""))
    return f'"{escaped}"'


def decode_env_value(text: str) -> str:
    """Inverse of :func:`_encode`, used when re-reading a hand-edited file."""
    if len(text) >= 2 and text[0] == text[-1] == '"':
        body = text[1:-1]
        return (body.replace("\\n", "\n").replace('\\"', '"')
                    .replace("\\\\", "\\"))
    return text


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
        entry = f"{f.key}={_encode(values[f.key])}"
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
            lines.append(f"{key}={_encode(values[key])}")

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

    restart_keys = {"DATABASE_URL", "HOST", "PORT", "DEBUG", "CORS_ORIGINS",
                    "SQLALCHEMY_ECHO"}
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
