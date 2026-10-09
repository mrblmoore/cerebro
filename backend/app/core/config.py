"""
Cerebro configuration.

Design goal: **Cerebro must start with no configuration at all.** Every setting
has a working default, so ``uvicorn app.main:app`` succeeds on a fresh clone and
the user can fill in the details later from the Setup UI at ``/setup``.
"""

import re
from typing import List, Optional

from pydantic import field_validator
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


#: DEXIS's RightAnswers agent workspace (as given by the user).
RIGHTANSWERS_WORKSPACE = "https://dexis.rightanswers.com/solutionmanger/controller/workspace/"
#: Addresses earlier versions saved, which only reached the site's front page.
_OLD_RIGHTANSWERS_DEFAULTS = {"https://dexis.rightanswers.com", "http://dexis.rightanswers.com",
                              "dexis.rightanswers.com"}


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
    #: How Ask lets the model call tools (search, read, draft). ``auto`` uses the
    #: provider's native tool calling and falls back to a JSON protocol for
    #: models that do not support it; ``json`` forces the fallback; ``off``
    #: answers in a single step with whatever context is already in view.
    LLM_TOOL_MODE: str = "auto"  # auto | native | json | off
    #: Ask writes longer answers than a case note; this is its own cap.
    ASK_MAX_TOKENS: int = 1200
    #: Most tool calls Ask may make while answering one message.
    ASK_MAX_STEPS: int = 8
    #: Minimum similarity for a source excerpt to be offered as evidence.
    #: Below it, an excerpt is treated as unrelated and left out of the answer.
    ASK_MIN_SOURCE_SCORE: float = 0.12
    #: An opened document counts as "what you are looking at" for this long
    #: after it was last seen, then stops being pulled into unrelated answers.
    ASK_ACTIVE_DOCUMENT_MINUTES: int = 20

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
    BEDROCK_AUTH_MODE: str = "default"  # default | api_key | profile | keys
    #: A Bedrock API key — the bearer token AWS added so Bedrock can be used
    #: with a single pasted key like every other provider here, instead of an
    #: access key pair. Passed to boto3 as AWS_BEARER_TOKEN_BEDROCK.
    BEDROCK_API_KEY: Optional[str] = None
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
    #: Optional direct SharePoint access. The local-sync resolver above remains
    #: available; Graph adds remote-only files, site search and exact item IDs.
    SHAREPOINT_GRAPH_ENABLED: bool = False
    MICROSOFT_TENANT_ID: str = "common"
    MICROSOFT_CLIENT_ID: Optional[str] = None
    #: Largest document Cerebro will read into memory, in megabytes.
    DOCUMENT_MAX_MB: float = 25.0

    # --------------------------------------------------------- integrations
    #: A hidden browser, driven by Playwright, that works in RightAnswers and
    #: Dynamics 365 with the user's own sign-in. Cerebro keeps a dedicated
    #: browser profile for it; no passwords are stored here.
    BROWSER_AUTOMATION_ENABLED: bool = False
    #: ``msedge`` uses the Edge already on Windows; ``chrome`` uses Chrome;
    #: ``chromium`` uses a Playwright-downloaded Chromium.
    BROWSER_CHANNEL: str = "msedge"
    #: ``headless`` is invisible; ``offscreen`` is a real window placed off
    #: screen, for sign-in systems that refuse headless browsers; ``visible``
    #: shows what Cerebro is doing (useful for checking a new setup).
    BROWSER_MODE: str = "headless"
    #: Close the hidden browser after this long without work.
    BROWSER_IDLE_SECONDS: int = 300
    #: Longest Cerebro waits for one page to load or one step to finish.
    BROWSER_TIMEOUT_SECONDS: int = 30
    RIGHTANSWERS_ENABLED: bool = False
    #: The page RightAnswers work starts from: DEXIS's SolutionManager agent
    #: workspace, which is where the articles are reached. A missing "https://"
    #: is added. (Its host is also what "on the RightAnswers site" means.)
    RIGHTANSWERS_URL: Optional[str] = RIGHTANSWERS_WORKSPACE

    @field_validator("RIGHTANSWERS_URL")
    @classmethod
    def _workspace_address(cls, value):
        # 0.4.0 saved the bare site, which doesn't lead to the articles.
        if value and value.strip().rstrip("/").lower() in _OLD_RIGHTANSWERS_DEFAULTS:
            return RIGHTANSWERS_WORKSPACE
        return value
    DYNAMICS_ENABLED: bool = False
    #: The company's Dynamics 365 organisation (host only, as above).
    DYNAMICS_URL: Optional[str] = "https://dental.crm.dynamics.com"
    #: BeyondTrust Remote Support and Genesys Cloud, through the same hidden
    #: browser and the user's own sign-in (no API client needed). Read-only.
    BEYONDTRUST_ENABLED: bool = False
    BEYONDTRUST_URL: Optional[str] = None
    GENESYS_ENABLED: bool = False
    GENESYS_URL: Optional[str] = "https://apps.mypurecloud.com"
    #: SharePoint through the same hidden browser and sign-in: open pasted
    #: links, read documents and pages, and change them with approval. Needs
    #: no app registration (unlike SHAREPOINT_GRAPH_ENABLED below).
    SHAREPOINT_BROWSER_ENABLED: bool = False
    SHAREPOINT_SITE_URL: Optional[str] = "https://envistaconnect.sharepoint.com"
    #: Apply SharePoint document and page changes as soon as Ask (or a chat
    #: task) makes them, instead of waiting for approval. Each one is still
    #: shown with its before/after and can be undone from its card.
    SHAREPOINT_AUTO_APPLY: bool = False
    #: Outlook and Teams on the web, through the same hidden browser and
    #: Microsoft 365 sign-in: read, search and send mail and chats, watched
    #: for new messages. Sending always asks first unless the matching
    #: *_AUTO_SEND is on, and even then only replies in an existing thread.
    OUTLOOK_BROWSER_ENABLED: bool = False
    OUTLOOK_URL: Optional[str] = "https://outlook.cloud.microsoft/mail/"
    OUTLOOK_AUTO_SEND: bool = False
    TEAMS_BROWSER_ENABLED: bool = False
    TEAMS_URL: Optional[str] = "https://teams.cloud.microsoft"
    TEAMS_AUTO_SEND: bool = False
    #: Watch Outlook and Teams for new messages while they are connected.
    INBOX_MONITOR_ENABLED: bool = True
    INBOX_MONITOR_SECONDS: int = 60
    #: Which new messages Cerebro researches on its own: off | important | all.
    #: "Important" = sent directly to you, @mentions you, urgent, or names a case.
    INBOX_ASSIST: str = "important"
    #: Most messages researched per hour, so a busy inbox can't run up AI costs.
    INBOX_ASSIST_PER_HOUR: int = 12
    #: Windows notifications for: important | all | suggestions (when research finishes).
    INBOX_NOTIFY: str = "important"
    #: Your name(s) as Outlook and Teams show them, comma-separated — so your
    #: own messages are never treated as new mail. Found automatically when it can be.
    INBOX_MY_NAMES: Optional[str] = None

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
    #: Connect whenever an independently installed Screenpipe service is available.
    #: The client degrades cleanly while it is not running.
    SCREENPIPE_ENABLED: bool = True

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
                or (auth_mode == "api_key" and bool(self.BEDROCK_API_KEY))
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
