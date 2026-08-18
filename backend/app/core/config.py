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
    LLM_PROVIDER: str = "none"  # none | openai | ollama | qwen
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
    def excluded_domain_list(self) -> list:
        return [d.strip().lower() for d in re.split(r"[,\n]", self.BROWSER_EXCLUDED_DOMAINS)
                if d.strip()]

    @property
    def cors_origin_list(self) -> list:
        if self.CORS_ORIGINS.strip() in ("*", ""):
            return ["*"]
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


settings = Settings()
