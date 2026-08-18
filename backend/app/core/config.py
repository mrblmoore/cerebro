"""
Cerebro configuration.

Design goal: **Cerebro must start with no configuration at all.** Every setting
has a working default, so ``uvicorn app.main:app`` succeeds on a fresh clone and
the user can fill in the details later from the Setup UI at ``/setup``.
"""

import re
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.paths import (
    DEFAULT_LOG_PATH,
    ENV_FILE,
    default_database_url,
    ensure_data_dirs,
)

ensure_data_dirs()


def _split_paths(value: str) -> List[str]:
    """Split a multi-path setting. Newlines and semicolons only — Windows paths
    contain colons, and folder names legitimately contain commas."""
    return [part.strip() for part in re.split(r"[;\n]", value or "") if part.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ app
    APP_NAME: str = "Cerebro"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    #: Flipped to True the first time the setup wizard is completed. While it is
    #: False the dashboard nudges the user towards ``/setup``.
    SETUP_COMPLETED: bool = False

    #: Origins allowed to call the API. "*" keeps the browser extension and the
    #: local widget working out of the box; tighten it for shared deployments.
    CORS_ORIGINS: str = "*"

    # ------------------------------------------------------------- database
    #: Defaults to a SQLite file under ``data/`` so nothing needs installing.
    DATABASE_URL: str = default_database_url()
    SQLALCHEMY_ECHO: bool = False

    # --------------------------------------------------------- vector store
    #: ``auto`` uses Qdrant when QDRANT_URL is reachable and falls back to the
    #: built-in SQLite vector store. ``local`` and ``qdrant`` force a backend.
    VECTOR_BACKEND: str = "auto"
    QDRANT_URL: Optional[str] = None
    QDRANT_API_KEY: Optional[str] = None

    #: ``local`` needs no API key and no network; ``openai`` produces much
    #: better semantic matches but requires an OpenAI key.
    EMBEDDING_PROVIDER: str = "local"  # local | openai
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    # ------------------------------------------------------------------ llm
    #: ``none`` disables AI generation entirely; Cerebro still tracks context,
    #: events and knowledge search without it.
    LLM_PROVIDER: str = "none"  # none | openai | ollama | qwen | bedrock
    LLM_TIMEOUT: int = 60
    LLM_MAX_TOKENS: int = 500
    LLM_TEMPERATURE: float = 0.7

    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_ORG_ID: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = None

    OLLAMA_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.1"

    QWEN_API_URL: Optional[str] = None
    QWEN_API_KEY: Optional[str] = None
    QWEN_MODEL: str = "qwen-plus"

    #: Amazon Bedrock uses the AWS SDK credential chain by default. A named
    #: profile or explicit temporary credentials can be selected in the UI for
    #: workstations that do not already have an AWS identity configured.
    BEDROCK_REGION: str = "us-east-1"
    BEDROCK_MODEL_ID: str = ""
    BEDROCK_AUTH_MODE: str = "default"  # default | profile | keys
    BEDROCK_AWS_PROFILE: Optional[str] = None
    BEDROCK_AWS_ACCESS_KEY_ID: Optional[str] = None
    BEDROCK_AWS_SECRET_ACCESS_KEY: Optional[str] = None
    BEDROCK_AWS_SESSION_TOKEN: Optional[str] = None
    BEDROCK_ENDPOINT_URL: Optional[str] = None

    # -------------------------------------------------- enterprise bridge
    #: Outlook and Teams reach Cerebro through folders that Power Automate
    #: writes to and reads from — no Microsoft credentials live here.
    ENTERPRISE_ENABLED: bool = False
    ENTERPRISE_INBOX_DIR: str = ""
    ENTERPRISE_OUTBOX_DIR: str = ""
    ENTERPRISE_ARCHIVE_DIR: str = ""
    #: Seconds between inbox sweeps when the backend watches the folder itself.
    ENTERPRISE_POLL_SECONDS: int = 5
    #: Replies are written as drafts for approval unless this is turned on.
    ENTERPRISE_AUTO_SEND: bool = False

    # ------------------------------------------------------------ documents
    DOCUMENTS_ENABLED: bool = True
    #: Folders the document watcher may read. Empty means "anywhere the user
    #: points Cerebro at explicitly, but nothing scanned automatically".
    DOCUMENT_WATCH_DIRS: str = ""
    #: Local roots where OneDrive/SharePoint libraries are synced, used to turn
    #: a SharePoint URL into a file Cerebro can actually open.
    SHAREPOINT_SYNC_ROOTS: str = ""
    #: Largest document Cerebro will read into memory, in megabytes.
    DOCUMENT_MAX_MB: float = 25.0
    #: Keep a timestamped copy beside any document before editing it.
    DOCUMENT_BACKUP_ON_EDIT: bool = True

    # ------------------------------------------------------- activity capture
    #: The whole activity-capture subsystem is off unless this is true. It is the
    #: single switch IT or the user flips to enable screenshots and typed-text
    #: capture, and nothing here records until it is on.
    ACTIVITY_CAPTURE_ENABLED: bool = False
    #: Periodic downscaled screenshots of the active window.
    ACTIVITY_SCREENSHOTS: bool = False
    ACTIVITY_SCREENSHOT_SECONDS: int = 60
    #: Longest edge of a stored screenshot, in pixels. Small on purpose — enough
    #: to recognise "the Q3 spreadsheet", not to read fine print back.
    ACTIVITY_SCREENSHOT_MAX_PX: int = 1280
    #: Capture typed text (keystrokes assembled into words).
    ACTIVITY_KEYSTROKES: bool = False
    #: Redact anything that looks like a name, email or phone number as well as
    #: secrets. Secrets are always redacted regardless.
    ACTIVITY_REDACT_PII: bool = True
    #: Delete captured activity older than this many days. 0 keeps it forever.
    ACTIVITY_RETENTION_DAYS: int = 14
    #: Applications and window titles never captured, one per line. Matched as a
    #: case-insensitive substring against the window title and process name.
    ACTIVITY_EXCLUDED_APPS: str = ""

    # ------------------------------------------------------- memory & voice
    MEMORY_ENABLED: bool = True
    #: How Cerebro refers to itself and the user. "partner" speaks as we/us;
    #: "assistant" speaks as your secretary would.
    PERSONA: str = "assistant"  # assistant | partner
    #: Learn the user's writing voice from their sent messages and transcripts.
    STYLE_LEARNING_ENABLED: bool = True

    # ------------------------------------------------- copilot studio bridge
    #: Entirely optional. Cerebro is complete without it; this shares a slice of
    #: what it knows with a Microsoft Copilot Studio agent, and lets that agent
    #: ask Cerebro to do local things.
    COPILOT_BRIDGE_ENABLED: bool = False
    #: A OneDrive-synced folder both sides can reach. No app registration, no
    #: token — the sync client does the crossing.
    COPILOT_BRIDGE_DIR: str = ""
    COPILOT_PUBLISH_CONTEXT: bool = True
    COPILOT_PUBLISH_MEMORY: bool = True
    COPILOT_PUBLISH_STYLE: bool = True
    #: How many memories to share. Raw activity is never shared at any setting.
    COPILOT_MEMORY_LIMIT: int = 100
    COPILOT_ACCEPT_COMMANDS: bool = True
    #: approve — anything that changes something waits for you in the widget.
    #: auto    — Cerebro carries it out immediately.
    COPILOT_COMMAND_MODE: str = "approve"  # approve | auto
    COPILOT_SYNC_SECONDS: int = 45

    # ------------------------------------------------------------- tasks
    TASKS_ENABLED: bool = True
    #: Seconds between task-scheduler ticks.
    TASK_TICK_SECONDS: int = 30
    #: Surface proactive nudges (unanswered mail, cases resolved but not updated).
    NUDGES_ENABLED: bool = True

    # ------------------------------------------------------------- browser
    #: Report every page visited, not just recognised CRM cases.
    BROWSER_TRACK_ALL_TABS: bool = False
    #: Domains the extension must never report, one per line or comma-separated.
    BROWSER_EXCLUDED_DOMAINS: str = ""

    # ------------------------------------------------------------- desktop
    SCREENPIPE_URL: str = "http://localhost:3030"
    SCREENPIPE_ENABLED: bool = False

    # ------------------------------------------------------------- logging
    CEREBRO_LOG_PATH: str = ""
    LOG_LEVEL: str = "INFO"
    LOG_TO_STDOUT: bool = True

    # ---------------------------------------------------------- properties
    @property
    def log_path(self) -> str:
        return self.CEREBRO_LOG_PATH or str(DEFAULT_LOG_PATH)

    @property
    def llm_model(self) -> str:
        """The model name for whichever provider is selected."""
        return {
            "openai": self.OPENAI_MODEL,
            "ollama": self.OLLAMA_MODEL,
            "qwen": self.QWEN_MODEL,
            "bedrock": self.BEDROCK_MODEL_ID,
        }.get(self.LLM_PROVIDER.lower(), "")

    @property
    def llm_configured(self) -> bool:
        provider = self.LLM_PROVIDER.lower()
        if provider == "openai":
            return bool(self.OPENAI_API_KEY)
        if provider == "ollama":
            return bool(self.OLLAMA_URL)
        if provider == "qwen":
            return bool(self.QWEN_API_URL and self.QWEN_API_KEY)
        if provider == "bedrock":
            auth_mode = self.BEDROCK_AUTH_MODE.lower()
            credentials_ready = (
                auth_mode == "default"
                or (auth_mode == "profile" and bool(self.BEDROCK_AWS_PROFILE))
                or (auth_mode == "keys" and bool(
                    self.BEDROCK_AWS_ACCESS_KEY_ID
                    and self.BEDROCK_AWS_SECRET_ACCESS_KEY
                ))
            )
            return bool(self.BEDROCK_REGION and self.BEDROCK_MODEL_ID and credentials_ready)
        return False

    @property
    def using_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    @property
    def document_watch_list(self) -> list:
        return _split_paths(self.DOCUMENT_WATCH_DIRS)

    @property
    def sharepoint_root_list(self) -> list:
        return _split_paths(self.SHAREPOINT_SYNC_ROOTS)

    @property
    def activity_excluded_apps(self) -> list:
        return [line.strip().lower()
                for line in re.split(r"[;\n]", self.ACTIVITY_EXCLUDED_APPS or "")
                if line.strip()]

    @property
    def excluded_domain_list(self) -> list:
        return [d.strip().lower() for d in re.split(r"[,\n]", self.BROWSER_EXCLUDED_DOMAINS)
                if d.strip()]

    @property
    def cors_origin_list(self) -> list:
        if self.CORS_ORIGINS.strip() in ("*", ""):
            return ["*"]
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


settings = Settings()
