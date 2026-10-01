#!/usr/bin/env python3
"""
Cerebro test suite.

Runs against a throwaway SQLite database and needs no external services, no API
key and no running server:

    python test_mvp.py
"""

import os
import json
import shutil
import sys
import tempfile
from pathlib import Path

# The suite prints ✓/✗ marks. A Windows console defaults to cp1252, which cannot
# encode them, so the first print would crash with UnicodeEncodeError before any
# assertion ran. Force UTF-8 so the tests behave identically on every platform.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

# Point every component at a temporary database before the app imports settings.
_TEMP_DIR = tempfile.mkdtemp(prefix="cerebro-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_TEMP_DIR).as_posix()}/test.db"
os.environ["LOG_TO_STDOUT"] = "false"
os.environ["CEREBRO_LOG_PATH"] = str(Path(_TEMP_DIR) / "test.log")
os.environ["LLM_PROVIDER"] = "none"
os.environ["VECTOR_BACKEND"] = "local"
os.environ["ENTERPRISE_INBOX_DIR"] = str(Path(_TEMP_DIR) / "inbox")
os.environ["ENTERPRISE_OUTBOX_DIR"] = str(Path(_TEMP_DIR) / "outbox")
os.environ["ENTERPRISE_ENABLED"] = "true"
os.environ["SHAREPOINT_SYNC_ROOTS"] = str(Path(_TEMP_DIR) / "sync")
os.environ["MEMORY_ENABLED"] = "true"
os.environ["STYLE_LEARNING_ENABLED"] = "true"
os.environ["TASKS_ENABLED"] = "true"
os.environ["NUDGES_ENABLED"] = "true"
os.environ["COPILOT_BRIDGE_ENABLED"] = "true"
os.environ["COPILOT_BRIDGE_DIR"] = str(Path(_TEMP_DIR) / "copilot")

from app.core.database import SessionLocal, init_db  # noqa: E402
from app.schemas.event import EventCreate  # noqa: E402
from app.services import embeddings  # noqa: E402
from app.services.context_engine import ContextEngine  # noqa: E402
from app.services.event_detector import EventDetector  # noqa: E402
from app.services.llm_service import LLMService  # noqa: E402
from app.services.rag_service import RAGService  # noqa: E402

PASSED = []
FAILED = []


def check(label, condition, detail=""):
    """``detail`` is shown only on failure — a hint about what went wrong."""
    (PASSED if condition else FAILED).append(label)
    suffix = f"  ({detail})" if detail and not condition else ""
    print(f"  {'✓' if condition else '✗'} {label}{suffix}")


def session():
    return SessionLocal()


# --------------------------------------------------------------- versioning
def test_version_source():
    print("\nVersioning")
    from app.api.system import VERSION

    declared = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    check("Runtime version matches VERSION", VERSION == declared)


# --------------------------------------------------------------- detectors
def test_event_detector():
    print("\nEvent detection")

    detected = EventDetector.detect_crm_event(
        "https://company.lightning.force.com/case/500abc123",
        "Case #12345 - Outlook Issue | John Doe",
    )
    check("Salesforce case detected", detected and detected[0] == "CRM_CASE_OPENED")
    check("Salesforce case id extracted", detected and detected[1]["case_id"] == "500abc123")

    detected = EventDetector.detect_remote_session_event(
        "bomgar-rep.exe", "BeyondTrust Remote Support - Connected to SERVER-01")
    check("Bomgar session detected", detected and detected[0] == "REMOTE_SESSION_CONNECTED")
    check("Remote host extracted", detected and detected[1]["host"] == "SERVER-01")

    detected = EventDetector.detect_remote_session_event(
        "bomgar-rep.exe", "BeyondTrust Remote Support - Session ended")
    check("Disconnection detected",
          detected and detected[0] == "REMOTE_SESSION_DISCONNECTED")

    detected = EventDetector.detect_crm_event(
        "https://acme.service-now.com/nav_to.do?uri=incident.do%3Fsysparm_query=number%3DINC0012345",
        "Northwind | ServiceNow")
    check("ServiceNow incident detected", detected and detected[1]["case_id"] == "INC0012345")

    detected = EventDetector.detect_call_event("Teams.exe", "Meeting with Contoso | Microsoft Teams")
    check("Call start detected", detected and detected[0] == "CALL_STARTED")

    detected = EventDetector.detect_call_event("Teams.exe", "Call ended | Microsoft Teams")
    check("Call end detected", detected and detected[0] == "CALL_ENDED")

    check("Non-CRM page ignored",
          EventDetector.detect_crm_event("https://news.example.com/", "Example") is None)
    check("Non-conferencing app ignored",
          EventDetector.detect_call_event("notepad.exe", "Untitled - Notepad") is None)


# ----------------------------------------------------------- context engine
def test_context_engine():
    print("\nContext engine")
    db = session()
    engine = ContextEngine(db)

    check("Context initialises", engine.init_context() is not None)

    result = engine.process_event(EventCreate(
        event_type="CRM_CASE_OPENED", source="test",
        data={"system": "Salesforce", "case_id": "12345", "customer": "Acme Corp"}))
    check("Case opened sets case", result["context"]["crm_case"] == "12345")
    check("Case opened sets customer", result["context"]["customer"] == "Acme Corp")
    check("Case opened suggests documentation",
          any(r["type"] == "retrieve_docs" for r in result["recommendations"]))

    result = engine.process_event(EventCreate(
        event_type="CALL_STARTED", source="test", data={"application": "Teams"}))
    check("Call started sets call_active", result["context"]["call_active"] is True)

    result = engine.process_event(EventCreate(
        event_type="REMOTE_SESSION_CONNECTED", source="test", data={"host": "SERVER-01"}))
    check("Remote session tracked", result["context"]["remote_session_active"] is True)
    check("Remote host recorded", result["context"]["remote_host"] == "SERVER-01")

    result = engine.process_event(EventCreate(
        event_type="UNKNOWN_EVENT_TYPE", source="test", data={}))
    check("Unknown event does not raise", result["event_id"] is not None)

    context = engine.reset_context()
    check("Reset clears the case", context.crm_case is None)
    check("Reset clears session state",
          context.call_active is False and context.remote_session_active is False)

    db.close()


def test_event_flow():
    print("\nFull event flow")
    db = session()
    engine = ContextEngine(db)
    engine.reset_context()

    for event_type, data in [
        ("CRM_CASE_OPENED", {"system": "Salesforce", "case_id": "500abc", "customer": "Contoso"}),
        ("CALL_STARTED", {"application": "Teams"}),
        ("REMOTE_SESSION_CONNECTED", {"host": "CONTOSO-PC"}),
        ("CALL_ENDED", {}),
    ]:
        engine.process_event(EventCreate(event_type=event_type, source="test", data=data))

    context = engine.get_current_context()
    check("Case survives the flow", context.crm_case == "500abc")
    check("Customer survives the flow", context.customer == "Contoso")
    check("Call ended", context.call_active is False)
    check("Remote session still open", context.remote_session_active is True)

    recommendations = engine.current_recommendations()
    check("Live recommendations produced", len(recommendations) > 0)
    check("Missing AI provider is surfaced",
          any(r["type"] == "configure_ai" for r in recommendations))

    db.close()


# -------------------------------------------------------------- embeddings
def test_embeddings():
    print("\nEmbeddings")
    related = embeddings.cosine(
        embeddings.local_embedding("Outlook cannot connect to the Exchange server"),
        embeddings.local_embedding("Exchange server unreachable from Outlook"))
    unrelated = embeddings.cosine(
        embeddings.local_embedding("Outlook cannot connect to the Exchange server"),
        embeddings.local_embedding("The printer queue is stuck and will not clear"))

    check(f"Related texts score higher ({related:.2f} > {unrelated:.2f})", related > unrelated)
    check("Identical text scores ~1.0", abs(embeddings.cosine(
        embeddings.local_embedding("same text"),
        embeddings.local_embedding("same text")) - 1.0) < 1e-6)
    check("Empty text is handled", embeddings.local_embedding("") is not None)


# --------------------------------------------------------- knowledge search
def test_knowledge_search():
    print("\nKnowledge search")
    db = session()
    rag = RAGService(db)
    check("Falls back to the built-in store", rag.backend == "local")

    rag.index_document({
        "title": "KB-1043 Outlook connectivity 0x80040115",
        "content": "Outlook cannot connect to Exchange. Error code 0x80040115 usually "
                   "means the Exchange server is unreachable. Check autodiscover and VPN.",
        "source": "RightAnswers",
    })
    rag.index_document({
        "title": "Printer spooler restart runbook",
        "content": "When a printer queue stalls, restart the print spooler service "
                   "and clear the spool folder.",
        "source": "runbook",
    })

    results = rag.search("outlook cannot reach exchange", limit=3)
    check("Search returns a result", len(results) > 0)
    check("Most relevant document ranks first",
          results and "Outlook" in results[0]["title"])

    results = rag.search("printer queue stuck", limit=3)
    check("Second query finds the other document",
          results and "Printer" in results[0]["title"])

    check("Empty query returns nothing", rag.search("", limit=3) == [])

    try:
        rag.index_document({"title": "Empty", "content": "   "})
        check("Empty content is rejected", False)
    except ValueError:
        check("Empty content is rejected", True)

    db.close()


def test_chunked_citations_and_sources():
    print("\nUnified sources and cited retrieval")
    from app.models.knowledge_chunk import KnowledgeChunk
    from app.services.source_service import SourceService

    db = session()
    indexed = RAGService(db).index_document({
        "title": "Machine Manual",
        "source": "test",
        "url": "https://example.test/manual",
        "content": "## Page 1\nOrdinary setup notes.\n\n## Page 2\n"
                   "The flux capacitor reset code is ALPHA-77.\n\n"
                   "## Page 3\nWarranty information.",
    })
    chunks = db.query(KnowledgeChunk).filter(
        KnowledgeChunk.document_id == indexed.id).all()
    check("Indexed documents are split into attributable sections", len(chunks) >= 3)

    hits = RAGService(db).search("flux capacitor reset code", limit=3)
    check("Search returns a section citation", bool(hits and hits[0].get("citation")))
    check("Citation identifies the matching page", hits and "Page 2" in hits[0].get("locator", ""))
    check("Search returns the relevant passage, not the document beginning",
          hits and "ALPHA-77" in hits[0].get("excerpt", ""))

    source = SourceService(db).observe(
        "browser", "https://example.test/live", "Live troubleshooting page",
        uri="https://example.test/live",
        content="The current incident workaround is to recycle service Delta.",
        readable=True, exclusive=True)
    context = SourceService(db).context_for_query("incident workaround Delta")
    check("Captured pages become queryable Ask sources",
          any(item.get("source_id") == source.id for item in context))
    check("Transient sources carry a stable citation ID",
          any(item.get("ref", "").startswith("S") for item in context))
    db.close()


# --------------------------------------------------------------------- llm
def test_llm_disabled():
    print("\nAI provider (disabled)")
    llm = LLMService()
    check("Reports itself disabled", llm.enabled is False)
    check("Status is not an error", llm.status()["ok"] is True)

    summary = llm.generate_case_summary({"customer": "Contoso", "title": "Outlook issue"})
    check("Generation returns guidance, not an exception", "Settings" in summary)


def test_model_catalog_and_discovery():
    """Model dropdowns always offer something, and never raise on a dead provider."""
    print("\nModel catalogue and discovery")

    from app.core import model_catalog, settings_store
    from app.services import model_discovery

    description = settings_store.describe()
    model_fields = [f for f in description["fields"] if f["type"] == "model"]
    check("Every provider's model field is a dropdown", len(model_fields) == 5)
    check("Model fields name their provider",
          all(f["model_provider"] for f in model_fields))
    check("Model dropdowns are pre-populated",
          all(len(f["options"]) >= 3 for f in model_fields))
    check("Every dropdown offers a custom escape hatch",
          all(any(o["id"] == model_catalog.CUSTOM for o in f["options"])
              for f in model_fields))

    # Chat and embedding catalogues must stay distinct: an embedding endpoint
    # rejects a chat model ID, which is a confusing failure to debug.
    chat = {m["id"] for m in model_catalog.fallback_models("openai")}
    embedding = {m["id"] for m in model_catalog.fallback_models("openai_embedding")}
    check("Chat and embedding catalogues do not overlap", not (chat & embedding))

    # A configured value that is not in the curated list must survive.
    options = model_catalog.options("openai", "my-private-deployment")
    check("An existing custom model stays selectable",
          options[0]["id"] == "my-private-deployment")

    # Discovery must degrade, never raise — the settings page has to render.
    for provider in ("openai", "ollama", "qwen", "unknown-provider"):
        try:
            models, error = model_discovery.discover(provider)
            ok = True
        except Exception:
            ok, models, error = False, [], ""
        check(f"Listing {provider} degrades instead of raising", ok)
        if provider != "unknown-provider":
            check(f"{provider} returns models or falls back to a usable list", len(models) > 0)


def test_bedrock_credential_modes():
    """Every AWS sign-in mode is honoured, and CRT trouble explains itself."""
    print("\nBedrock credentials")

    from app.core.config import settings
    from app.services.llm_service import _bedrock_error, LLMNotConfigured

    # The stock botocore message points at a pip that fixes the wrong Python.
    # Ours has to name Cerebro's setup instead.
    crt = _bedrock_error(Exception(
        "MissingDependencyException: Using CRT_AUTH requires an additional "
        "dependency. pip install botocore[crt]"))
    check("A missing CRT dependency is explained", isinstance(crt, LLMNotConfigured))
    check("CRT guidance points at Cerebro's own setup", "setup" in str(crt))
    check("CRT guidance warns against a plain pip install", "pip install" in str(crt))

    check("Access denied names the needed permission",
          "bedrock:InvokeModel" in str(_bedrock_error(
              Exception("AccessDeniedException: not authorized"))))
    check("An expired token says so",
          "expired" in str(_bedrock_error(Exception("ExpiredTokenException"))).lower())
    check("An unrelated error passes through unchanged",
          type(_bedrock_error(ValueError("something else"))) is ValueError)

    previous = {key: getattr(settings, key) for key in
                ("BEDROCK_AUTH_MODE", "BEDROCK_API_KEY", "BEDROCK_MODEL_ID",
                 "BEDROCK_REGION", "LLM_PROVIDER")}
    try:
        for key, value in (("LLM_PROVIDER", "bedrock"), ("BEDROCK_REGION", "us-east-1"),
                           ("BEDROCK_MODEL_ID", "us.example.chat-v1:0")):
            object.__setattr__(settings, key, value)

        object.__setattr__(settings, "BEDROCK_AUTH_MODE", "api_key")
        object.__setattr__(settings, "BEDROCK_API_KEY", "")
        check("An API key mode with no key is not 'configured'",
              settings.llm_configured is False)

        object.__setattr__(settings, "BEDROCK_API_KEY", "ABSKtest")
        check("An API key alone is enough to be configured",
              settings.llm_configured is True)
    finally:
        for key, value in previous.items():
            object.__setattr__(settings, key, value)


def test_bundled_dependencies():
    """Everything a feature needs ships with the install, not after it."""
    print("\nBundled dependencies")

    ai_requirements = (ROOT / "backend" / "requirements-ai.txt").read_text(encoding="utf-8")
    # Bedrock cross-Region inference profiles sign with SigV4a, which botocore
    # can only do with its "crt" extra. Without this pin the first Bedrock call
    # dies asking the user to install it by hand.
    check("botocore's CRT extra is pinned", "botocore[crt]" in ai_requirements)

    spec = (ROOT / "packaging" / "cerebro.spec").read_text(encoding="utf-8")
    check("The frozen build bundles awscrt", '"awscrt"' in spec)
    check("The frozen build bundles botocore's CRT auth", "botocore.crt.auth" in spec)

    launcher = (ROOT / "cerebro.py").read_text(encoding="utf-8")
    check("Setup installs every requirement group", "ALL_REQUIREMENTS" in launcher)
    for group in ("requirements-ai.txt", "requirements-documents.txt",
                  "requirements-capture.txt", "requirements-audio.txt"):
        check(f"Setup installs {group}", group in launcher)


def test_database_resilience():
    """A bad database setting must never stop Cerebro from starting."""
    print("\nDatabase resilience")

    from app.core.database import _explain, check_database, probe_database

    result = check_database()
    check("The working database reports ok", result["ok"] is True)
    check("It names the dialect", "sqlite" in result["detail"].lower())

    configured = probe_database(os.environ["DATABASE_URL"], initialize=True)
    check("The setup probe verifies the configured database", configured["ok"] is True)
    check("The setup probe proves writes and reports the location",
          configured.get("writable") is True
          and str(Path(_TEMP_DIR).resolve()) in configured.get("location", ""))
    check("The setup probe reports whether Cerebro's schema exists",
          configured.get("tables", 0) > 0)

    # create_engine imports the driver eagerly, so this class of failure lands
    # at import time. If it were fatal, one wrong setting would leave no way
    # back into the UI to correct it.
    source = (ROOT / "backend" / "app" / "core" / "database.py").read_text(encoding="utf-8")
    check("Engine creation is guarded", "ENGINE_ERROR" in source)
    check("A broken URL falls back to the built-in database",
          "default_database_url()" in source)

    check("A missing driver is explained, not dumped",
          "not installed" in _explain("ModuleNotFoundError: No module named 'psycopg'"))
    check("The explanation says data is safe meanwhile",
          "built-in database" in _explain("No module named 'psycopg'"))
    check("A refused connection is explained",
          "listening" in _explain("connection refused"))
    check("Bad credentials are explained",
          "rejected" in _explain("password authentication failed for user"))
    check("An unknown error passes through unchanged",
          _explain("something unexpected") == "something unexpected")

    from app.core import setup_checks
    check("The PostgreSQL driver is a repairable component",
          "postgres" in setup_checks.COMPONENTS)
    wizard = (ROOT / "desktop" / "setup_wizard.py").read_text(encoding="utf-8")
    check("The wizard offers to install the driver",
          'component_status("postgres")' in wizard)


def test_database_round_trip():
    """Write through the models and read it back, on a real database file."""
    print("\nDatabase round trip")

    from app.core.database import SessionLocal, engine
    from sqlalchemy import inspect

    tables = set(inspect(engine).get_table_names())
    for required in ("events", "context_state", "memories", "tasks",
                     "tracked_documents", "enterprise_messages", "sources",
                     "knowledge_chunks"):
        check(f"Table {required} exists", required in tables)

    from app.models.event import Event
    db = SessionLocal()
    try:
        marker = f"ROUNDTRIP-{os.getpid()}"
        db.add(Event(event_type="CRM_CASE_OPENED", source="test",
                     case_id=marker, data={"customer": "Round Trip Ltd"}))
        db.commit()

        # A second session, so this reads from the database rather than the
        # identity map of the session that wrote it.
        other = SessionLocal()
        try:
            found = other.query(Event).filter(Event.case_id == marker).one_or_none()
            check("A written event is readable from a new session", found is not None)
            check("Its JSON payload survives the round trip",
                  bool(found) and found.data.get("customer") == "Round Trip Ltd")
        finally:
            other.close()

        db.query(Event).filter(Event.case_id == marker).delete()
        db.commit()
        check("It can be deleted again",
              db.query(Event).filter(Event.case_id == marker).count() == 0)
    finally:
        db.close()


def test_file_reads_declare_encoding():
    """
    Every file read must name its encoding.

    Python uses the *locale* encoding when none is given. That is UTF-8 on the
    machines this is usually developed on and cp1252 on a Windows runner, so a
    bare read_text() works everywhere except the one place it matters — and it
    fails on the first em-dash or tick mark in a source file, which is exactly
    how this broke the v0.3.6 installer build.
    """
    print("\nFile reads declare an encoding")

    import re

    targets = [ROOT / "test_mvp.py", ROOT / "cerebro.py"]
    for directory in ("backend/app", "desktop", "packaging"):
        targets.extend(sorted((ROOT / directory).rglob("*.py")))

    # A read with no encoding= before the closing paren on the same line.
    bare_read_text = re.compile(r"\.read_text\((?![^)]*encoding=)")
    bare_open = re.compile(r"(?<![\w.])open\(\s*[^)]*?[\"'\w)][^)]*\)"
                           r"(?<!encoding)", re.X)

    offenders = []
    for path in targets:
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(source.splitlines(), 1):
            if "encoding=" in line:
                continue
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if bare_read_text.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number} read_text()")
            # Only a real call site: "with open(...)" or "x = open(...)".
            # Matching a bare "open(" also hits the word inside string literals,
            # including this test's own message.
            if re.search(r"(?:with|=)\s+open\(", line) and "urlopen" not in line \
                    and not re.search(r"[\"']([rw]b|[rw]b\+)[\"']", line):
                offenders.append(f"{path.relative_to(ROOT)}:{number} open()")

    check("No file is read without an explicit encoding", not offenders,
          "; ".join(offenders[:4]))

    # The source tree genuinely contains non-ASCII, which is what makes the
    # above matter rather than being a style rule.
    wizard = (ROOT / "desktop" / "setup_wizard.py").read_text(encoding="utf-8")
    check("Source files really do contain non-ASCII",
          any(ord(character) > 127 for character in wizard))


def test_everything_is_explained():
    """Any setting that is not self-evident has to say what it is for."""
    print("\nSettings are explained")

    from app.core import settings_store

    described = settings_store.describe()
    check("Every group explains itself",
          all(group.get("description") for group in described["groups"]))

    # A checkbox called "Debug mode" speaks for itself. A box wanting a path, a
    # URL, a key or a bare number does not, and guessing is how people give up.
    unexplained = []
    for field in described["fields"]:
        if field.get("help"):
            continue
        key = field["key"]
        if field["type"] in ("url", "password") or any(
                word in key for word in
                ("DIR", "PATH", "URL", "KEY", "ID", "TOKEN", "MODEL", "LIMIT",
                 "INTERVAL", "SECONDS", "DAYS", "MAX", "MIN")):
            unexplained.append(key)
    check("Every non-obvious setting has help text", not unexplained,
          f"missing: {unexplained}")

    # A "Test connection" button wired to a target the backend does not handle
    # returns a 404 that reads as a broken integration.
    import re
    html = (ROOT / "backend" / "app" / "web" / "settings.html").read_text(encoding="utf-8")
    block = html.split("TEST_TARGETS = {")[1].split("}")[0]
    ui_targets = dict(re.findall(r"(\w+):\s*'(\w+)'", block))
    api = (ROOT / "backend" / "app" / "api" / "system.py").read_text(encoding="utf-8")
    backend_targets = set(re.findall(r'if target == "(\w+)"', api))
    for tab, target in sorted(ui_targets.items()):
        check(f"The {tab} tab's test target exists", target in backend_targets)


def test_installer_integrity():
    """Every file and executable the installer names actually exists."""
    print("\nInstaller integrity")

    import re

    iss = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    spec = (ROOT / "packaging" / "cerebro.spec").read_text(encoding="utf-8")

    # A missing image is a compile error on the Windows runner, which is a slow
    # and confusing way to find out.
    for line in set(re.findall(
            r'(?:SetupIconFile|WizardImageFile|WizardSmallImageFile)=([^\n]+)', iss)):
        for item in line.split(","):
            item = item.strip()
            path = ROOT / "packaging" / item.replace("\\", "/")
            check(f"Installer image {item} exists", path.is_file())

    # An installer that launches an exe PyInstaller never built produces a
    # "file not found" at the end of a successful install.
    for name in sorted(set(re.findall(r'#\{?(\w*ExeName)\}?', iss))):
        define = re.search(rf'#define {name}\s+"([^"]+)"', iss)
        if not define:
            continue
        exe = define.group(1)
        check(f"{exe} is built by the spec",
              f'name="{exe.replace(".exe", "")}"' in spec)

    check("The wizard is what the installer runs at the end",
          "CerebroSetupWizard.exe" in iss and "postinstall" in iss)
    check("The welcome text says what Cerebro is", "WelcomeLabel2=" in iss)
    check("The finish text says what happens next", "FinishedLabel=" in iss)


def test_branding_assets():
    """The logo, the font and the icon all ship, and are wired everywhere."""
    print("\nBranding assets")

    # The font is bundled rather than fetched from a CDN: Cerebro is local-first
    # and often runs with no outbound internet, where a webfont silently fails.
    web_font = ROOT / "backend" / "app" / "web" / "static" / "fonts" / "InterVariable.woff2"
    desktop_font = ROOT / "assets" / "fonts" / "InterVariable.ttf"
    check("The web font is bundled", web_font.is_file())
    check("The desktop font is bundled", desktop_font.is_file())
    check("The font licence ships with it",
          (ROOT / "assets" / "fonts" / "Inter-LICENSE.txt").is_file())

    css = (ROOT / "backend" / "app" / "web" / "static" / "cerebro.css").read_text(encoding="utf-8")
    check("The CSS declares the bundled face", "@font-face" in css)
    check("The CSS serves the font locally, not from a CDN",
          "/static/fonts/InterVariable.woff2" in css
          and "fonts.googleapis.com" not in css and "fonts.gstatic.com" not in css)
    check("Inter leads the sans stack", '--sans: "Inter Variable"' in css)

    icon = ROOT / "packaging" / "cerebro.ico"
    check("The Windows icon exists", icon.is_file())
    check("The icon is not a stub", icon.stat().st_size > 20_000)

    for size in (32, 48, 64, 128, 256):
        check(f"The {size}px logo ships",
              (ROOT / "assets" / "icons" / f"cerebro-{size}.png").is_file())
    check("The web favicon ships",
          (ROOT / "backend" / "app" / "web" / "static" / "cerebro-256.png").is_file())

    for name in ("logo-mark.svg", "logo-mark-small.svg"):
        svg = (ROOT / "assets" / name).read_text(encoding="utf-8")
        check(f"{name} is valid XML", svg.strip().startswith("<svg") and "</svg>" in svg)
        # The mark is mirrored rather than drawn twice, so the hemispheres
        # cannot drift apart when the shape is edited.
        check(f"{name} mirrors its halves", "scale(-1,1)" in svg)

    spec = (ROOT / "packaging" / "cerebro.spec").read_text(encoding="utf-8")
    check("The frozen build ships the assets", '"assets"' in spec)
    check("The frozen build ships the branding module", '"branding"' in spec)

    installer = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    check("The installer uses the icon", "SetupIconFile=cerebro.ico" in installer)
    check("The installer is branded", "WizardImageFile=" in installer)
    for image in ("wizard-image.bmp", "wizard-image@2x.bmp",
                  "wizard-small.bmp", "wizard-small@2x.bmp"):
        check(f"{image} exists", (ROOT / "packaging" / "images" / image).is_file())

    # Inno reads a .iss without a BOM in the system ANSI codepage, so anything
    # outside ASCII can reach a user's screen mangled.
    check("The installer script is pure ASCII",
          all(ord(character) < 128 for character in installer))


def test_motion_and_icons():
    """Animation is present, consistent, and can be turned off."""
    print("\nMotion and icons")

    css = (ROOT / "backend" / "app" / "web" / "static" / "cerebro.css").read_text(encoding="utf-8")
    check("Shared easing and duration tokens exist",
          "--ease:" in css and "--fast:" in css and "--slow:" in css)
    for animation in ("cb-rise", "cb-fade", "cb-spin", "cb-shimmer", "cb-pulse-ring", "cb-pop"):
        check(f"The {animation} animation is defined", f"@keyframes {animation}" in css)
    # Interface animation causes motion sickness for some people, and this app
    # is designed to sit on screen all day.
    check("Reduced motion is honoured", "prefers-reduced-motion: reduce" in css)
    check("Reduced motion overrides every animation",
          "animation-duration: .01ms !important" in css)

    js = (ROOT / "backend" / "app" / "web" / "static" / "cerebro.js").read_text(encoding="utf-8")
    check("The topbar carries the logo", '<svg class="mark"' in js)
    check("The logo is inline, not a request", "logo-mark.svg" not in js)
    check("Settings groups have drawn icons", "GROUP_ICONS" in js)
    # Emoji render in a different style and colour on every OS.
    for group in ("general", "database", "ai", "knowledge", "copilot", "logging"):
        check(f"The {group} group has an icon", f"'{group}':" in js)
    check("Icons follow the theme colour", "currentColor" in js)

    wizard = (ROOT / "desktop" / "setup_wizard.py").read_text(encoding="utf-8")
    check("The wizard uses the shared palette", "branding.DARK" in wizard)
    check("The wizard registers the bundled font", "branding.load_fonts()" in wizard)
    check("The wizard shows the logo", "branding.logo_image" in wizard)
    check("The wizard has a spinner for slow work", "_start_spinner" in wizard)
    check("The spinner is stopped when work finishes", "_stop_spinner" in wizard)
    check("Steps animate in", "_animate_step_in" in wizard)

    widget = (ROOT / "desktop" / "widget.py").read_text(encoding="utf-8")
    check("The widget uses the bundled font", "branding.load_fonts()" in widget)
    check("The widget shows the logo in its title bar", "branding.logo_image" in widget)
    check("The widget fades in on launch", "_fade_in" in widget)
    check("The widget can pulse a status dot", "_pulse_dot" in widget)
    check("Ask keeps its composer below the scrolling transcript",
          'composer.pack(side="bottom", fill="x")' in widget
          and "def _render_ask_composer" in widget)
    check("The widget presents one Ask, Sources, Activity workflow",
          'TABS = [("ask", "Ask"), ("sources", "Sources"), ("activity", "Activity")]' in widget)
    check("Starting the widget also starts its capture helpers",
          "_start_desktop_helpers" in widget and "DocumentWatcher" in widget)
    apply_snapshot = widget.split("def _apply_snapshot", 1)[1].split("@staticmethod", 1)[0]
    check("Unchanged Ask polls do not rebuild the widget",
          'previous_snapshot.get("nudges") != snapshot.get("nudges")' in apply_snapshot)


def test_branding_module():
    """branding.py degrades safely when assets or a display are missing."""
    print("\nBranding module")

    sys.path.insert(0, str(ROOT / "desktop"))
    try:
        import branding
    finally:
        sys.path.pop(0)

    check("Assets resolve to a real directory", branding.assets_dir().is_dir())
    check("The font file is found",
          (branding.assets_dir() / "fonts" / "InterVariable.ttf").is_file())
    check("An icon path is found", bool(branding.icon_path()))

    # Called with no Tk root, as the widget does before its window exists.
    family = branding.load_fonts()
    check("A font family is always returned", isinstance(family, str) and family)

    # Without a display there is no PhotoImage, and that must not raise.
    try:
        branding.logo_image(32)
        safe = True
    except Exception:
        safe = False
    check("Loading the logo never raises", safe)

    check("Light and dark palettes have the same keys",
          set(branding.DARK) == set(branding.LIGHT))
    check("The palette matches the web accent",
          branding.DARK["accent"] == "#7c74f5")


def test_setup_checks_and_repair():
    """Preflight explains itself, and repair targets the right interpreter."""
    print("\nSetup checks")

    from app.core import setup_checks

    report = setup_checks.preflight()
    check("Preflight reports every component",
          len(report["checks"]) == len(setup_checks.COMPONENTS))
    check("Preflight names the running Python", bool(report["python"]))
    check("Core is marked required",
          next(c for c in report["checks"] if c["component"] == "core")["required"] is True)
    check("Every component explains why it matters",
          all(c["why"] for c in report["checks"]))

    # Bedrock must list awscrt, not just boto3: boto3 alone imports fine and
    # then fails at request-signing time, which is the bug this guards.
    check("Bedrock's check includes awscrt",
          "awscrt" in setup_checks.COMPONENTS["bedrock"]["modules"])
    check("Providers map to their dependency group",
          setup_checks.component_for_provider("bedrock") == "bedrock"
          and setup_checks.component_for_provider("ollama") is None)

    check("An unknown component is refused, not crashed",
          setup_checks.repair("not-a-component")["ok"] is False)
    check("Repairing something already present is a no-op",
          setup_checks.repair("core")["ok"] is True)

    # Every component must name a requirements file that actually exists,
    # otherwise "install what's missing" is a button that cannot work.
    for name, spec in setup_checks.COMPONENTS.items():
        check(f"{name} points at a real requirements file",
              (ROOT / spec["requirements"]).exists())


def test_setup_wizard():
    """The wizard the installer runs covers every step and is wired in."""
    print("\nSetup wizard")

    source = (ROOT / "desktop" / "setup_wizard.py").read_text(encoding="utf-8")
    steps = ["welcome", "database", "ai", "microsoft", "done"]
    for step in steps:
        check(f"The wizard has a {step} step", f"_step_{step}" in source)
    check("Every step is registered in order",
          all(f'"{s}"' in source.split("STEPS = [")[1].split("]")[0] for s in steps))

    # Each configurable step must actually verify itself — the whole promise is
    # that finishing setup means it was tested, not assumed.
    for tester in ("_test_database", "_test_ai", "_test_microsoft"):
        check(f"{tester} exists", f"def {tester}" in source)
    check("Slow checks run off the UI thread", "_run_async" in source)
    check("Finishing marks setup complete", "mark_setup_complete" in source)
    check("Missing dependencies can be repaired in place", "_repair" in source)
    check("Successful tests keep their result visible after re-rendering",
          source.index('self._render()\n                self._set_status(result.get("detail")')
          > source.index("def _test_database"))
    check("Switching to the built-in database saves its real URL",
          'self.pending["DATABASE_URL"] = default_database_url()' in source)
    check("Frozen setup resolves bundled files through PyInstaller",
          'getattr(sys, "_MEIPASS"' in source)
    check("The flow package and quickstart resolve from the bundled root",
          'root / "power_automate" / "Cerebro-Bridge.zip"' in source
          and 'root / "docs" / "POWER_AUTOMATE_QUICKSTART.md"' in source)

    spec = (ROOT / "packaging" / "cerebro.spec").read_text(encoding="utf-8")
    check("The wizard is built as its own executable", "CerebroSetupWizard" in spec)
    check("The wizard bundles Tkinter", '"tkinter"' in spec)

    installer = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    check("The installer runs the wizard", "CerebroSetupWizard.exe" in installer)
    check("The installer runs it as a first run", "--first-run" in installer)

    launcher = (ROOT / "cerebro.py").read_text(encoding="utf-8")
    check("A source install can open the wizard too", "def cmd_configure" in launcher)
    check("Setup ends by configuring", "5. Configuring Cerebro" in launcher)


def test_copilot_instructions():
    """The pasted instructions must match what the bridge actually implements."""
    print("\nCopilot Studio instructions")

    import json as _json
    import re as _re

    from app.services.copilot_bridge import ALLOWED_COMMANDS
    from app.services.copilot_guide import AGENT_INSTRUCTIONS, guide, instructions

    text = instructions("D:/OneDrive/Cerebro/copilot")
    check("The real folder replaces the placeholder",
          "{FOLDER}" not in text and "D:/OneDrive/Cerebro/copilot" in text)
    check("Escaped braces are unescaped for the reader",
          "{{" not in text and "}}" not in text)
    check("A blank folder still reads sensibly",
          "OneDrive" in instructions(""))

    # An agent told about an action that does not exist writes command files
    # that are refused; one it is not told about is a capability lost. Both
    # directions have to stay in sync with the whitelist.
    documented = set(_re.findall(r'"action":\s*"([a-z_]+)"', text))
    check("Every allowed command is documented",
          documented >= set(ALLOWED_COMMANDS),
          f"missing: {sorted(set(ALLOWED_COMMANDS) - documented)}")
    check("No command is documented that does not exist",
          documented <= set(ALLOWED_COMMANDS),
          f"extra: {sorted(documented - set(ALLOWED_COMMANDS))}")

    # Every JSON example must parse; a malformed one teaches the agent to
    # write malformed command files.
    examples = _re.findall(r'\{"action".*?\}(?=\n)', text, _re.S)
    check("The instructions carry JSON examples", len(examples) >= len(ALLOWED_COMMANDS))
    bad = []
    for example in examples:
        try:
            _json.loads(" ".join(example.split()))
        except ValueError:
            bad.append(example[:40])
    check("Every JSON example parses", not bad, f"bad: {bad}")

    # The folder layout must describe the real one.
    check("The command folder is named correctly", "commands/" in text)
    check("The processed folder matches the bridge", "commands/processed" in text)
    check("A stale archive/ folder is not invented", "\n    archive/" not in text)
    for name in ("context.json", "memory.json", "style.json"):
        check(f"{name} is described", name in text)

    # Real field names, so the agent reads the files rather than guessing.
    for field_name in ("generated_at", "current_case", "on_a_call", "persona",
                       "style_card", "confidence", "command_file"):
        check(f"The schema names {field_name}", field_name in text)

    check("Result files are explained", "result-cmd-" in text)
    check("The agent is told not to wait for results", "do NOT wait" in text.replace("do not wait", "do NOT wait"))
    check("Staleness has explicit thresholds", "10 minutes" in text)

    payload = guide()
    check("The guide serves the filled-in instructions",
          "{FOLDER}" not in payload["instructions"])
    check("OneDrive is the required tool",
          any(tool["required"] for tool in payload["tools"]
              if "OneDrive" in tool["name"]))


def test_bedrock_provider():
    """Bedrock uses Converse and never needs real AWS credentials in tests."""
    print("\nAI provider (Amazon Bedrock)")
    import types as _types

    from app.core.config import settings

    captured = {}

    class FakeConfig:
        def __init__(self, **kwargs):
            captured["config"] = kwargs

    class FakeClient:
        def converse(self, **kwargs):
            captured["request"] = kwargs
            return {"output": {"message": {"content": [
                {"text": "ready"}, {"text": "to help"},
            ]}}}

    class FakeSession:
        def __init__(self, **kwargs):
            captured["session"] = kwargs

        def client(self, service_name, **kwargs):
            captured["service"] = service_name
            captured["client"] = kwargs
            return FakeClient()

    fake_boto3 = _types.ModuleType("boto3")
    fake_boto3.Session = FakeSession
    fake_botocore = _types.ModuleType("botocore")
    fake_config = _types.ModuleType("botocore.config")
    fake_config.Config = FakeConfig

    module_names = ("boto3", "botocore", "botocore.config")
    previous_modules = {name: sys.modules.get(name) for name in module_names}
    keys = (
        "LLM_PROVIDER", "BEDROCK_REGION", "BEDROCK_MODEL_ID",
        "BEDROCK_AUTH_MODE", "BEDROCK_AWS_PROFILE",
    )
    previous_settings = {key: getattr(settings, key) for key in keys}
    try:
        sys.modules["boto3"] = fake_boto3
        sys.modules["botocore"] = fake_botocore
        sys.modules["botocore.config"] = fake_config
        object.__setattr__(settings, "LLM_PROVIDER", "bedrock")
        object.__setattr__(settings, "BEDROCK_REGION", "us-west-2")
        object.__setattr__(settings, "BEDROCK_MODEL_ID", "us.example.chat-v1:0")
        object.__setattr__(settings, "BEDROCK_AUTH_MODE", "profile")
        object.__setattr__(settings, "BEDROCK_AWS_PROFILE", "support-sso")

        llm = LLMService()
        reply = llm._dispatch("Help with this case")
        check("Bedrock is reported as configured", llm.enabled is True)
        check("Named AWS profile is used",
              captured["session"] == {"region_name": "us-west-2",
                                      "profile_name": "support-sso"})
        check("Bedrock Runtime client is selected", captured["service"] == "bedrock-runtime")
        check("Configured model is sent to Converse",
              captured["request"]["modelId"] == "us.example.chat-v1:0")
        check("Converse text blocks are combined", reply == "ready\nto help")
    finally:
        for key, value in previous_settings.items():
            object.__setattr__(settings, key, value)
        for name, module in previous_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


# ------------------------------------------------- enterprise bridge
def test_enterprise_normalisation():
    """Power Automate payloads vary; the normaliser must absorb the variation."""
    print("\nEnterprise bridge — normalising")
    from app.services.enterprise_service import assess_urgency, detect_case, normalise

    outlook = normalise({
        "source": "outlook", "type": "email",
        "timestamp": "2026-08-17T15:30:01Z",
        "sender": "person@company.com",
        "recipients": ["you@company.com"],
        "subject": "Need update on customer case",
        "body": "Can you send the latest status?",
        "thread_id": "abc123",
        "metadata": {"importance": "high"},
    })
    check("Outlook payload normalised", outlook["source"] == "outlook")
    check("Recipients flattened", outlook["recipients"] == "you@company.com")
    check("High importance raises urgency", outlook["urgency"] == "high")
    check("Timestamp parsed", outlook["timestamp"] is not None)

    teams = normalise({
        "source": "teams", "type": "message",
        "sender": "manager@company.com",
        "chat_or_channel": "Support Escalations",
        "body": "Can you check this? Case 500XY7 is escalating.",
        "thread_id": "teams-thread-456",
    })
    check("Teams channel kept", teams["chat_or_channel"] == "Support Escalations")
    check("Case found in the body", teams["case_id"] == "500XY7")
    check("Escalation raises urgency", teams["urgency"] == "high")

    # Graph-shaped fields, as a flow built from dynamic content produces them.
    graph = normalise({
        "source": "outlook",
        "from": {"emailAddress": {"address": "dana@contoso.com", "name": "Dana Reed"}},
        "toRecipients": [{"emailAddress": {"address": "you@company.com"}}],
        "subject": "RE: INC0012345",
        "bodyPreview": "<html><body><p>Any update?</p><b>Still down</b></body></html>",
        "receivedDateTime": "2026-08-17T16:02:00Z",
        "conversationId": "AAQkAD00",
    })
    check("Graph sender extracted", graph["sender"] == "dana@contoso.com")
    check("Graph display name kept", graph["sender_name"] == "Dana Reed")
    check("HTML stripped from the body", "<" not in (graph["body"] or ""))
    check("Body text survived stripping", "Any update?" in (graph["body"] or ""))
    check("Case found in the subject", graph["case_id"] == "INC0012345")

    check("Ordinary English is not a case", detect_case("In case of emergency") is None)
    check("Question raises urgency to medium",
          assess_urgency("Quick question", "Can you look at this?", "normal")[0] == "medium")
    check("Urgency always explains itself",
          bool(assess_urgency("URGENT", "outage", "normal")[1]))


def test_enterprise_ingest_and_reply():
    """Ingest is idempotent, and replies only leave on approval."""
    print("\nEnterprise bridge — ingest and reply")
    import json as _json

    from app.services import enterprise_service
    from app.services.enterprise_service import EnterpriseService

    inbox = Path(_TEMP_DIR) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "outlook", "type": "email", "external_id": "msg-1",
        "sender": "person@company.com", "subject": "Urgent: mail is down",
        "body": "Nothing is sending.", "timestamp": "2026-08-17T15:30:01Z",
    }
    (inbox / "outlook-1.json").write_text(_json.dumps(payload), encoding="utf-8")
    os.utime(inbox / "outlook-1.json", (0, 0))   # old enough to be swept

    db = session()
    result = enterprise_service.drain_inbox(db)
    check("File ingested", result["ingested"] == 1)
    check("File archived out of the inbox",
          not (inbox / "outlook-1.json").exists())

    # Replaying the same message must not create a second row.
    service = EnterpriseService(db)
    again = service.ingest_payload(payload, source_file="replay.json")
    check("Replay detected as duplicate", again["status"] == "duplicate")

    messages = service.list_messages()
    check("Exactly one message stored", len(messages) == 1)
    check("Urgency assessed on ingest", messages[0].urgency == "high")

    briefing = service.briefing(hours=24)
    check("Briefing counts the message", briefing["total"] == 1)
    check("Briefing lists it as urgent", len(briefing["urgent"]) == 1)

    outbox = Path(_TEMP_DIR) / "outbox"
    draft = service.create_action("reply_email", "On it — fix deploying now.",
                                  in_reply_to=messages[0].id, send=False)
    check("Reply starts as a draft", draft.status == "draft")
    check("Reply addressed to the sender", draft.to == "person@company.com")
    check("Subject prefixed with Re:", (draft.subject or "").startswith("Re:"))
    check("Nothing written to the outbox before approval",
          not outbox.exists() or not list(outbox.glob("*.json")))

    sent = service.dispatch_action(draft)
    check("Approval queues the reply", sent.status == "queued")

    files = list(outbox.glob("*.json"))
    check("Exactly one file written for Power Automate", len(files) == 1)
    written = _json.loads(files[0].read_text(encoding="utf-8"))
    check("Outbound payload carries the action", written["action"] == "reply_email")
    check("Outbound payload carries the recipient",
          written["to"] == ["person@company.com"])
    check("No partial .part file left behind", not list(outbox.glob("*.part")))
    db.close()


# ------------------------------------------------------------- documents
def _sample_documents() -> Path:
    """Build a Word file and a workbook to exercise the readers and editors."""
    folder = Path(_TEMP_DIR) / "docs"
    folder.mkdir(parents=True, exist_ok=True)

    import docx
    import openpyxl

    document = docx.Document()
    document.add_heading("Case 500XY7 — Contoso Ltd", 0)
    document.add_paragraph("Outlook cannot connect. Error 0x80040115.")
    document.add_paragraph("Status: PENDING")
    document.save(str(folder / "notes.docx"))

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Tickets"
    sheet.append(["Case", "Customer", "Hours", "Status"])
    sheet.append(["500XY7", "Contoso", 4.5, "Open"])
    workbook.save(str(folder / "tickets.xlsx"))
    workbook.close()

    return folder


def test_document_reading():
    print("\nDocuments — reading")
    from app.services import document_readers as readers

    folder = _sample_documents()

    word = readers.read(folder / "notes.docx")
    check("Word document read", word["kind"] == "docx")
    check("Word text extracted", "0x80040115" in word["text"])
    check("Word headings found", len(word["outline"]["headings"]) >= 1)

    excel = readers.read(folder / "tickets.xlsx")
    check("Excel workbook read", excel["kind"] == "xlsx")
    check("Excel sheet named", excel["outline"]["sheets"][0]["name"] == "Tickets")
    check("Excel headers found",
          excel["outline"]["sheets"][0]["headers"][0] == "Case")
    check("Excel values extracted", "Contoso" in excel["text"])

    (folder / "notes.txt").write_text("Plain text notes", encoding="utf-8")
    check("Plain text read", readers.read(folder / "notes.txt")["kind"] == "text")

    for label, path, expected in [
        ("missing file", folder / "nope.docx", "not found"),
        ("unsupported type", folder / "thing.zip", "does not read"),
        ("legacy format", folder / "old.doc", "Word 97"),
    ]:
        if label != "missing file":
            path.write_text("x", encoding="utf-8")
        try:
            readers.read(path)
            check(f"{label} rejected", False)
        except readers.DocumentError as exc:
            check(f"{label} rejected with a clear message", expected in str(exc))


def test_document_editing():
    print("\nDocuments — editing")
    from app.services import document_editors as editors
    from app.services import document_readers as readers

    folder = _sample_documents()
    word = folder / "notes.docx"
    excel = folder / "tickets.xlsx"

    operations = [{"op": "replace_text", "find": "PENDING", "replace": "COMPLETE"}]
    preview = editors.apply(word, operations, dry_run=True)
    check("Dry run reports the change", preview["operations"][0]["occurrences"] == 1)
    check("Dry run writes nothing", "PENDING" in readers.read(word)["text"])
    check("Dry run makes no backup", preview["backup"] is None)

    applied = editors.apply(word, operations)
    check("Edit applied", "COMPLETE" in readers.read(word)["text"])
    check("Original text gone", "PENDING" not in readers.read(word)["text"])
    check("Backup created", applied["backup"] and Path(applied["backup"]).exists())

    editors.apply(excel, [
        {"op": "set_cell", "sheet": "Tickets", "cell": "D2", "value": "Closed"},
        {"op": "append_row", "sheet": "Tickets", "values": ["500ZZ1", "Fabrikam", 2, "Open"]},
        {"op": "set_cell", "sheet": "Tickets", "cell": "E1", "value": "=C2*150"},
    ])
    result = readers.read(excel)
    check("Cell updated", "Closed" in result["text"])
    check("Row appended", "Fabrikam" in result["text"])
    check("Row count grew", result["outline"]["sheets"][0]["rows"] == 3)

    import openpyxl
    workbook = openpyxl.load_workbook(str(excel))
    check("Formula stored as a formula", workbook["Tickets"]["E1"].value == "=C2*150")
    check("Numbers stayed numeric",
          isinstance(workbook["Tickets"].cell(row=3, column=3).value, (int, float)))
    workbook.close()

    for label, target, operations in [
        ("unknown sheet", excel, [{"op": "set_cell", "sheet": "Ghost", "cell": "A1", "value": 1}]),
        ("unknown operation", excel, [{"op": "explode"}]),
        ("paragraph out of range", word, [{"op": "set_paragraph", "index": 999, "text": "x"}]),
        ("no operations", excel, []),
    ]:
        try:
            editors.apply(target, operations)
            check(f"{label} rejected", False)
        except readers.DocumentError:
            check(f"{label} rejected", True)

    # Office holds a ~$ lock file open while a document is open.
    lock = excel.parent / f"~${excel.name}"
    lock.write_text("", encoding="utf-8")
    try:
        editors.apply(excel, [{"op": "set_cell", "cell": "A1", "value": "x"}])
        check("Refuses to edit a file open in Office", False)
    except readers.DocumentError as exc:
        check("Refuses to edit a file open in Office", "open in Office" in str(exc))
    finally:
        lock.unlink()


def test_sharepoint_resolution():
    """A SharePoint URL must resolve to the locally synced file."""
    print("\nDocuments — SharePoint links")
    from app.services import document_service

    synced = Path(_TEMP_DIR) / "sync" / "Contoso Ltd" / "Shared Documents"
    synced.mkdir(parents=True, exist_ok=True)
    (synced / "Q3 Report.xlsx").write_bytes(
        (_sample_documents() / "tickets.xlsx").read_bytes())

    for url, expected in [
        ("https://contoso.sharepoint.com/sites/S/Shared%20Documents/Q3%20Report.xlsx",
         "Q3 Report.xlsx"),
        ("https://contoso.sharepoint.com/:x:/r/sites/S/_layouts/15/Doc.aspx"
         "?sourcedoc=%7Babc%7D&file=Q3%20Report.xlsx", "Q3 Report.xlsx"),
    ]:
        check(f"Filename extracted from {url.split('/')[-1][:24]}",
              document_service.filename_from_url(url) == expected)
        resolved = document_service.resolve_sharepoint(url)
        check("Resolved to the synced file",
              resolved is not None and resolved.name == expected)

    check("Non-document SharePoint page ignored",
          document_service.filename_from_url(
              "https://contoso.sharepoint.com/sites/S/SitePages/Home.aspx") is None)
    check("Unsynced document returns nothing",
          document_service.resolve_sharepoint(
              "https://contoso.sharepoint.com/sites/S/Missing.docx") is None)
    check("SharePoint host recognised",
          document_service.is_sharepoint_url("https://contoso.sharepoint.com/x"))
    check("Ordinary host not treated as SharePoint",
          not document_service.is_sharepoint_url("https://example.com/x.docx"))


def test_document_tracking():
    print("\nDocuments — tracking")
    from app.services.document_service import DocumentService

    folder = _sample_documents()
    db = session()
    service = DocumentService(db)

    record = service.observe(str(folder / "notes.docx"), discovered_by="desktop_watcher")
    check("Document tracked", record.id is not None)
    check("Kind detected", record.kind == "docx")
    check("Case found in the content", record.case_id == "500XY7")
    check("Text extracted", "0x80040115" in (record.text_preview or ""))

    again = service.observe(str(folder / "notes.docx"))
    check("Observing again reuses the same record", again.id == record.id)

    content = service.content(record)
    check("Content served with an outline", "headings" in content["outline"])
    check("Not reported as open in Office", content["open_in_office"] is False)

    indexed = service.index_into_knowledge(record)
    check("Indexed into the knowledge base", indexed["ok"])

    from app.services.rag_service import RAGService
    results = RAGService(db).search("outlook cannot connect error", limit=3)
    check("Document findable by search",
          any("notes.docx" in r["title"] for r in results))
    db.close()


# --------------------------------------------------------------- redaction
def test_redaction():
    print("\nRedaction — secrets and sensitive windows")
    from app.services.redaction import looks_sensitive, redact

    cleaned, fired = redact("my password is Hunter2! and the api key: sk-abc123def456ghi789jkl")
    check("Password removed", "Hunter2" not in cleaned and "password" in fired)
    check("Secret removed", "sk-abc123" not in cleaned)

    cleaned, fired = redact("card 4532 0151 1283 0366 today", redact_pii=True)
    check("Valid card number removed", "4532" not in cleaned)
    cleaned, _ = redact("order 1234 5678 9012 3456 7890")
    check("Non-Luhn long number kept", "1234 5678" in cleaned)

    clean_text = "Outlook error 0x80040115 after the VPN change."
    cleaned, fired = redact(clean_text)
    check("Clean text is untouched", cleaned == clean_text and not fired)

    check("Login window flagged sensitive", looks_sensitive("Sign in - Portal"))
    check("Password manager flagged", looks_sensitive("1Password"))
    check("Ordinary window not flagged", not looks_sensitive("Case 500XY7 - Salesforce"))


def test_activity_capture():
    print("\nActivity capture — storage and guards")
    import os as _os

    _os.environ["ACTIVITY_CAPTURE_ENABLED"] = "true"
    _os.environ["ACTIVITY_EXCLUDED_APPS"] = "notepad"
    from app.core.config import settings
    from app.services.activity_service import ActivityService

    for key in ("ACTIVITY_CAPTURE_ENABLED", "ACTIVITY_EXCLUDED_APPS"):
        object.__setattr__(settings, key,
                           True if key.endswith("ENABLED") else "notepad")

    db = session()
    service = ActivityService(db)

    kept = service.record(kind="keystrokes", application="Teams",
                          window_title="Chat", text="the password is Hunter2 ok")
    check("Frame stored", kept is not None)
    check("Secret redacted before storage", "Hunter2" not in (kept.text or ""))

    dropped = service.record(kind="screenshot", window_title="Bank - Sign in")
    check("Sensitive window dropped", dropped is None)

    excluded = service.record(kind="window", application="notepad.exe",
                              window_title="notepad - secret")
    check("Excluded app dropped", excluded is None)

    result = service.purge(everything=True)
    check("Purge clears everything", result["ok"])


# --------------------------------------------------------------- memory
def test_memory():
    print("\nMemory — store, recall, dedupe")
    from app.services.memory_service import MemoryService

    db = session()
    service = MemoryService(db)
    service.remember("Contoso Outlook fix",
                     "For Contoso, Outlook 0x80040115 after a VPN change was fixed "
                     "by rebuilding the MAPI profile.",
                     memory_type="case_resolution", case_id="500XY7", customer="Contoso")
    service.remember("Northwind prefers email",
                     "Northwind's IT lead prefers email over Teams.",
                     memory_type="customer_fact", customer="Northwind")

    results = service.recall("contoso outlook cannot connect after vpn",
                             case_id="500XY7", customer="Contoso")
    check("Relevant memory recalled", results and "MAPI" in results[0]["content"])
    check("Recall bumps use count", results[0]["use_count"] >= 1)

    before = len(service.list_memories())
    service.remember("Contoso Outlook",
                     "Contoso Outlook error 0x80040115 after VPN fixed by rebuilding "
                     "the MAPI profile.",
                     memory_type="case_resolution", case_id="500XY7", customer="Contoso")
    check("Near-duplicate merged, not added", len(service.list_memories()) == before)

    text = service.recall_text("how to reach northwind")
    check("Recall block is prompt-ready", "Northwind" in text)


# ------------------------------------------------------------- style
def test_style_and_persona():
    print("\nStyle & persona")
    from app.core.config import settings
    from app.services.style_service import StyleService, persona_directive

    db = session()
    style = StyleService(db)
    for sample in [
        "Hey Randy — got it working, mail's flowing again. Lmk if anything pops up. Thanks!",
        "Hi team, quick update: VPN change caused it. Fixed now. Cheers",
        "Thanks for the heads up, I'll take a look this afternoon. Best",
    ]:
        style.add_sample(sample)

    result = style.learn()
    check("Voice learned from samples", result["ok"])
    check("Casual tone detected", result["profile"]["formality"] == "casual")
    check("Greeting picked up", result["profile"]["typical_greeting"] == "hey")

    directive = style.drafting_directive()
    check("Drafting directive produced", "voice" in directive.lower())

    style.add_sample("the login password is Hunter2 use that")
    import json as _json
    last = _json.loads(style._row().samples)[-1]["text"]
    check("Secrets redacted from samples", "Hunter2" not in last)

    object.__setattr__(settings, "PERSONA", "partner")
    check("Partner persona says we/us", "we" in persona_directive().lower())
    object.__setattr__(settings, "PERSONA", "assistant")
    check("Assistant persona says you", "you" in persona_directive().lower())


# ------------------------------------------------------------- tasks
def test_task_parsing_and_scheduling():
    print("\nTasks — parsing and scheduling")
    from datetime import datetime

    from app.services.task_service import (TaskService, compute_next_run,
                                           parse_instruction)

    parsed = parse_instruction("keep the project log updated daily at 9am under my name")
    check("Daily schedule parsed", parsed["schedule"] == "daily")
    check("Time parsed", parsed["at_time"] == "09:00")
    check("Attribution parsed", parsed["attribution"] == "user")
    check("Document-update kind inferred", parsed["kind"] == "document_update")

    parsed = parse_instruction("remind me to call Randy at 3pm")
    check("3pm parsed as 15:00", parsed["at_time"] == "15:00")

    nxt = compute_next_run("daily", "09:00")
    check("Next run is in the future", nxt and nxt > datetime.now())
    check("Manual schedule never fires", compute_next_run("manual") is None)

    db = session()
    task = TaskService(db).create_from_instruction("remind me to update Northwind daily at 9am")
    check("Task created and scheduled", task.next_run is not None)

    # A one-off task must finish after firing, not silently become recurring.
    from datetime import datetime as _dt
    from app.models.task import Task as _Task
    once = _Task(title="call Randy", kind="reminder", schedule="once",
                 at_time="15:00", status="active",
                 next_run=_dt.now())
    db.add(once); db.commit(); db.refresh(once)
    TaskService(db).run(once)
    check("One-off task completes after firing", once.status == "done")
    check("One-off task does not reschedule", once.next_run is None)

    # A weekly task steps a full week, not a day.
    from app.services.task_service import compute_next_run as _next
    first = _next("weekly", "09:00")
    second = _next("weekly", "09:00", after=first)
    check("Weekly reschedule advances seven days",
          (second - first).days == 7)


def test_document_update_task():
    print("\nTasks — autonomous document maintenance")
    import json as _json

    import docx

    from app.models.task import Task
    from app.services import document_readers
    from app.services.task_executors import _find_user_section, execute

    folder = Path(_TEMP_DIR) / "tasklog"
    folder.mkdir(parents=True, exist_ok=True)
    document = docx.Document()
    document.add_heading("Project Log", 0)
    document.add_heading("Team updates", 1)
    document.add_paragraph("2026-08-15 — Kickoff.")
    document.add_heading("My daily log", 1)
    document.add_paragraph("2026-08-16 — Reviewed plan.")
    path = folder / "log.docx"
    document.save(str(path))

    content = document_readers.read(path)
    mine = _find_user_section(content, "mine", "user")
    paragraphs = [p.text for p in docx.Document(str(path)).paragraphs]
    check("'Mine' resolves to the user's section",
          mine is not None and paragraphs[mine - 1] == "My daily log")

    # With a stub LLM, the autonomous write lands under the user's section.
    import app.services.llm_service as llm_module

    original_enabled = llm_module.LLMService.enabled
    original_call = llm_module.LLMService._call_llm
    llm_module.LLMService.enabled = property(lambda self: True)
    llm_module.LLMService._call_llm = lambda self, prompt: "Cut over two servers."
    try:
        db = session()
        task = Task(title="daily log", kind="document_update", autonomous=True,
                    attribution="user", status="active",
                    spec=_json.dumps({"document": str(path), "section": "mine"}))
        db.add(task); db.commit(); db.refresh(task)
        result = execute(db, task)
        check("Autonomous write succeeded", result.get("status") == "active")

        after = [p.text for p in docx.Document(str(path)).paragraphs]
        entry = [p for p in after if "Cut over two servers" in p]
        check("Entry written", bool(entry))
        check("Entry under the user's section",
              after.index(entry[0]) > after.index("My daily log"))
        check("Team section untouched", "Kickoff." in " ".join(after))
    finally:
        llm_module.LLMService.enabled = original_enabled
        llm_module.LLMService._call_llm = original_call


# ------------------------------------------------------------- nudges
def test_nudges():
    print("\nNudges — detection, dedupe, persona")
    from datetime import datetime, timedelta

    from app.core.config import settings
    from app.models.case import Case
    from app.models.enterprise import EnterpriseMessage
    from app.models.event import Event
    from app.services.nudge_service import NudgeService

    db = session()
    db.add(EnterpriseMessage(source="outlook", type="email", external_id="nudge-1",
        sender="randy@company.com", sender_name="Randy", subject="Follow-up",
        urgency="high", handled=False,
        ingested_at=datetime.utcnow() - timedelta(hours=5)))
    db.add(Event(event_type="REMOTE_SESSION_DISCONNECTED", source="agent",
                 case_id="500ZZ9", data={"case_id": "500ZZ9"},
                 created_at=datetime.utcnow() - timedelta(hours=2)))
    db.add(Case(case_id="500ZZ9", system="Salesforce", customer="Fabrikam",
                title="Migration", status="open"))
    db.commit()

    service = NudgeService(db)
    object.__setattr__(settings, "PERSONA", "partner")
    first = service.scan()
    check("Nudges raised", first["raised"] >= 2)

    nudges = {n.kind: n for n in service.open_nudges()}
    check("Unanswered-mail nudge raised", "unanswered_email" in nudges)
    check("Case-not-updated nudge raised", "case_not_updated" in nudges)
    check("Partner voice used", "we" in nudges["case_not_updated"].body.lower())

    second = service.scan()
    check("Re-scan does not duplicate", second["raised"] == 0)


# ------------------------------------------------------ copilot bridge
def test_copilot_bridge():
    """Publishing, command execution, and the boundaries that must hold."""
    print("\nCopilot bridge")
    import json as _json

    from app.core.config import settings
    from app.services.copilot_bridge import ALLOWED_COMMANDS, CopilotBridge

    object.__setattr__(settings, "COPILOT_BRIDGE_ENABLED", True)
    object.__setattr__(settings, "COPILOT_COMMAND_MODE", "auto")

    folder = Path(_TEMP_DIR) / "copilot"
    db = session()
    bridge = CopilotBridge(db)

    published = bridge.publish()
    check("Publishes the shared files", published["ok"])
    check("Context published", (folder / "context.json").exists())
    check("Memory published", (folder / "memory.json").exists())
    check("Style published", (folder / "style.json").exists())

    context = _json.loads((folder / "context.json").read_text(encoding="utf-8"))
    check("Context carries a freshness stamp", "generated_at" in context)
    check("Context includes suggestions", "suggestions" in context)

    memory = _json.loads((folder / "memory.json").read_text(encoding="utf-8"))
    check("Memory payload has no raw activity",
          not any(key in memory for key in ("screenshots", "keystrokes", "activity")))

    # A permitted command runs.
    commands = folder / "commands"
    commands.mkdir(parents=True, exist_ok=True)
    good = commands / "cmd-good.json"
    good.write_text(_json.dumps({"action": "get_context"}), encoding="utf-8")
    os.utime(good, (0, 0))
    result = bridge.drain_commands()
    check("Permitted command executed", result["executed"] == 1)
    check("Command file archived", not good.exists())

    results = list((folder / "results").glob("*.json"))
    check("Result written back for the agent", bool(results))

    # A command outside the whitelist is refused, not executed.
    bad = commands / "cmd-bad.json"
    bad.write_text(_json.dumps({"action": "send_email", "to": "ceo@company.com"}),
                   encoding="utf-8")
    os.utime(bad, (0, 0))
    bridge.drain_commands()
    refusal = _json.loads(
        sorted((folder / "results").glob("*cmd-bad*"))[0].read_text(encoding="utf-8"))
    check("Non-permitted command refused", refusal["ok"] is False)
    check("Refusal explains why", "not-permitted" in refusal["error"]
          or "Unknown" in refusal["error"])
    check("Sending is not a permitted action", "send_email" not in ALLOWED_COMMANDS)

    # Approval mode stages changes instead of doing them.
    object.__setattr__(settings, "COPILOT_COMMAND_MODE", "approve")
    staged = commands / "cmd-change.json"
    staged.write_text(_json.dumps({
        "action": "append_document", "name": "nope.docx", "text": "hi"}),
        encoding="utf-8")
    os.utime(staged, (0, 0))
    bridge.drain_commands()
    outcome = _json.loads(
        sorted((folder / "results").glob("*cmd-change*"))[0].read_text(encoding="utf-8"))
    check("Changes wait for approval in approve mode", outcome.get("staged") is True)

    status = bridge.status()
    check("Status reports the folder", status["enabled"] and status["ok"])
    db.close()


def test_copilot_guide():
    """The pasteable instructions must actually describe the real contract."""
    print("\nCopilot guide")
    from app.services.copilot_bridge import ALLOWED_COMMANDS
    from app.services.copilot_guide import guide

    payload = guide()
    check("Guide has steps", len(payload["steps"]) >= 5)
    check("OneDrive is the required tool",
          any(t["required"] and "OneDrive" in t["name"] for t in payload["tools"]))

    instructions = payload["instructions"]
    check("Instructions mention context.json", "context.json" in instructions)
    check("Instructions mention style.json", "style.json" in instructions)
    # Asserted on intent rather than an exact sentence: the wording is expected
    # to be rewritten, the two guarantees are not.
    lowered = instructions.lower()
    check("Instructions forbid sending without approval",
          "showing the draft" in lowered or "showing the user the draft" in lowered)
    check("Instructions require an explicit yes before sending",
          "explicit yes" in lowered)
    check("Instructions tell it not to invent desktop state",
          "never invent desktop state" in lowered
          or "never claim to see their screen" in lowered)

    # Every command the instructions advertise must really be permitted, or the
    # agent will be told to do things Cerebro refuses.
    advertised = {name for name in ALLOWED_COMMANDS if f'"{name}"' in instructions}
    check("Every advertised command is permitted",
          advertised and advertised.issubset(set(ALLOWED_COMMANDS)))
    check("All permitted commands are documented",
          set(ALLOWED_COMMANDS).issubset(advertised))


def test_copilot_approval_flow():
    """A change staged in approve mode must actually run when approved."""
    print("\nCopilot approval flow")
    import json as _json

    import docx

    from app.core.config import settings
    from app.models.nudge import Nudge
    from app.services.copilot_bridge import CopilotBridge
    from app.services.document_service import DocumentService
    from app.services.nudge_service import NudgeService

    object.__setattr__(settings, "COPILOT_BRIDGE_ENABLED", True)
    object.__setattr__(settings, "COPILOT_COMMAND_MODE", "approve")

    folder = Path(_TEMP_DIR) / "approve"
    (folder / "commands").mkdir(parents=True, exist_ok=True)
    object.__setattr__(settings, "COPILOT_BRIDGE_DIR", str(folder))

    # A tracked document with a section that is the user's.
    document = docx.Document()
    document.add_heading("Log", 0)
    document.add_heading("My updates", 1)
    document.add_paragraph("2026-08-16 — started.")
    path = folder / "log.docx"
    document.save(str(path))

    db = session()
    DocumentService(db).observe(str(path))

    # Stub the LLM so the append can produce text.
    import app.services.llm_service as llm_module

    original_enabled = llm_module.LLMService.enabled
    original_call = llm_module.LLMService._call_llm
    llm_module.LLMService.enabled = property(lambda self: True)
    llm_module.LLMService._call_llm = lambda self, prompt: "reviewed the migration."
    try:
        bridge = CopilotBridge(db)
        command = {"action": "append_document", "name": "log.docx",
                   "section": "mine", "text": "add a status line"}

        # Stage via the real path: write a command file and drain.
        cmd = folder / "commands" / "cmd-approve.json"
        cmd.write_text(_json.dumps(command), encoding="utf-8")
        os.utime(cmd, (0, 0))
        bridge.drain_commands()

        # The shared test DB may hold copilot_request nudges from earlier tests;
        # take the most recent, which is the one we just staged.
        nudge = (db.query(Nudge)
                 .filter(Nudge.kind == "copilot_request")
                 .order_by(Nudge.id.desc()).first())
        check("Change staged as a nudge", nudge is not None)

        # Approving the nudge must actually perform the append.
        from app.api.tasks import act

        act(nudge.id, db)
        after = [p.text for p in docx.Document(str(path)).paragraphs]
        check("Approved command actually ran",
              any("reviewed the migration" in p for p in after))
        check("Entry landed under the user's section",
              after.index([p for p in after if "reviewed the migration" in p][0])
              > after.index("My updates"))

        # A duplicate stage does not silently vanish.
        cmd2 = folder / "commands" / "cmd-approve-2.json"
        cmd2.write_text(_json.dumps(command), encoding="utf-8")
        os.utime(cmd2, (0, 0))
        bridge.drain_commands()
        result2 = _json.loads(
            sorted((folder / "results").glob("*cmd-approve-2*"))[0].read_text(encoding="utf-8"))
        check("Duplicate stage reports it is already pending",
              result2.get("duplicate") is True)
    finally:
        llm_module.LLMService.enabled = original_enabled
        llm_module.LLMService._call_llm = original_call
    db.close()


def test_copilot_memory_redaction():
    """Memory published to the cloud folder must be redacted."""
    print("\nCopilot memory redaction")
    import json as _json

    from app.core.config import settings
    from app.services.copilot_bridge import CopilotBridge
    from app.services.memory_service import MemoryService

    folder = Path(_TEMP_DIR) / "redact-pub"
    folder.mkdir(parents=True, exist_ok=True)
    object.__setattr__(settings, "COPILOT_BRIDGE_ENABLED", True)
    object.__setattr__(settings, "COPILOT_BRIDGE_DIR", str(folder))

    db = session()
    # A memory that (wrongly) captured a secret in its content.
    MemoryService(db).remember(
        "Server access", "The admin password is Hunter2 for the Contoso box.",
        memory_type="fact")

    CopilotBridge(db).publish()
    published = _json.loads((folder / "memory.json").read_text(encoding="utf-8"))
    blob = _json.dumps(published)
    check("Secret redacted before publishing", "Hunter2" not in blob)
    check("Memory still published", published["count"] >= 1)
    db.close()


# ------------------------------------------------------------- regressions
def test_embedding_signature_matches_vector():
    """A fallback vector must be labelled with the space it actually belongs to."""
    print("\nEmbedding signatures")
    vector, produced = embeddings.embed_with_signature("outlook exchange error")
    check("Signature matches the vector length",
          produced.endswith(f":{len(vector)}"))
    check("Local provider reports the local signature",
          produced == embeddings.local_signature())


def test_schema_upgrade():
    """A database created before the embedding columns existed must still work."""
    print("\nSchema upgrade")
    import sqlite3
    from sqlalchemy import create_engine, inspect

    legacy = Path(_TEMP_DIR) / "legacy.db"
    connection = sqlite3.connect(legacy)
    connection.execute(
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, source VARCHAR, title VARCHAR, "
        "content TEXT, url VARCHAR, vector_id VARCHAR, tags VARCHAR, indexed BOOLEAN, "
        "created_at DATETIME, updated_at DATETIME)")
    connection.commit()
    connection.close()

    import app.core.database as database

    original = database.engine
    try:
        database.engine = create_engine(f"sqlite:///{legacy.as_posix()}",
                                        connect_args={"check_same_thread": False})
        database.init_db()
        columns = {c["name"] for c in inspect(database.engine).get_columns("documents")}
        check("Missing embedding column is added", "embedding" in columns)
        check("Missing signature column is added", "embedding_signature" in columns)
    finally:
        database.engine = original


def test_customer_from_title():
    """A two-part title carries no customer — guessing files the case number."""
    print("\nTitle parsing")
    detected = EventDetector.detect_crm_event(
        "https://acme.lightning.force.com/lightning/r/Case/5008d00000ABCDEfgh/view",
        "Case 00001234 | Contoso Ltd | Salesforce")
    check("Three-part title yields the customer",
          detected and detected[1]["customer"] == "Contoso Ltd")

    detected = EventDetector.detect_crm_event(
        "https://acme.lightning.force.com/lightning/r/Case/5008d00000ABCDEfgh/view",
        "Case 00001234 | Salesforce")
    check("Two-part title yields no customer",
          detected and detected[1]["customer"] is None)


def test_database_password_masking():
    """The settings API must never hand a database password to the browser."""
    print("\nCredential masking")
    from app.core import settings_store

    url = "postgresql+psycopg://cerebro:s3cret@db.internal:5432/cerebro"
    masked = settings_store.mask_url_password(url)
    check("Password is masked", "s3cret" not in masked)
    check("Rest of the URL survives", "db.internal:5432/cerebro" in masked)
    check("Masked value round-trips back to the real password",
          settings_store.restore_url_password(masked, url) == url)
    check("SQLite URLs are untouched",
          settings_store.mask_url_password("sqlite:///./data/cerebro.db")
          == "sqlite:///./data/cerebro.db")


# ----------------------------------------------------------- settings store
def test_env_round_trip():
    """Windows paths and multi-line settings must survive a write/read cycle."""
    print("\nSettings — .env encoding")
    import tempfile as _tempfile

    from dotenv import dotenv_values

    from app.core.settings_store import _encode

    folder = Path(_tempfile.mkdtemp(dir=_TEMP_DIR))
    cases = [
        ("windows path", r"C:\Users\you\Contoso"),
        # \n and \t inside a quoted value are escape sequences to dotenv.
        ("path with escape-shaped segments", r"C:\notes\team"),
        ("multi-line folders", "C:\\a\\b\nC:\\c\\d"),
        ("multi-line domains", "mybank.com\npayroll.company.com"),
        ("value with a hash", "value # not a comment"),
        ("value with quotes", 'say "hello"'),
        ("padded value", "  padded  "),
    ]
    for label, value in cases:
        target = folder / "t.env"
        target.write_text(f"K={_encode(value)}\nNEXT=ok\n", encoding="utf-8")
        parsed = dotenv_values(target)
        check(f"{label} round-trips",
              parsed.get("K") == value and parsed.get("NEXT") == "ok"
              and len(parsed) == 2)


def test_urgency_word_boundaries():
    """Urgency must key on words, not substrings."""
    print("\nEnterprise bridge — urgency precision")
    from app.services.enterprise_service import assess_urgency

    for text, expected in [
        ("Please download the report", "normal"),
        ("Shutdown window is Saturday", "normal"),
        ("Countdown to launch", "normal"),
        ("The site is down", "high"),
        ("This is escalating fast", "high"),
        ("Escalated to tier 3", "high"),
        ("Server outage in progress", "high"),
        ("Can you review by EOD", "medium"),
    ]:
        check(f"{text!r} → {expected}", assess_urgency(text, "", "normal")[0] == expected)


def test_empty_batch_file():
    """An empty batch must be archived, not retried forever."""
    print("\nEnterprise bridge — empty batch")
    from app.services import enterprise_service

    inbox = Path(_TEMP_DIR) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    empty = inbox / "outlook-empty.json"
    empty.write_text("[]", encoding="utf-8")
    os.utime(empty, (0, 0))

    db = session()
    result = enterprise_service.drain_inbox(db)
    check("Empty batch did not raise", result["ok"])
    check("Empty batch archived, not left to retry", not empty.exists())
    db.close()


def test_office_lock_detection():
    """Office truncates lock-file names for long documents."""
    print("\nDocuments — open-in-Office guard")
    from app.services.document_readers import is_open_in_office

    folder = Path(_TEMP_DIR) / "locks"
    folder.mkdir(parents=True, exist_ok=True)

    short = folder / "notes.docx"
    short.write_text("x", encoding="utf-8")
    check("Unlocked file reported as closed", not is_open_in_office(short))

    (folder / "~$notes.docx").write_text("", encoding="utf-8")
    check("Exact lock name detected", is_open_in_office(short))

    # Office drops leading characters when the base name is long.
    long_name = folder / "Case notes 2026.docx"
    long_name.write_text("x", encoding="utf-8")
    check("Long name reported as closed before locking",
          not is_open_in_office(long_name))
    (folder / "~$se notes 2026.docx").write_text("", encoding="utf-8")
    check("Truncated lock name detected", is_open_in_office(long_name))


def test_spreadsheet_value_coercion():
    """Identifier-shaped strings must not be turned into numbers."""
    print("\nDocuments — cell value coercion")
    from app.services.document_editors import _coerce

    for value, expected in [
        ("00123", "00123"),          # case number, not one hundred and twenty three
        ("+441234567", "+441234567"),  # phone number
        ("-0012", "-0012"),
        ("123", 123),
        ("4.5", 4.5),
        ("=A1*2", "=A1*2"),
        ("Contoso", "Contoso"),
    ]:
        check(f"{value!r} → {expected!r}", _coerce(value) == expected)


def test_failed_edit_leaves_no_litter():
    """A rejected edit must not leave a backup beside the user's document."""
    print("\nDocuments — failed edit cleanup")
    from app.services import document_editors as editors
    from app.services.document_readers import DocumentError

    folder = _sample_documents()
    excel = folder / "tickets.xlsx"
    before = set(folder.glob("*cerebro-backup*"))

    try:
        editors.apply(excel, [{"op": "set_cell", "sheet": "Ghost", "cell": "A1", "value": 1}])
    except DocumentError:
        pass

    check("No backup left behind by a rejected edit",
          set(folder.glob("*cerebro-backup*")) == before)


def test_settings_store():
    print("\nSettings")
    from app.core import settings_store

    described = settings_store.describe()
    check("Every group has fields",
          all(any(f["group"] == g["id"] for f in described["fields"])
              for g in described["groups"]))

    secrets = [f for f in described["fields"] if f["secret"]]
    check("Secrets are masked, never returned",
          all(f["value"] in ("", settings_store.SECRET_MASK) for f in secrets))

    result = settings_store.update({"PORT": "not-a-number"})
    check("Invalid values are rejected", result["ok"] is False)


def test_setup_and_package_contract():
    print("\nSetup and Windows package")
    from app.core.config import Settings

    setup = (ROOT / "backend" / "app" / "web" / "setup.html").read_text(encoding="utf-8")
    manifest = json.loads((ROOT / "browser-extension" / "src" / "manifest.json").read_text(encoding="utf-8"))
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

    check("Continue collects visible values before saving",
          "collectVisibleInputs();" in setup and "Save & continue" in setup)
    check("The built-in database can be tested without changing modes",
          'id="test-db"' in setup
          and setup.index('id="test-db"') > setup.index('id="db-url"')
          and "if (await saveStaged()) runTest('database', test)" in setup)
    check("Skip explicitly saves before advancing",
          "document.getElementById('skip').onclick = async" in setup
          and "const saved = await saveStaged();" in setup)
    check("Packaged extension instructions use the runtime folder",
          "runtime.extension_dir" in setup)
    installer = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    check("Installer extension shortcut targets the bundled asset folder",
          r'{app}\_internal\browser-extension' in installer)
    check("Browser extension version matches the release version",
          manifest["version"] == version)
    workflow = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text(encoding="utf-8")
    check("Every pull request runs the Windows package contract",
          "pull_request:\n    paths:" not in workflow)
    check("Release metadata is validated before dependencies are installed",
          workflow.index("- name: Validate release metadata")
          < workflow.index("- name: Install dependencies"))
    check("Release tags must match VERSION and current main",
          "does not match VERSION" in workflow
          and "Tag the tested main commit instead" in workflow)
    check("Extension metadata is checked before packaging",
          "Browser extension version" in workflow
          and "does not match VERSION" in workflow)
    check("Screenpipe integration defaults on", Settings().SCREENPIPE_ENABLED is True)
    check("CI builds the importable flow before PyInstaller collects assets",
          workflow.index("Build the Power Automate import package")
          < workflow.index("Build the executables"))
    check("Releases include the standalone Power Automate bridge",
          "packaging/power_automate/dist/Cerebro-Bridge.zip" in workflow
          and "name: Cerebro-Bridge" in workflow)

    popup = (ROOT / "browser-extension" / "src" / "popup.html").read_text(encoding="utf-8")
    manifest_text = (ROOT / "browser-extension" / "src" / "manifest.json").read_text(encoding="utf-8")
    background = (ROOT / "browser-extension" / "src" / "background.js").read_text(encoding="utf-8")
    check("The extension offers explicit current-page reading", "Read this page" in popup)
    check("Explicit capture uses a temporary tab grant",
          '"activeTab"' in manifest_text and '"scripting"' in manifest_text)
    check("Excluded domains also block explicit capture",
          "excludedDomains" in background and "CAPTURE_ACTIVE" in background)


def test_power_automate_package():
    """The generated ZIP must match Power Automate's exported package layout."""
    print("\nPower Automate package")
    import runpy
    import zipfile

    builder = runpy.run_path(
        str(ROOT / "packaging" / "build_power_automate_package.py"),
        run_name="cerebro_power_automate_builder",
    )
    package = builder["build"]()

    with zipfile.ZipFile(package) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
        flow_manifest_path = "Microsoft.Flow/flows/manifest.json"
        check("Package contains the Microsoft.Flow flow manifest",
              flow_manifest_path in names)
        flow_manifest = json.loads(archive.read(flow_manifest_path))
        flow_ids = flow_manifest["flowAssets"]["assetPaths"]

        root_flow_ids = {
            resource_id for resource_id, resource in manifest["resources"].items()
            if resource["type"] == "Microsoft.Flow/flows"
        }
        check("Flow manifest lists every packaged flow",
              set(flow_ids) == root_flow_ids and len(flow_ids) == 4)

        all_maps_resolve = True
        connector_auth_complete = True
        operations = {}

        def connector_nodes(value):
            if isinstance(value, dict):
                if str(value.get("type") or "").startswith("OpenApiConnection"):
                    yield value
                for child in value.values():
                    yield from connector_nodes(child)
            elif isinstance(value, list):
                for child in value:
                    yield from connector_nodes(child)

        def named_connector_nodes(value):
            if isinstance(value, dict):
                if str(value.get("type") or "").startswith("OpenApiConnection"):
                    operation = value.get("inputs", {}).get("host", {}).get("operationId")
                    if operation:
                        yield operation, value
                for child in value.values():
                    yield from named_connector_nodes(child)
            elif isinstance(value, list):
                for child in value:
                    yield from named_connector_nodes(child)

        for flow_id in flow_ids:
            base = f"Microsoft.Flow/flows/{flow_id}"
            required = {
                f"{base}/definition.json",
                f"{base}/apisMap.json",
                f"{base}/connectionsMap.json",
            }
            check(f"{flow_id[:8]} has definition and connector maps",
                  required.issubset(names))

            wrapped = json.loads(archive.read(f"{base}/definition.json"))
            apis_map = json.loads(archive.read(f"{base}/apisMap.json"))
            connections_map = json.loads(
                archive.read(f"{base}/connectionsMap.json"))
            references = wrapped["properties"]["connectionReferences"]
            all_maps_resolve = all_maps_resolve and (
                set(apis_map) == set(connections_map) == set(references)
                and all(asset in manifest["resources"]
                        for asset in (*apis_map.values(), *connections_map.values()))
            )
            definition = wrapped["properties"]["definition"]
            connector_auth_complete = connector_auth_complete and all(
                node.get("inputs", {}).get("authentication")
                == "@parameters('$authentication')"
                for node in connector_nodes(definition)
            )
            operations.update(dict(named_connector_nodes(definition)))

        check("Connector maps resolve to declared package resources",
              all_maps_resolve)
        check("Every connector action receives import-time authentication",
              connector_auth_complete)
        check("Polling mail and Teams triggers are not declared as webhooks",
              operations.get("OnNewEmailV3", {}).get("type") == "OpenApiConnection"
              and operations.get("OnNewChannelMessage", {}).get("type")
              == "OpenApiConnection")
        teams_trigger = operations.get("OnNewChannelMessage", {}).get("inputs", {})
        check("Teams channel trigger uses required groupId and channelId",
              teams_trigger.get("parameters", {}).get("groupId")
              == "SELECT_TEAM_AFTER_IMPORT"
              and teams_trigger.get("parameters", {}).get("channelId")
              == "SELECT_CHANNEL_AFTER_IMPORT"
              and "teamId" not in teams_trigger.get("parameters", {}))
        dataverse_trigger = operations.get("SubscribeWebhookTrigger", {}).get("inputs", {})
        check("Dataverse Case trigger uses the singular logical table name",
              dataverse_trigger.get("parameters", {}).get(
                  "subscriptionRequest/entityname") == "incident")
        teams_post = operations.get("PostMessageToChannelV3", {}).get("inputs", {})
        check("Teams outbound avoids import-time dynamic schema lookup",
              "PostMessageToConversation" not in operations
              and teams_post.get("parameters", {}).get("groupId")
              == "SELECT_TEAM_AFTER_IMPORT"
              and teams_post.get("parameters", {}).get("channelId")
              == "SELECT_CHANNEL_AFTER_IMPORT")
        check("Nonstandard placeholder package files are gone",
              "connections.json" not in names
              and not any(name.endswith("/flow.json") for name in names))


def test_screenpipe_current_api():
    print("\nScreenpipe API")
    from unittest.mock import Mock, patch
    from app.services.screenpipe_client import ScreenpipeClient

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "data": [{"type": "OCR", "content": {"app_name": "Notepad", "text": "case"}}]
    }
    with patch("app.services.screenpipe_client.requests.get", return_value=response) as request:
        records = ScreenpipeClient().get_screenshots(limit=3)
    check("Screenpipe content is read through /search",
          request.call_args.args[0].endswith("/search"))
    check("Screenpipe OCR records are returned", len(records) == 1)
    check("Screenpipe search sends the current content type",
          request.call_args.kwargs["params"]["content_type"] == "ocr")


# --------------------------------------------------------------------- chat
def test_chat_service():
    """Questions get answered, instructions become tasks, gaps get asked about."""
    print("\nChat — questions, instructions and clarification")
    from app.services.chat_service import ChatService
    from app.services.task_service import TaskService
    from app.models.task import Task

    db = session()
    chat = ChatService(db)

    # A question, with no AI provider configured, degrades to an honest
    # "can't answer" rather than becoming a garbled reminder.
    result = chat.handle_message("What does error 0x80040115 mean?")
    check("A question is answered, not turned into a task", result["kind"] == "answer")
    check("With no AI provider it says so", "Settings" in result["reply"])

    # A plain conversational statement used to become a dormant task.  It is
    # context for a back-and-forth, so it must receive an answer instead.
    before = db.query(Task).count()
    result = chat.handle_message("Outlook is broken again")
    check("A conversational statement receives an answer", result["kind"] == "answer")
    check("A conversational statement does not create a task",
          db.query(Task).count() == before)

    # An instruction missing what it needs to run is met with a clarifying
    # question instead of a task that would fail silently later.
    result = chat.handle_message("Keep the project log updated daily under my name")
    check("An incomplete instruction is not turned into a task right away",
          result["kind"] == "clarification")
    check("The clarifying question names what's missing", "document" in result["reply"].lower())

    # The next message answers that question and completes the original one.
    result = chat.handle_message("C:/logs/project-log.docx")
    check("A reply to a clarifying question creates the task",
          result["kind"] == "confirmation" and result.get("task"))
    task = db.query(Task).get(result["task"]["id"])
    check("The completed task carries the resolved document",
          json.loads(task.spec or "{}").get("document") == "C:/logs/project-log.docx")

    # A complete instruction needs no back-and-forth at all.
    result = chat.handle_message("Remind me to call the customer back tomorrow")
    check("A complete instruction creates the task immediately",
          result["kind"] == "confirmation")

    # Provider intent parsing may call an immediate chat request "manual".
    # Chat itself is already the trigger, so it must become runnable now.
    from unittest.mock import patch
    with patch("app.services.task_service.parse_instruction", return_value={
        "title": "Prepare the case summary", "kind": "summarise",
        "schedule": "manual", "at_time": None, "autonomous": False,
        "attribution": None, "spec": {},
    }):
        immediate = TaskService(db).create_from_instruction(
            "Prepare the case summary", source="chat")
    check("A manual intent asked in chat is scheduled once now",
          immediate.schedule == "once" and immediate.next_run is not None)

    history = chat.history(limit=50)
    check("Every turn is recorded", len(history) >= 8)
    check("History is chronological (oldest first)",
          history[0]["created_at"] <= history[-1]["created_at"])

    # With AI enabled, a question is answered directly rather than degrading.
    import app.services.llm_service as llm_module

    original_enabled = llm_module.LLMService.enabled
    original_chat = llm_module.LLMService.chat
    llm_module.LLMService.enabled = property(lambda self: True)
    llm_module.LLMService.chat = lambda self, messages, **kwargs: {
        "content": "Restart the print spooler.", "tool_calls": [], "mode": "native"}
    try:
        result = chat.handle_message("Why is the printer stuck?")
        check("An answer is generated when AI is on", result["reply"] == "Restart the print spooler.")
        check("It is still filed as an answer, not a task", result["kind"] == "answer")
    finally:
        llm_module.LLMService.enabled = original_enabled
        llm_module.LLMService.chat = original_chat

    db.close()


def test_chat_reference_images():
    """Reference images must be relevant, requested and not recently repeated."""
    print("\nChat — relevant reference images")
    from datetime import datetime, timedelta
    from unittest.mock import patch

    from app.models.tracked_document import TrackedDocument
    from app.services.chat_service import ChatService

    db = session()
    now = datetime.utcnow()
    db.add_all([
        TrackedDocument(
            path=str(Path(_TEMP_DIR) / "printer-guide.pdf"),
            name="Printer troubleshooting guide.pdf", kind="pdf",
            text_preview="Printer spooler, fuser and paper path troubleshooting diagram.",
            last_seen=now - timedelta(minutes=2),
        ),
        TrackedDocument(
            path=str(Path(_TEMP_DIR) / "vacation-guide.pdf"),
            name="Vacation guide.pdf", kind="pdf",
            text_preview="Beach resort map, restaurant list and airport transfers.",
            last_seen=now,
        ),
    ])
    db.commit()
    chat = ChatService(db)

    def fake_extract(path, kind, limit=2):
        name = "printer-reference.png" if "printer" in str(path) else "vacation-map.png"
        return [{"image": name, "caption": Path(path).name}]

    with patch("app.services.chat_images.extract_document_images",
               side_effect=fake_extract) as extract:
        ordinary = chat._document_images_for_answer(
            "Why is the printer spooler stuck?", {})
        check("Ordinary answers do not receive unsolicited images",
              ordinary == [] and extract.call_count == 0)

        relevant = chat._document_images_for_answer(
            "Show me the printer diagram", {})
        check("A visual request chooses the relevant document",
              [item["image"] for item in relevant] == ["printer-reference.png"])

        chat._store("assistant", "Here it is.", kind="answer",
                    meta={"images": relevant})
        repeated = chat._document_images_for_answer(
            "Show me the printer diagram again", {})
        check("A recently shown reference image is not duplicated",
              repeated == [])

    db.close()


def test_ask_tools_and_action_cards():
    """Ask reads services immediately and gates Power Automate writes."""
    print("\nChat — tools, previews and Power Automate approvals")
    from unittest.mock import patch

    from app.models.enterprise import EnterpriseAction
    from app.services.ask_tools import AskToolService
    from app.services.chat_service import ChatService
    from app.services.enterprise_service import EnterpriseService

    db = session()
    enterprise = EnterpriseService(db)
    ingested = enterprise.ingest_payload({
        "source": "outlook", "external_id": "ask-tools-message",
        "sender": "alex@example.com", "sender_name": "Alex",
        "subject": "Ask tools test deployment", "body": "Can you confirm the deployment?",
        "timestamp": "2026-09-22T12:00:00Z", "thread_id": "ask-tools-thread",
    }, source_file="ask-tools.json")
    message_id = ingested["id"]
    chat = ChatService(db)

    briefing = chat.handle_message("Summarize my inbox today")
    check("Ask routes inbox briefing to a read tool",
          briefing.get("tool") == "get_inbox_briefing")
    check("Tool results include visible progress and completion cards",
          {card.get("type") for card in briefing.get("cards", [])} ==
          {"progress", "completion"})

    fake_draft = {
        "message_id": message_id, "source": "outlook",
        "subject": "Re: Ask tools test deployment", "to": ["alex@example.com"],
        "chat_or_channel": None, "thread_id": "ask-tools-thread",
        "draft": "The deployment is complete.",
    }
    with patch("app.services.enterprise_service.EnterpriseService.draft_reply",
               return_value=fake_draft):
        drafted = chat.handle_message(f"Draft a reply to message {message_id}")
    action_id = drafted.get("action", {}).get("id")
    check("Ask creates a previewable outbound draft",
          drafted.get("kind") == "draft" and bool(action_id))
    check("Draft card carries approval controls",
          any(card.get("type") == "draft" and card.get("action_id") == action_id
              for card in drafted.get("cards", [])))
    check("Draft remains in the database before approval",
          db.query(EnterpriseAction).get(action_id).status == "draft")

    outbox = Path(_TEMP_DIR) / "outbox"
    before = len(list(outbox.glob("*.json"))) if outbox.exists() else 0
    approved = chat.handle_action(action_id, "approve")
    after = len(list(outbox.glob("*.json"))) if outbox.exists() else 0
    check("Explicit approval queues the Power Automate action",
          approved.get("action", {}).get("status") == "queued" and after == before + 1)
    check("Approved actions request a completion notification", approved.get("notify") is True)

    teams = chat.handle_message(
        "Post to Teams channel Support Escalations: Deployment is complete")
    teams_id = teams.get("action", {}).get("id")
    check("Ask exposes Teams as a previewed Power Automate action",
          teams.get("tool") == "send_teams_message" and bool(teams_id))
    discarded = chat.handle_action(teams_id, "discard")
    check("A preview can be discarded without entering the outbox",
          "Nothing was sent" in discarded.get("reply", ""))

    modes = {item["name"]: item["mode"] for item in AskToolService.catalog()}
    check("Tool catalogue distinguishes reads from approval-gated writes",
          modes.get("search_sources") == "read" and modes.get("send_email") == "approval")

    widget = (ROOT / "desktop" / "widget.py").read_text(encoding="utf-8")
    check("Widget renders structured Ask cards",
          "def _render_tool_card" in widget and "def _chat_action" in widget)
    check("Widget displays completion notifications",
          "Cerebro queued the approved Power Automate action" in widget)
    db.close()


def test_activity_state():
    """The tray and app header can always tell what Cerebro is doing."""
    print("\nActivity state")
    from app.core import activity_state

    activity_state.reset()
    check("Nothing running reads as idle", activity_state.snapshot()["state"] == "idle")

    with activity_state.activity("searching", "Searching sources"):
        with activity_state.activity("thinking", "Answering"):
            snap = activity_state.snapshot()
            check("The most important running state wins", snap["state"] == "thinking")
            check("Every running item is listed", len(snap["active"]) == 2)
        check("Finished work disappears",
              activity_state.snapshot()["state"] == "searching")

    try:
        with activity_state.activity("browsing", "Opening a case"):
            raise RuntimeError("page timed out")
    except RuntimeError:
        pass
    snap = activity_state.snapshot()
    check("A failure is shown briefly as an error",
          snap["state"] == "error" and "page timed out" in snap["detail"])
    activity_state.reset()
    check("Pending drafts show as awaiting approval",
          activity_state.snapshot(pending_approvals=2)["state"] == "awaiting_approval")

    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        body = client.get("/api/system/activity").json()
        check("Activity is served over the API", body.get("state") in activity_state.STATES)


def test_llm_chat_protocol():
    """Real conversation turns and tool calls work on every provider shape."""
    print("\nAI chat interface")
    from unittest import mock

    from app.core.config import settings
    from app.services import llm_service

    tools = [{"name": "search_knowledge", "description": "Search the KB",
              "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}]
    history = [
        {"role": "user", "content": "What fixes 0x80040115?"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "name": "search_knowledge", "arguments": {"query": "0x80040115"}}]},
        {"role": "tool", "tool_call_id": "call_1", "name": "search_knowledge",
         "content": "[K1] Outlook profile repair"},
    ]

    openai_shaped = llm_service._openai_messages(history, "system")
    check("OpenAI shape keeps the system prompt first", openai_shaped[0]["role"] == "system")
    check("Tool calls carry JSON-encoded arguments",
          openai_shaped[2]["tool_calls"][0]["function"]["arguments"] == '{"query": "0x80040115"}')
    check("Tool results reference their call", openai_shaped[3]["tool_call_id"] == "call_1")

    protocol = llm_service._to_json_protocol(history)
    check("The JSON protocol has no tool roles",
          all(m["role"] in ("user", "assistant") for m in protocol))
    check("The JSON protocol shows tool results as text", "[K1]" in protocol[-1]["content"])

    call = llm_service._extract_json_tool_call(
        '{"tool": "search_knowledge", "arguments": {"query": "vpn"}}', {"search_knowledge"})
    check("A JSON tool request is recognised", call and call["arguments"] == {"query": "vpn"})
    check("An ordinary answer is not mistaken for a tool call",
          llm_service._extract_json_tool_call("Restart the VPN client.", {"search_knowledge"}) is None)
    check("Unknown tools are ignored",
          llm_service._extract_json_tool_call('{"tool": "rm_rf"}', {"search_knowledge"}) is None)

    llm = LLMService()
    with mock.patch.object(settings, "LLM_PROVIDER", "ollama"), \
            mock.patch.object(type(settings), "llm_configured", new=property(lambda self: True)):
        unsupported = RuntimeError("Ollama returned 400: model does not support tools")
        replies = iter([unsupported, {"content": '{"tool": "search_knowledge", '
                                                 '"arguments": {"query": "vpn"}}', "tool_calls": []}])

        def fake_backend(messages, tool_list, system, max_tokens, temperature):
            value = next(replies)
            if isinstance(value, Exception):
                raise value
            return value

        with mock.patch.dict(llm_service._CHAT_BACKENDS, {"ollama": fake_backend}):
            llm_service._NO_NATIVE_TOOLS.clear()
            result = llm.chat([{"role": "user", "content": "vpn?"}], tools=tools)
        check("A model without tool support falls back to the JSON protocol",
              result["mode"] == "json" and result["tool_calls"][0]["name"] == "search_knowledge")
        check("The fallback is remembered for the session",
              ("ollama", settings.OLLAMA_MODEL) in llm_service._NO_NATIVE_TOOLS)
        llm_service._NO_NATIVE_TOOLS.clear()


ONBOARDING_DOC = (
    "Quarterly onboarding guide for new support engineers. This document explains how the "
    "team handles escalations, the on-call rotation, and how to request access to the CRM, "
    "the knowledge base and the VPN. New engineers should shadow two calls per day during "
    "their first week and review the escalation matrix with their lead. Access requests go "
    "through the service portal; approvals typically take two business days. Complete the "
    "security training before your first customer call. The rotation schedule is published "
    "in the team calendar every Monday. Questions about payroll or benefits go to HR."
)


class ScriptedLLM:
    """Stands in for LLMService.chat: replays scripted turns, records requests."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def __call__(self, llm_self, messages, tools=None, system=None, **kwargs):
        self.requests.append({"messages": [dict(m) for m in messages],
                              "tools": [t["name"] for t in (tools or [])],
                              "system": system})
        turn = self.turns.pop(0) if self.turns else "Done."
        if isinstance(turn, dict):
            return {"content": "", "tool_calls": [{"id": f"call_{len(self.requests)}",
                                                   **turn}], "mode": "native"}
        return {"content": turn, "tool_calls": [], "mode": "native"}


def _with_ai(script):
    from unittest import mock

    import app.services.llm_service as llm_module

    return (mock.patch.object(llm_module.LLMService, "enabled",
                              new=property(lambda self: True)),
            mock.patch.object(llm_module.LLMService, "chat",
                              new=lambda self, messages, **kwargs: script(self, messages, **kwargs)))


def test_ask_relevance():
    """An open document is not evidence for an unrelated question."""
    print("\nAsk — source relevance")
    from app.services.rag_service import RAGService
    from app.services.source_service import SourceService
    from app.services.chat_service import ChatService
    from app.services import text_chunks

    db = session()
    SourceService(db).observe("document", "C:/docs/onboarding.docx", "Onboarding guide.docx",
                              local_path="C:/docs/onboarding.docx", content=ONBOARDING_DOC)
    RAGService(db).index_document({
        "title": "Outlook 0x80040115", "source": "RightAnswers", "content": (
            "Outlook error 0x80040115 means Outlook cannot reach Exchange. Repair the "
            "Outlook profile in Control Panel > Mail, then disable cached mode.")})

    sources = SourceService(db)
    floor = 0.12
    check("An unrelated question gets no excerpts from the open document",
          sources.context_for_query("What's the capital of France?", min_score=floor) == [])
    check("A question about the open document still finds it",
          bool(sources.context_for_query("how do I request VPN access?", min_score=floor)))
    check("Without a floor the old behaviour is unchanged",
          isinstance(sources.context_for_query("capital of France"), list))

    hits = RAGService(db).search("How do I fix Outlook error 0x80040115?", min_score=floor)
    check("A relevant knowledge article clears the floor",
          any(hit["title"] == "Outlook 0x80040115" for hit in hits))
    unrelated = RAGService(db).search("What's the capital of France?", min_score=floor)
    check("An unrelated question gets no knowledge citations", unrelated == [],
          [hit["title"] for hit in unrelated])

    scored = [(0.5, "a"), (0.3, "b"), (0.1, "c")]
    check("Weak hits riding along with a strong one are dropped",
          [item for _, item in text_chunks.relevant(scored, 0.12)] == ["a", "b"])

    # No AI provider: the fallback reply depends on the question.
    chat = ChatService(db)
    first = chat.handle_message("What's the capital of France?")
    second = chat.handle_message("How do I fix Outlook error 0x80040115?")
    check("The no-AI reply does not list the open document for an unrelated question",
          "Onboarding" not in first["reply"] and not first.get("sources"))
    check("Different questions get different no-AI replies", first["reply"] != second["reply"])
    db.close()


def test_ask_agent_loop():
    """The model can look things up, and its answer is about the new question."""
    print("\nAsk — agent loop")
    from app.services.chat_service import ChatService
    from app.services.source_service import SourceService
    from app.models.task import Task
    from app.models.enterprise import EnterpriseAction

    db = session()
    SourceService(db).observe("document", "C:/docs/onboarding.docx", "Onboarding guide.docx",
                              local_path="C:/docs/onboarding.docx", content=ONBOARDING_DOC)
    chat = ChatService(db)

    # 1. A tool call, then an answer that cites what the tool found.
    script = ScriptedLLM([
        {"name": "search_knowledge", "arguments": {"query": "outlook 0x80040115"}},
        "Repair the Outlook profile, then turn off cached mode [K1].",
    ])
    events = []
    enabled, patched = _with_ai(script)
    with enabled, patched:
        result = chat.handle_message("How do I fix Outlook error 0x80040115?", emit=events.append)
    check("The model was offered tools", "search_knowledge" in script.requests[0]["tools"])
    check("The tool result is fed back to the model",
          any(m["role"] == "tool" and "0x80040115" in m["content"]
              for m in script.requests[1]["messages"]))
    check("Cited sources are returned", [s["ref"] for s in result["sources"]] == ["K1"])
    check("Progress is reported while the tool runs",
          any(e.get("status") == "running" for e in events)
          and any(e.get("status") == "complete" for e in events))
    check("The answer records which tools it used", result["tools_used"] == ["search_knowledge"])

    # 2. The next, unrelated question: the previous answer is a separate
    # assistant turn, the new question comes last, no stale document.
    script = ScriptedLLM(["Paris."])
    enabled, patched = _with_ai(script)
    with enabled, patched:
        result = chat.handle_message("What's the capital of France?")
    messages = script.requests[0]["messages"]
    check("History is sent as real turns",
          any(m["role"] == "assistant" and "cached mode" in m["content"] for m in messages[:-1]))
    check("The new question is the last thing the model reads",
          messages[-1]["role"] == "user" and messages[-1]["content"].endswith("What's the capital of France?"))
    check("An unrelated open document is not attached to the question",
          "Onboarding" not in messages[-1]["content"])
    check("An answer without sources cites nothing", result["sources"] == [] and result["reply"] == "Paris.")

    # 3. A draft that just repeats the previous answer is sent back once.
    script = ScriptedLLM(["Paris.", "Berlin is the capital of Germany."])
    previous = "Paris is the capital of France and has been for a very long time, " \
               "since the early medieval period; it is also its largest city."
    script.turns = [previous, "Berlin is the capital of Germany."]
    chat._store("assistant", previous, kind="answer")
    enabled, patched = _with_ai(script)
    with enabled, patched:
        result = chat.handle_message("And Germany?")
    check("A repeated answer is caught and re-asked", len(script.requests) == 2)
    check("The final answer is about the new question", result["reply"].startswith("Berlin"))

    # 4. Ordinary questions that used to trip keyword shortcuts reach the model.
    drafts_before = db.query(EnterpriseAction).count()
    tasks_before = db.query(Task).count()
    for question in ("How do I send a message in Teams?",
                     "Any update on the dialog backlog?",
                     "How do I find files in SharePoint?"):
        script = ScriptedLLM([f"Answer to: {question}"])
        enabled, patched = _with_ai(script)
        with enabled, patched:
            result = chat.handle_message(question)
        check(f"“{question}” is answered by the model", result["reply"] == f"Answer to: {question}")
    check("No drafts were created by keyword matching",
          db.query(EnterpriseAction).count() == drafts_before)
    check("No tasks were created by keyword matching", db.query(Task).count() == tasks_before)

    # 5. When the user does want a task, the model creates it through a tool.
    script = ScriptedLLM([
        {"name": "create_task", "arguments": {"instruction": "Remind me tomorrow at 9am to call Contoso"}},
        "Done — I'll remind you tomorrow at 9am.",
    ])
    enabled, patched = _with_ai(script)
    with enabled, patched:
        result = chat.handle_message("remind me tomorrow at 9 to call Contoso")
    check("A task is created through the create_task tool",
          db.query(Task).count() == tasks_before + 1 and result.get("task"))

    # 6. Typed approval still works exactly, without the model.
    from app.services.ask_tools import AskToolService
    action = AskToolService(db).enterprise.create_action(
        "send_email", body="Hi", source="outlook", to=["a@example.com"], send=False)
    script = ScriptedLLM([])
    enabled, patched = _with_ai(script)
    with enabled, patched:
        result = chat.handle_message("approve")
    check("Typed approval is handled without the model", not script.requests)
    check("Typed approval queues the draft", db.query(EnterpriseAction).get(action.id).status != "draft")

    # 7. A failing provider is explained, not raised.
    def broken(self, messages, **kwargs):
        raise RuntimeError("connection refused")
    enabled, _ = _with_ai(script)
    from unittest import mock
    import app.services.llm_service as llm_module
    with enabled, mock.patch.object(llm_module.LLMService, "chat", new=broken):
        result = chat.handle_message("Why is VPN slow?")
    check("Provider failures are explained in the reply", "connection refused" in result["reply"])

    from app.services.agent import catalog
    modes = {item["name"]: item["mode"] for item in catalog()}
    check("Every external write is approval-gated or a draft",
          modes["send_email"] == "approval" and modes["draft_reply"] == "draft")
    db.close()


def test_chat_stream():
    """Ask progress and the final answer arrive as Server-Sent Events."""
    print("\nAsk — streaming")
    import json as _json

    from fastapi.testclient import TestClient
    from app.main import app

    script = ScriptedLLM([
        {"name": "search_sources", "arguments": {"query": "vpn access"}},
        "Request it through the service portal [S1].",
    ])
    enabled, patched = _with_ai(script)
    with enabled, patched, TestClient(app) as client:
        response = client.post("/api/chat/stream", json={"message": "How do I get VPN access?"})
        body = response.text
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line
                     and not line.startswith(":"))
        if "event" in lines:
            events.append((lines["event"], _json.loads(lines["data"])))
    kinds = [kind for kind, _ in events]
    check("The stream is served as Server-Sent Events",
          response.headers["content-type"].startswith("text/event-stream"))
    check("Progress cards arrive before the answer",
          "card" in kinds and kinds.index("card") < kinds.index("message"))
    check("The stream ends with the final message",
          kinds[-1] == "message" and "service portal" in events[-1][1]["reply"])


def _fake_sites():
    """Two tiny local web apps standing in for Dynamics 365 and RightAnswers.

    Both require a session cookie (set by visiting /login?done=1) and send the
    browser to /login without it, the way the real systems redirect to a
    sign-in page.
    """
    import http.server
    import json as _json
    import re as _re
    import threading
    from urllib.parse import parse_qs, unquote, urlparse

    guid = "11111111-2222-3333-4444-555555555555"
    state = {
        "case": {"incidentid": guid, "ticketnumber": "CAS-01234-ABCDE",
                 "title": "Outlook cannot connect", "description": "User sees 0x80040115.",
                 "statecode": 0, "statuscode": 1, "prioritycode": 2, "severitycode": 1,
                 "caseorigincode": 1, "createdon": "2026-09-30T10:00:00Z",
                 "modifiedon": "2026-10-01T09:00:00Z", "_customerid_value": "c1",
                 "_customerid_value@OData.Community.Display.V1.FormattedValue": "Contoso",
                 "_ownerid_value": "o1",
                 "_ownerid_value@OData.Community.Display.V1.FormattedValue": "Sam Agent",
                 "statuscode@OData.Community.Display.V1.FormattedValue": "In Progress",
                 "prioritycode@OData.Community.Display.V1.FormattedValue": "Normal"},
        "notes": [{"subject": "Called user", "notetext": "Asked for a screenshot.",
                   "createdon": "2026-09-30T11:00:00Z"}],
        "articles": {"KB100": {"title": "Fix Outlook 0x80040115",
                               "body": "Repair the Outlook profile, then disable cached mode."},
                     "KB200": {"title": "Reset VPN client",
                               "body": "Remove and re-add the VPN profile."},
                     "KB300": {"title": "Reset a forgotten password",
                               "body": "Open the self-service portal, choose Forgot password, "
                                       "confirm the code sent to your phone, then choose a new "
                                       "password of at least fourteen characters."}},
        "requests": [],
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        site = "dynamics"

        def log_message(self, *args):
            pass

        def _signed_in(self):
            return "session=ok" in (self.headers.get("Cookie") or "")

        def _send(self, code, body="", kind="text/html", headers=None):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def _json(self, payload, code=200):
            self._send(code, _json.dumps(payload), "application/json")

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length).decode("utf-8") if length else ""

        def _login(self, query):
            if query.get("done"):
                self._send(302, "", headers={"Set-Cookie": "session=ok; Path=/",
                                             "Location": "/home"})
            else:
                self._send(200, "<h1>Sign in</h1><a href='/login?done=1'>Sign in</a>")

        def do_GET(self):
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/login":
                return self._login(query)
            if not self._signed_in():
                if url.path.startswith("/api/"):
                    return self._json({"error": {"message": "unauthorised"}}, 401)
                return self._send(302, "", headers={"Location": "/login"})
            if self.site == "dynamics":
                return self._dynamics_get(url, query)
            return self._rightanswers_get(url, query)

        def do_POST(self):
            url = urlparse(self.path)
            if not self._signed_in():
                return self._json({"error": {"message": "unauthorised"}}, 401)
            body = self._body()
            state["requests"].append(("POST", url.path, body))
            if self.site == "dynamics":
                if url.path.endswith("/annotations"):
                    data = _json.loads(body)
                    state["notes"].insert(0, {"subject": data["subject"],
                                              "notetext": data["notetext"],
                                              "createdon": "2026-10-01T12:00:00Z"})
                    return self._send(204, "", headers={
                        "OData-EntityId": "/api/data/v9.2/annotations(abc)"})
                if url.path.endswith("/CloseIncident"):
                    state["case"]["statecode"] = 1
                    return self._send(204, "")
            elif url.path == "/kb/save":
                form = {k: v[0] for k, v in parse_qs(body).items()}
                state["articles"][form["docid"]].update(
                    {"title": form["kbTitle"], "body": form["kbBody"]})
                return self._send(302, "", headers={"Location": f"/kb/view?docid={form['docid']}"})
            else:
                form = {k: v[0] for k, v in parse_qs(body).items()}
                article = state["articles"].setdefault(form["id"], {})
                article.update({"title": form.get("title", article.get("title")),
                                "body": form.get("body", article.get("body"))})
                return self._send(302, "", headers={
                    "Location": f"/portal/app/portlets/results/viewsolution.jsp?solutionid={form['id']}"})
            self._json({"error": {"message": "not found"}}, 404)

        def do_PATCH(self):
            url = urlparse(self.path)
            if not self._signed_in():
                return self._json({"error": {"message": "unauthorised"}}, 401)
            body = _json.loads(self._body())
            state["requests"].append(("PATCH", url.path, body))
            state["case"].update(body)
            self._send(204, "")

        # ------------------------------------------------------- dynamics
        def _dynamics_get(self, url, query):
            path = url.path
            if path in ("/main.aspx", "/home"):
                return self._send(200, "<title>Dynamics 365</title><main>Dashboard</main>")
            if path.endswith("/WhoAmI"):
                return self._json({"UserId": "u1"})
            if "/systemusers(" in path:
                return self._json({"fullname": "Sam Agent"})
            if "EntityDefinitions" in path:
                # This org renamed "High" to "Urgent" and added its own status.
                options = {
                    "prioritycode": [(1, "Urgent"), (2, "Normal"), (3, "Low")],
                    "statuscode": [(1, "In Progress"), (3, "Waiting on customer")],
                }
                field = next((name for name in options if name in path), None)
                if field is None:
                    return self._json({"error": {"message": "not found"}}, 404)
                return self._json({"OptionSet": {"Options": [
                    {"Value": value, "Label": {"UserLocalizedLabel": {"Label": label}}}
                    for value, label in options[field]]}})
            if path.endswith("/incidents"):
                flt = unquote(query.get("$filter", ""))
                case = state["case"]
                match = _re.search(r"ticketnumber eq '([^']+)'", flt)
                rows = [case] if (not flt or (match and match.group(1) == case["ticketnumber"])
                                  or ("contains" in flt and "outlook" in flt.lower())) else []
                return self._json({"value": rows})
            if "/incidents(" in path:
                return self._json(state["case"])
            if path.endswith("/annotations"):
                return self._json({"value": state["notes"]})
            if path.endswith("/activitypointers"):
                return self._json({"value": []})
            return self._json({"error": {"message": "not found"}}, 404)

        # --------------------------------------------------- rightanswers
        def _rightanswers_get(self, url, query):
            path = url.path
            if path in ("/home", "/portal", "/portal/"):
                return self._send(200, "<main>RightAnswers portal</main>")
            if path == "/portal/ss/":
                text = query.get("searchText", "").lower()
                items = "".join(
                    f"<li class='result' data-solution-id='{key}'>"
                    f"<a href='/portal/app/portlets/results/viewsolution.jsp?solutionid={key}'>"
                    f"<span class='title'>{a['title']}</span></a>"
                    f"<p class='snippet'>{a['body'][:60]}</p></li>"
                    for key, a in state["articles"].items()
                    if any(word in (a["title"] + a["body"]).lower() for word in text.split()))
                return self._send(200, f"<main><ul>{items}</ul></main>")
            if path.endswith("viewsolution.jsp"):
                key = query.get("solutionid")
                article = state["articles"].get(key)
                if not article:
                    return self._send(404, "<main>Not found</main>")
                return self._send(200, (
                    f"<main><h1>{article['title']}</h1>"
                    f"<div class='solution-body'>{article['body']}</div>"
                    f"<a href='/edit?solutionid={key}'>Edit</a></main>"))
            # A second portal layout that matches none of the default
            # selectors — standing in for a company's customised portal.
            if path == "/kb/search":
                text = query.get("q", "").lower()
                hits = "".join(
                    f"<div class='hit'><h5><a href='/kb/view?docid={key}'>{a['title']}</a></h5>"
                    f"<span class='blurb'>{a['body'][:50]}</span></div>"
                    for key, a in state["articles"].items()
                    if any(word in (a["title"] + a["body"]).lower() for word in text.split()))
                return self._send(200, (
                    "<div id='page'><div class='topnav'><a href='/kb/home'>Home</a></div>"
                    f"<div class='hits'>{hits}</div></div>"))
            if path == "/kb/view":
                key = query.get("docid")
                article = state["articles"].get(key)
                if not article:
                    return self._send(404, "<div>Not found</div>")
                return self._send(200, (
                    "<div id='page'><div class='crumbs'>Home › Accounts</div>"
                    f"<div id='kbArticle'><h2 class='kb-title'>{article['title']}</h2>"
                    f"<div class='kb-text'>{article['body']}</div></div>"
                    f"<a href='/kb/edit?docid={key}'>Edit article</a></div>"))
            if path == "/kb/edit":
                key = query.get("docid")
                article = state["articles"][key]
                return self._send(200, (
                    "<form method='post' action='/kb/save'>"
                    f"<input type='hidden' name='docid' value='{key}'>"
                    "<input type='hidden' name='__VIEWSTATE' value='SECRET-TOKEN'>"
                    f"<input id='kbTitle' name='kbTitle' value='{article['title']}'>"
                    f"<textarea id='kbBody' name='kbBody'>{article['body']}</textarea>"
                    "<button type='submit'>Save article</button></form>"))
            if path == "/edit":
                key = query.get("solutionid")
                article = state["articles"][key]
                return self._send(200, (
                    "<main><form method='post' action='/save'>"
                    f"<input type='hidden' name='id' value='{key}'>"
                    f"<input name='title' value='{article['title']}'>"
                    f"<textarea name='body'>{article['body']}</textarea>"
                    "<button type='submit'>Save</button></form></main>"))
            return self._send(404, "<main>Not found</main>")

    servers = {}
    for site in ("dynamics", "rightanswers"):
        handler = type(f"{site}Handler", (Handler,), {"site": site})
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers[site] = server
    return servers, state


def test_browser_integrations():
    """The hidden browser reads and (after approval) writes Dynamics and RightAnswers."""
    print("\nHidden browser — Dynamics 365 and RightAnswers")
    import sys as _sys
    from unittest import mock

    from app.core.config import settings
    from app.services import browser
    import importlib

    # ``app.services.browser.engine`` the module, not the ``engine()`` accessor
    # the package re-exports under the same name.
    engine_module = importlib.import_module("app.services.browser.engine")
    from app.services.browser.engine import BrowserUnavailable, playwright_installed

    if not playwright_installed():
        print("  - skipped: Playwright is not installed")
        return

    servers, state = _fake_sites()
    dynamics_url = f"http://127.0.0.1:{servers['dynamics'].server_port}"
    rightanswers_url = f"http://127.0.0.1:{servers['rightanswers'].server_port}/portal"
    profile = Path(_TEMP_DIR) / "browser_profile"
    overrides = {
        "BROWSER_AUTOMATION_ENABLED": True, "DYNAMICS_ENABLED": True,
        "RIGHTANSWERS_ENABLED": True, "DYNAMICS_URL": dynamics_url,
        "RIGHTANSWERS_URL": rightanswers_url, "BROWSER_MODE": "headless",
        "BROWSER_CHANNEL": "msedge" if _sys.platform == "win32" else "chromium",
        "BROWSER_TIMEOUT_SECONDS": 15,
    }
    patches = [mock.patch.object(settings, key, value) for key, value in overrides.items()]
    patches.append(mock.patch.object(engine_module, "BROWSER_PROFILE_DIR", profile))
    for patch in patches:
        patch.start()
    db = session()
    try:
        dynamics = browser.get("dynamics")
        rightanswers = browser.get("rightanswers")
        check("Both integrations are registered and enabled",
              dynamics.enabled and rightanswers.enabled)

        try:
            status = dynamics.check()
        except BrowserUnavailable as exc:
            print(f"  - skipped: no browser could be started ({exc})")
            return
        if "No browser could be started" in status.get("detail", ""):
            print(f"  - skipped: {status['detail'][:160]}")
            return
        check("Before sign-in the session is reported as not signed in",
              status["signed_in"] is False)
        try:
            dynamics.search_cases("outlook")
            raised = None
        except browser.SignInRequired as exc:
            raised = exc
        check("Work without a session asks the user to sign in", raised is not None)

        # Sign in the way the visible window would: visit the login page once.
        browser.engine().submit(lambda eng: eng.page("signin").goto(f"{dynamics_url}/login?done=1"))
        browser.engine().submit(lambda eng: eng.page("signin").goto(f"{rightanswers_url[:-7]}/login?done=1"))
        signed = dynamics.check()
        check("After sign-in the session is recognised", signed["signed_in"] is True)
        check("The check says who is signed in", signed.get("account") == "Sam Agent")
        # The browser closes when idle and after the sign-in window; a session
        # cookie must survive that, or every restart would mean signing in again.
        browser.engine().submit(lambda eng: eng.close_context())
        check("The sign-in survives the browser restarting", dynamics.check()["signed_in"] is True)

        cases = dynamics.search_cases("outlook")
        check("Dynamics cases are found through the Web API",
              cases and cases[0]["ticket"] == "CAS-01234-ABCDE")
        check("Formatted values come back readable", cases[0]["customer"] == "Contoso")
        record = dynamics.get_case("CAS-01234-ABCDE")
        check("A case is read with its timeline notes",
              record["notes"] and record["notes"][0]["subject"] == "Called user")

        results = rightanswers.search("outlook")
        check("RightAnswers search returns articles",
              results and results[0]["id"] == "KB100")
        article = rightanswers.get_article("KB100")
        check("A RightAnswers article is read in full",
              article["title"] == "Fix Outlook 0x80040115" and "cached mode" in article["body"])

        # Through Ask's tools: reads run now, writes wait for approval.
        from app.services.agent import actions
        from app.services.agent.registry import REGISTRY, ToolContext
        from app.models.agent_action import AgentAction

        ctx = ToolContext(db=db, context={})
        result = REGISTRY["dynamics_get_case"].handler(ctx, case="CAS-01234-ABCDE")
        check("The case tool cites the case", "[CRM1]" in result["content"])
        from app.models.case import Case
        mirrored = db.query(Case).filter(Case.case_id == "CAS-01234-ABCDE").first()
        check("The case is mirrored locally with its Dynamics link",
              mirrored is not None and mirrored.url and mirrored.external_id)

        before_requests = len(state["requests"])
        REGISTRY["dynamics_add_note"].handler(ctx, case="CAS-01234-ABCDE",
                                              text="Profile repaired; user confirmed.")
        refused = REGISTRY["dynamics_update_case"].handler(ctx, case="CAS-01234-ABCDE",
                                                           fields={"priority": "High"})
        check("A label this org doesn't use is refused, listing its own labels",
              "Urgent" in refused["content"]
              and not any(c.get("tool") == "dynamics_update_case" for c in ctx.drafts))
        REGISTRY["dynamics_update_case"].handler(ctx, case="CAS-01234-ABCDE",
                                                 fields={"priority": "Urgent"})
        REGISTRY["rightanswers_update_article"].handler(
            ctx, article="KB100", body="Repair the profile. Disable cached mode. Rebuild the OST.")
        check("Proposing changes sends nothing", len(state["requests"]) == before_requests)
        drafts = [card for card in ctx.drafts if card["type"] == "approval"]
        check("Each change becomes an approval card", len(drafts) == 3)
        update_card = next(c for c in drafts if c["tool"] == "dynamics_update_case")
        check("The update card shows before and after in the org's own words",
              update_card["preview"]["fields"][0] == {"name": "Priority", "before": "Normal",
                                                       "after": "Urgent"},
              update_card["preview"]["fields"])

        for card in drafts:
            outcome = actions.approve(db, card["action_id"])
            check(f"Approved {card['tool']} runs", outcome.get("notify") is True, outcome.get("reply"))
        check("The note reached Dynamics",
              state["notes"][0]["notetext"] == "Profile repaired; user confirmed.")
        check("The priority changed in Dynamics", state["case"]["prioritycode"] == 1)
        check("The article changed in RightAnswers",
              "Rebuild the OST" in state["articles"]["KB100"]["body"])
        check("Approved changes are recorded as done",
              all(db.get(AgentAction, c["action_id"]).status == "done" for c in drafts))

        discard_ctx = ToolContext(db=db, context={})
        REGISTRY["dynamics_resolve_case"].handler(discard_ctx, case="CAS-01234-ABCDE",
                                                  resolution="Fixed")
        card = discard_ctx.drafts[0]
        actions.discard(db, card["action_id"])
        check("A discarded change never runs", state["case"]["statecode"] == 0)

        from fastapi.testclient import TestClient
        from app.main import app

        with TestClient(app) as client:
            listed = client.get("/api/integrations").json()
        check("Integrations are listed over the API",
              {item["name"] for item in listed["integrations"]}
              == {"dynamics", "rightanswers", "sharepoint"})
    finally:
        browser.engine().shutdown()
        for patch in reversed(patches):
            patch.stop()
        for server in servers.values():
            server.shutdown()
        db.close()


def test_browser_disabled_by_default():
    """Nothing launches a browser unless it is switched on."""
    print("\nHidden browser — off by default")
    from app.core.config import settings
    from app.services import browser
    from app.services.agent.registry import ToolContext, available_tools

    check("The hidden browser is off by default", settings.BROWSER_AUTOMATION_ENABLED is False)
    try:
        browser.engine().submit(lambda eng: None)
        refused = False
    except browser.BrowserUnavailable:
        refused = True
    check("Work is refused while it is off", refused)
    names = {item.name for item in available_tools(ToolContext(db=None))}
    check("Integration tools are hidden from the model while off",
          not any(name.startswith(("dynamics_", "rightanswers_")) for name in names))
    check("Integration writes are approval-gated",
          all(browser_tool.mode == "approval" for browser_tool in __import__(
              "app.services.agent.registry", fromlist=["REGISTRY"]).REGISTRY.values()
              if browser_tool.name.startswith(("dynamics_add", "dynamics_update",
                                               "dynamics_resolve", "rightanswers_update",
                                               "rightanswers_create"))))


def test_dynamics_case_prefetch():
    """Opening a Dynamics case starts a background read only when Dynamics is on."""
    print("\nDynamics — background case read")
    from unittest import mock

    from app.services.agent import integration_tools

    with mock.patch.object(integration_tools, "prefetch_dynamics_case",
                           return_value=True) as prefetch:
        db = session()
        engine = ContextEngine(db)
        engine.process_event(EventCreate(event_type="CRM_CASE_OPENED", source="test",
                                         case_id="CAS-77777-ZZZZZ",
                                         data={"system": "Dynamics 365", "case_id": "CAS-77777-ZZZZZ"}))
        db.close()
    check("A Dynamics case opening asks for a background read",
          prefetch.call_args and prefetch.call_args[0][0] == "CAS-77777-ZZZZZ")
    integration_tools._PREFETCHED.clear()
    check("Nothing is fetched while Dynamics is switched off",
          integration_tools.prefetch_dynamics_case("CAS-77777-ZZZZZ") is False)


def test_tray_brain():
    """Every activity state has its own tray animation, and the tray follows it."""
    print("\nTray brain")
    sys.path.insert(0, str(ROOT / "desktop"))
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("  - skipped: Pillow is not installed")
        return
    import brain_frames
    import tray

    sizes = {}
    for state in brain_frames.STATES:
        loop = brain_frames.frames(state, 32)
        sizes[state] = (len(loop), loop[0].size)
    check("Every state has an animation loop",
          all(count >= 8 and size == (32, 32) for count, size in sizes.values()), sizes)
    idle = brain_frames.frames("idle", 32)
    check("The idle animation actually moves", idle[0].tobytes() != idle[len(idle) // 4].tobytes())
    def average(image):
        pixels = [p for p in image.getdata() if p[3] > 200]
        return tuple(sum(p[i] for p in pixels) / len(pixels) for i in range(3))

    r, g, b = average(brain_frames.frames("offline", 64)[0])
    check("Offline is drawn in grey", abs(r - g) < 8 and abs(g - b) < 8, (r, g, b))
    red = average(brain_frames.frames("error", 64)[0])
    calm = average(brain_frames.frames("thinking", 64)[0])
    check("Errors flush the brain red", red[0] - red[2] > calm[0] - calm[2], (red, calm))
    check("Unknown states fall back to idle", len(brain_frames.frames("bogus", 32)) == len(idle))
    check("Working states show the laptop brain, research the studying one",
          brain_frames.sprite_for("browsing") == "working"
          and brain_frames.sprite_for("searching") == "studying")
    first = brain_frames.raw_frames("working")[0]
    check("The pixel art keeps its transparent background",
          first.getpixel((0, 0))[3] == 0 and first.getchannel("A").getextrema() == (0, 255))

    class FakeIcon:
        def __init__(self):
            self.title, self.notes = "", []

        def update_menu(self):
            pass

        def notify(self, message, title):
            self.notes.append(message)

    brain = tray.BrainTray("http://127.0.0.1:1", actions={})
    brain.icon = FakeIcon()
    brain._on_activity({"state": "browsing", "detail": "Reading CAS-01234", "pending_approvals": 0})
    check("The tray follows the live state", brain.state == "browsing")
    check("The tooltip says what Cerebro is doing", "Reading CAS-01234" in brain.icon.title)
    brain._on_activity({"state": "awaiting_approval", "detail": "1 draft", "pending_approvals": 1})
    check("A new approval raises a notification", len(brain.icon.notes) == 1)
    brain._on_activity({"state": "awaiting_approval", "detail": "1 draft", "pending_approvals": 1})
    check("The same approval is not announced twice", len(brain.icon.notes) == 1)


def test_desktop_shell():
    """Closing hides to the tray, one instance runs, and Quit stops the server."""
    print("\nDesktop shell")
    sys.path.insert(0, str(ROOT / "desktop"))
    from unittest import mock

    import shell
    import widget_config

    lock = Path(_TEMP_DIR) / "shell.lock"
    with mock.patch.object(shell, "LOCK_PATH", lock):
        first, second = shell.SingleInstance(), shell.SingleInstance()
        check("The first shell takes the instance lock", first.acquire())
        check("A second launch does not start another shell", not second.acquire())

    request_file = Path(_TEMP_DIR) / "show.request"
    with mock.patch.object(shell, "SHOW_REQUEST", request_file), \
            mock.patch.object(widget_config, "load", return_value=dict(widget_config.DEFAULTS)):
        app = shell.Shell("http://127.0.0.1:1")
        app.window = mock.MagicMock()
        shell.SingleInstance.ask_running_instance_to_show("activity")
        app._check_show_request()
        check("A second launch brings the running window back",
              app.window.show.called and not request_file.exists())
        check("…on the requested tab",
              "cerebroSelectTab('activity')" in app.window.evaluate_js.call_args[0][0])

        app.window.reset_mock()
        check("Closing the window only hides it", app._on_closing() is False and app.window.hide.called)
        with mock.patch("requests.post") as post:
            app.quit()
        check("Quit asks the server to stop", post.call_args[0][0].endswith("/api/system/shutdown"))
        check("Quit really closes the window", app._on_closing() is True and app.window.destroy.called)

        api = shell.ShellApi(app)
        with mock.patch("webbrowser.open") as opened:
            api.open_external("javascript:alert(1)")
            api.open_external("https://example.com")
        check("Only web links are opened externally", opened.call_count == 1)

    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    from app.api import system

    with TestClient(fastapi_app) as client:
        refused = client.post("/api/system/shutdown")
    check("Shutdown is refused unless it comes from this computer", refused.status_code == 403)

    server = mock.MagicMock(should_exit=False)
    request = mock.MagicMock()
    request.client.host = "127.0.0.1"
    request.app.state.uvicorn_server = server
    import time as _time
    system.shutdown(request)
    _time.sleep(0.5)
    check("A local shutdown stops the server cleanly", server.should_exit is True)


def test_app_page():
    """The modern app page is served and wired to the live endpoints."""
    print("\nApp page")
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as client:
        page = client.get("/app")
        script = client.get("/static/ui/app.js")
        brain = client.get("/static/ui/brain.js")
    check("The app page is served", page.status_code == 200 and "Cerebro" in page.text)
    check("Its scripts are served", script.status_code == 200 and brain.status_code == 200)
    check("Ask streams progress", "/api/chat/stream" in script.text)
    check("The header follows live activity", "/api/system/activity/stream" in script.text)
    check("Changes are approved from the app", "/api/chat/changes/" in script.text)
    check("Integrations can be signed in from the app", "/auth/start" in script.text)
    check("The window can be dragged in the shell", "pywebview-drag-region" in page.text)
    markdown = (ROOT / "backend" / "app" / "web" / "static" / "ui" / "markdown.js").read_text(encoding="utf-8")
    render = markdown[markdown.index("export function renderMarkdown"):]
    check("Replies are escaped before any markup is applied",
          "const lines = escapeHTML(source)" in render.split("\n", 2)[1])


def test_rightanswers_teach():
    """A portal laid out differently still works, and Teach learns its layout."""
    print("\nRightAnswers — unfamiliar portal and Teach")
    import importlib
    import json as _json
    import sys as _sys
    import time as _time
    import zipfile
    from unittest import mock

    from app.core.config import settings
    from app.services import browser
    from app.services.browser.connector import normalise_address
    from app.services.browser.engine import playwright_installed

    check("Addresses typed without https:// work",
          normalise_address("dental.crm.dynamics.com") == "https://dental.crm.dynamics.com")
    check("Pasted page links are reduced to the site",
          normalise_address("https://dental.crm.dynamics.com/main.aspx?appid=1&pagetype=entityrecord")
          == "https://dental.crm.dynamics.com")
    check("The company addresses are pre-filled",
          settings.DYNAMICS_URL == "https://dental.crm.dynamics.com"
          and settings.RIGHTANSWERS_URL == "https://dexis.rightanswers.com")

    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    from app.core import settings_store

    with mock.patch.object(settings_store, "update",
                           return_value={"ok": True, "applied": []}) as update, \
            TestClient(fastapi_app) as client:
        response = client.post("/api/integrations/rightanswers/enable")
    check("Connect switches on the hidden browser and the system in one step",
          response.status_code == 200 and update.call_args[0][0] ==
          {"BROWSER_AUTOMATION_ENABLED": True, "RIGHTANSWERS_ENABLED": True})

    if not playwright_installed():
        print("  - skipped browser part: Playwright is not installed")
        return

    servers, state = _fake_sites()
    base = f"http://127.0.0.1:{servers['rightanswers'].server_port}"
    connectors_dir = Path(_TEMP_DIR) / "connectors"
    engine_module = importlib.import_module("app.services.browser.engine")
    overrides = {
        "BROWSER_AUTOMATION_ENABLED": True, "RIGHTANSWERS_ENABLED": True,
        "RIGHTANSWERS_URL": f"{base}/portal/", "BROWSER_MODE": "headless",
        "BROWSER_CHANNEL": "msedge" if _sys.platform == "win32" else "chromium",
        "BROWSER_TIMEOUT_SECONDS": 15,
    }
    patches = [mock.patch.object(settings, key, value) for key, value in overrides.items()]
    patches += [
        mock.patch.object(engine_module, "BROWSER_PROFILE_DIR", Path(_TEMP_DIR) / "teach_profile"),
        mock.patch("app.services.browser.connector.CONNECTORS_DIR", connectors_dir),
        mock.patch("app.services.browser.teach.CONNECTORS_DIR", connectors_dir),
        mock.patch("app.services.browser.teach.EDIT_STEP_SECONDS", 20),
    ]
    for patch in patches:
        patch.start()
    rightanswers = browser.get("rightanswers")
    engine = browser.engine()
    try:
        try:
            engine.submit(lambda eng: eng.page("signin").goto(f"{base}/login?done=1"))
        except browser.BrowserUnavailable as exc:
            print(f"  - skipped browser part: {exc}")
            return

        # Before teaching: only the search address is known; the result
        # markup matches none of the default selectors.
        connectors_dir.mkdir(parents=True, exist_ok=True)
        (connectors_dir / "rightanswers.json").write_text(
            _json.dumps({"search_url": "{base}/kb/search?q={query}"}), encoding="utf-8")
        results = rightanswers.search("password")
        check("Search falls back to article-looking links on an unfamiliar portal",
              results and results[0]["title"] == "Reset a forgotten password", results)
        (connectors_dir / "rightanswers.json").unlink()

        teacher = rightanswers.teacher
        started = teacher.start(mode="headless")
        check("Teaching starts and tells the user what to search for",
              started["ok"] and "password" in started["detail"])

        def user(action):
            engine.submit(lambda eng: action(eng._pages["rightanswers"]))
            _time.sleep(3.5)

        user(lambda page: page.goto(f"{base}/kb/search?q=password"))
        user(lambda page: page.click("text=Reset a forgotten password"))
        user(lambda page: page.click("text=Edit article"))
        deadline = _time.time() + 40
        while teacher.state()["status"] not in ("learned", "failed") and _time.time() < deadline:
            _time.sleep(1)
        learned = teacher.state().get("learned") or {}
        check("Teaching finishes", teacher.state()["status"] == "learned", teacher.state())
        check("It learns the search address",
              learned.get("search_url") == "{base}/kb/search?q={query}", learned)
        check("It learns the article address",
              learned.get("article_url") == "{base}/kb/view?docid={id}", learned)
        check("It learns which elements are results", learned.get("result_item") == "div.hit", learned)
        check("It learns where an article's text is",
              learned.get("article_body") in ("#kbArticle", "div.kb-text"), learned)
        check("It learns the editor",
              learned.get("editor_body") == "#kbBody" and learned.get("edit_url")
              == "{base}/kb/edit?docid={id}", learned)
        check("It pins the Save button by its label",
              "Save article" in str(learned.get("save_button")), learned)
        saved = _json.loads((connectors_dir / "rightanswers.json").read_text(encoding="utf-8"))
        check("What it learned is saved for the connector", saved.get("result_item") == "div.hit")

        with zipfile.ZipFile(connectors_dir / "rightanswers-capture.zip") as bundle:
            names = set(bundle.namelist())
            editor_html = bundle.read("editor.html").decode("utf-8")
        check("A diagnostic bundle is saved", {"urls.json", "search.html", "article.html"} <= names)
        check("Hidden form values are removed from the bundle", "SECRET-TOKEN" not in editor_html)

        results = rightanswers.search("password")
        check("Searches use the learned layout",
              results and results[0]["id"] == "KB300" and "forgotten" in results[0]["title"])
        article = rightanswers.get_article("KB300")
        check("Articles are read with the learned layout",
              "fourteen characters" in article["body"] and "Home" not in article["body"], article)
        rightanswers.update_article("KB300", body="Use the new self-service reset page.")
        check("Articles are updated through the learned editor",
              state["articles"]["KB300"]["body"] == "Use the new self-service reset page.")
    finally:
        engine.shutdown()
        for patch in reversed(patches):
            patch.stop()
        for server in servers.values():
            server.shutdown()


def _fake_sharepoint():
    """A local stand-in for SharePoint's REST API, signed in by cookie."""
    import http.server
    import io
    import json as _json
    import re as _re
    import threading
    from urllib.parse import parse_qs, unquote, urlparse

    import docx

    guid = "6f1c2d3e-4a5b-4c6d-8e9f-0a1b2c3d4e5f"
    document = docx.Document()
    document.add_paragraph("Escalation contacts: call the Tier 2 desk on extension 4410.")
    document.add_paragraph("Review this list every quarter.")
    buffer = io.BytesIO()
    document.save(buffer)
    state = {
        "files": {"/sites/Support/Shared Documents/Escalation Plan.docx": {
            "bytes": buffer.getvalue(), "etag": '"{A1},1"'}},
        "pages": {"/sites/Support/SitePages/Onboarding.aspx": {
            "id": 7, "title": "Onboarding",
            "canvas": ('<div data-sp-canvascontrol="" data-sp-controldata="{&quot;x&quot;:1}">'
                       '<div data-sp-rte=""><p>New starters shadow two calls a day.</p>'
                       '<p>Ask the Tier 2 desk for VPN access.</p></div></div>')}},
        "calls": [],
    }
    by_guid = {guid: "/sites/Support/Shared Documents/Escalation Plan.docx"}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body=b"", kind="application/json", headers=None):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def _json(self, payload, code=200):
            self._send(code, _json.dumps(payload))

        def _signed_in(self):
            return "session=ok" in (self.headers.get("Cookie") or "")

        def _target(self, path):
            found = _re.search(r"decodedurl='(.*?)'\)", path)
            if found:
                return found.group(1).replace("''", "'")
            found = _re.search(r"GetFileById\('([^']+)'\)", path)
            return by_guid.get(found.group(1)) if found else None

        def _meta(self, target):
            if target in state["files"]:
                item = state["files"][target]
                return {"Name": target.rsplit("/", 1)[-1], "ServerRelativeUrl": target,
                        "Length": len(item["bytes"]), "TimeLastModified": "2026-10-01T09:00:00Z",
                        "ETag": item["etag"]}
            if target in state["pages"]:
                return {"Name": target.rsplit("/", 1)[-1], "ServerRelativeUrl": target,
                        "Length": 100, "ETag": '"{P7},3"'}
            return None

        def do_GET(self):
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path == "/login":
                if parse_qs(parsed.query).get("done"):
                    return self._send(302, "", "text/html", {"Set-Cookie": "session=ok; Path=/",
                                                              "Location": "/"})
                return self._send(200, "<a href='/login?done=1'>Sign in</a>", "text/html")
            if not self._signed_in():
                return self._send(302, "", "text/html", {"Location": "/login"})
            if path.startswith("/:w:/s/Support/"):
                return self._send(302, "", "text/html", {"Location": (
                    f"/sites/Support/_layouts/15/Doc.aspx?sourcedoc=%7B{guid}%7D"
                    "&file=Escalation%20Plan.docx&action=default")})
            if path.endswith("/_layouts/15/Doc.aspx"):
                return self._send(200, "<html>Word Online</html>", "text/html")
            if path in ("/", "/_api/web") or path.endswith("/_api/web"):
                return self._json({"Title": "Support"})
            if path.endswith("/_api/web/currentuser"):
                return self._json({"Title": "Sam Agent", "Email": "sam@envista.example"})
            if path.endswith("/_api/search/query"):
                return self._json({"PrimaryQueryResult": {"RelevantResults": {"Table": {"Rows": [
                    {"Cells": [{"Key": "Title", "Value": "Escalation Plan"},
                               {"Key": "Path", "Value": "http://x/sites/Support/Shared Documents/Escalation Plan.docx"},
                               {"Key": "HitHighlightedSummary", "Value": "<c0>Tier 2</c0> desk"},
                               {"Key": "FileType", "Value": "docx"}]}]}}}})
            target = self._target(path)
            if target and path.endswith("/$value"):
                return self._send(200, state["files"][target]["bytes"],
                                  "application/octet-stream")
            if target and path.endswith("/ListItemAllFields"):
                page = state["pages"][target]
                return self._json({"Id": page["id"], "Title": page["title"],
                                   "CanvasContent1": page["canvas"]})
            if target:
                meta = self._meta(target)
                return self._json(meta) if meta else self._json(
                    {"error": {"message": {"value": "File Not Found."}}}, 404)
            return self._json({"error": {"message": {"value": "Not found"}}}, 404)

        def do_POST(self):
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if not self._signed_in():
                return self._json({"error": {"message": {"value": "Access denied"}}}, 401)
            if path.endswith("/_api/contextinfo"):
                return self._json({"FormDigestValue": "digest-1"})
            if self.headers.get("X-RequestDigest") != "digest-1":
                return self._json({"error": {"message": {"value": "The security validation "
                                                                  "for this page is invalid."}}}, 403)
            state["calls"].append(path.rsplit("/", 1)[-1])
            target = self._target(path)
            if target and path.endswith("/$value") and self.headers.get("X-HTTP-Method") == "PUT":
                item = state["files"][target]
                item["bytes"] = body
                item["etag"] = item["etag"].replace(",1", ",2").replace(",2\"", ",3\"")
                return self._send(204)
            found = _re.search(r"/sitepages/pages\((\d+)\)/(\w+)", path)
            if found:
                page = next(p for p in state["pages"].values() if p["id"] == int(found.group(1)))
                if found.group(2) == "savepage":
                    page["canvas"] = _json.loads(body)["CanvasContent1"]
                return self._send(204)
            return self._json({"error": {"message": {"value": "Not found"}}}, 404)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, state


def test_sharepoint_links():
    """Pasted SharePoint links are opened, read and (after approval) updated."""
    print("\nSharePoint — links through the hidden browser")
    import importlib
    import io
    import sys as _sys
    from unittest import mock

    from app.core.config import settings
    from app.services import browser
    from app.services.agent import actions
    from app.services.agent.loop import _sharepoint_links
    from app.services.agent.registry import REGISTRY, ToolContext
    from app.services.browser.engine import playwright_installed
    from app.services.browser.sharepoint import SharePointError, find_links

    check("The company SharePoint is pre-filled",
          settings.SHAREPOINT_SITE_URL == "https://envistaconnect.sharepoint.com")
    message = ("Can you update https://envistaconnect.sharepoint.com/:w:/s/Support/EabcXYZ?e=k2 "
               "and check https://envistaconnect.sharepoint.com/sites/Support/SitePages/Home.aspx.")
    check("SharePoint links are found in a message", len(find_links(message)) == 2)
    sharepoint = browser.get("sharepoint")
    try:
        sharepoint.check_link("https://contoso.sharepoint.com/sites/x/a.docx")
        refused = False
    except SharePointError:
        refused = True
    check("Links on another company's SharePoint are refused", refused)
    check("OneDrive links of the same company are accepted",
          "envistaconnect-my.sharepoint.com" in sharepoint.tenant_hosts)

    try:
        import docx  # noqa: F401
    except ImportError:
        print("  - skipped browser part: python-docx is not installed")
        return
    if not playwright_installed():
        print("  - skipped browser part: Playwright is not installed")
        return

    server, state = _fake_sharepoint()
    base = f"http://127.0.0.1:{server.server_port}"
    engine_module = importlib.import_module("app.services.browser.engine")
    sharepoint_module = importlib.import_module("app.services.browser.sharepoint")
    overrides = {"BROWSER_AUTOMATION_ENABLED": True, "SHAREPOINT_BROWSER_ENABLED": True,
                 "SHAREPOINT_SITE_URL": base, "BROWSER_MODE": "headless",
                 "BROWSER_CHANNEL": "msedge" if _sys.platform == "win32" else "chromium",
                 "BROWSER_TIMEOUT_SECONDS": 15}
    patches = [mock.patch.object(settings, key, value) for key, value in overrides.items()]
    patches += [mock.patch.object(engine_module, "BROWSER_PROFILE_DIR", Path(_TEMP_DIR) / "sp_profile"),
                mock.patch.object(sharepoint_module, "CACHE_DIR", Path(_TEMP_DIR) / "sp_cache")]
    for patch in patches:
        patch.start()
    engine = browser.engine()
    db = session()
    try:
        try:
            engine.submit(lambda eng: eng.page("signin").goto(f"{base}/login?done=1"))
        except browser.BrowserUnavailable as exc:
            print(f"  - skipped browser part: {exc}")
            return
        check("The check says who is signed in", sharepoint.check().get("account") == "Sam Agent")

        direct = f"{base}/sites/Support/Shared%20Documents/Escalation%20Plan.docx"
        sharing = f"{base}/:w:/s/Support/EabcXYZ?e=k2"
        office = (f"{base}/sites/Support/_layouts/15/Doc.aspx?sourcedoc=%7B6f1c2d3e-4a5b-4c6d-"
                  "8e9f-0a1b2c3d4e5f%7D&file=Escalation%20Plan.docx&action=default")
        for label, link in (("a direct link", direct), ("a sharing link", sharing),
                            ("an Office Online link", office)):
            item = sharepoint.read(link)
            check(f"A document opens from {label}", "extension 4410" in item["text"]
                  and item["name"] == "Escalation Plan.docx", item.get("text"))
        page = sharepoint.read(f"{base}/sites/Support/SitePages/Onboarding.aspx")
        check("A site page is read from its content", page["kind"] == "page"
              and "shadow two calls" in page["text"] and "data-sp" not in page["text"])
        check("SharePoint search returns results",
              sharepoint.search("tier 2")[0]["title"] == "Escalation Plan")

        ctx = ToolContext(db=db, context={})
        result = REGISTRY["sharepoint_read"].handler(ctx, link=sharing)
        check("Ask's read tool cites the document", "[SP1]" in result["content"])
        check("The model is pointed at pasted links", _sharepoint_links(message) == find_links(message))

        REGISTRY["sharepoint_update_document"].handler(
            ctx, link=direct, operations=[{"op": "replace_text", "find": "extension 4410",
                                           "replace": "extension 5520"}])
        REGISTRY["sharepoint_update_page"].handler(
            ctx, link=f"{base}/sites/Support/SitePages/Onboarding.aspx",
            changes=[{"find": "two calls", "replace": "three calls"}])
        cards = [card for card in ctx.drafts if card["type"] == "approval"]
        check("Proposing SharePoint changes writes nothing", not state["calls"] and len(cards) == 2)
        doc_card = next(c for c in cards if c["tool"] == "sharepoint_update_document")
        field = doc_card["preview"]["fields"][0]
        check("The document card shows the text before and after",
              "4410" in field["before"] and "5520" in field["after"])

        for card in cards:
            outcome = actions.approve(db, card["action_id"])
            check(f"Approved {card['tool']} runs", outcome.get("notify") is True, outcome.get("reply"))
        import docx
        saved = docx.Document(io.BytesIO(
            state["files"]["/sites/Support/Shared Documents/Escalation Plan.docx"]["bytes"]))
        check("The document in SharePoint now has the change",
              "extension 5520" in "\n".join(p.text for p in saved.paragraphs))
        check("The page was checked out, saved and published",
              state["calls"][-3:] == ["checkoutpage", "savepage", "publish"])
        canvas = state["pages"]["/sites/Support/SitePages/Onboarding.aspx"]["canvas"]
        check("Only the page text changed, not its markup",
              "three calls" in canvas and 'data-sp-controldata="{&quot;x&quot;:1}"' in canvas)

        # Someone edits the file between the preview and the approval.
        ctx = ToolContext(db=db, context={})
        REGISTRY["sharepoint_update_document"].handler(
            ctx, link=direct, operations=[{"op": "append_paragraph", "text": "Owner: Sam"}])
        state["files"]["/sites/Support/Shared Documents/Escalation Plan.docx"]["etag"] = '"{A1},9"'
        outcome = actions.approve(db, ctx.drafts[0]["action_id"])
        check("A change is refused if the file changed after the preview",
              "changed in SharePoint" in outcome["reply"])
    finally:
        engine.shutdown()
        for patch in reversed(patches):
            patch.stop()
        server.shutdown()
        db.close()


def test_bedrock_tool_fallbacks():
    """Bedrock models without tool use or system prompts still work in Ask."""
    print("\nBedrock — Nova, Llama and Mistral behaviour")
    from unittest import mock

    from app.core.config import settings
    from app.services import llm_service

    class ValidationException(Exception):
        pass

    class FakeClient:
        def __init__(self, behaviour):
            self.behaviour, self.requests = behaviour, []

        def converse(self, **kwargs):
            self.requests.append(kwargs)
            if "toolConfig" in kwargs and "no_tools" in self.behaviour:
                raise ValidationException("An error occurred (ValidationException) when calling "
                                          "the Converse operation: This model doesn't support tool use.")
            if "system" in kwargs and "no_system" in self.behaviour:
                raise ValidationException("An error occurred (ValidationException) when calling the "
                                          "Converse operation: This model doesn't support system messages.")
            blocks = [{"reasoningContent": {"reasoningText": {"text": "secret thoughts"}}},
                      {"text": '{"tool": "search_knowledge", "arguments": {"query": "vpn"}}'
                       if "toolConfig" not in kwargs else "Native answer"}]
            return {"output": {"message": {"content": blocks}}}

    tools = [{"name": "search_knowledge", "description": "Search",
              "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}]

    def run(model, behaviour):
        client = FakeClient(behaviour)
        session = mock.MagicMock()
        session.client.return_value = client
        llm_service._NO_NATIVE_TOOLS.clear()
        llm_service._NO_SYSTEM_PROMPT.clear()
        with mock.patch.object(settings, "LLM_PROVIDER", "bedrock"), \
                mock.patch.object(settings, "BEDROCK_REGION", "us-east-1"), \
                mock.patch.object(settings, "BEDROCK_MODEL_ID", model), \
                mock.patch.object(settings, "LLM_TOOL_MODE", "auto"), \
                mock.patch.object(type(settings), "llm_configured", new=property(lambda self: True)), \
                mock.patch.object(llm_service, "bedrock_session", return_value=session):
            result = LLMService().chat([{"role": "user", "content": "vpn?"}], tools=tools,
                                       system="You are Cerebro.")
        return result, client

    result, client = run("us.amazon.nova-pro-v1:0", set())
    check("Nova uses native tool calling", result["mode"] == "native"
          and "toolConfig" in client.requests[0])
    check("Nova chooses tools with temperature 0",
          client.requests[0]["inferenceConfig"]["temperature"] == 0.0)
    check("Reasoning blocks are not shown as the answer", result["content"] == "Native answer")

    result, client = run("meta.llama3-70b-instruct-v1:0", {"no_tools"})
    check("A model without tool use falls back instead of failing",
          result["mode"] == "json" and result["tool_calls"]
          and result["tool_calls"][0]["name"] == "search_knowledge", result)

    result, client = run("mistral.mistral-7b-instruct-v0:2", {"no_tools", "no_system"})
    last = client.requests[-1]
    check("A model without system prompts gets them folded into the chat",
          "system" not in last and "You are Cerebro." in last["messages"][0]["content"][0]["text"])
    check("…and still reaches a tool call", result["tool_calls"]
          and result["tool_calls"][0]["name"] == "search_knowledge", result)

    error = llm_service._bedrock_error(ValidationException(
        "ValidationException: The provided model identifier is invalid."))
    check("A genuinely wrong model ID is still explained",
          isinstance(error, llm_service.LLMNotConfigured) and "model ID" in str(error))
    llm_service._NO_NATIVE_TOOLS.clear()
    llm_service._NO_SYSTEM_PROMPT.clear()


# -------------------------------------------------------------------- main
def main() -> int:
    print("Running Cerebro tests…")
    init_db()

    for suite in (test_version_source, test_event_detector, test_context_engine, test_event_flow,
                  test_embeddings, test_knowledge_search, test_chunked_citations_and_sources,
                  test_llm_disabled,
                  test_bedrock_provider, test_model_catalog_and_discovery,
                  test_setup_checks_and_repair, test_setup_wizard,
                  test_database_resilience, test_database_round_trip,
                  test_file_reads_declare_encoding,
                  test_everything_is_explained, test_installer_integrity,
                  test_branding_assets, test_motion_and_icons,
                  test_branding_module,
                  test_copilot_instructions,
                  test_bedrock_credential_modes, test_bundled_dependencies,
                  test_enterprise_normalisation, test_enterprise_ingest_and_reply,
                  test_document_reading, test_document_editing,
                  test_sharepoint_resolution, test_document_tracking,
                  test_embedding_signature_matches_vector, test_schema_upgrade,
                  test_customer_from_title, test_database_password_masking,
                  test_env_round_trip, test_urgency_word_boundaries,
                  test_empty_batch_file, test_office_lock_detection,
                  test_spreadsheet_value_coercion, test_failed_edit_leaves_no_litter,
                  test_redaction, test_activity_capture, test_memory,
                  test_style_and_persona, test_task_parsing_and_scheduling,
                  test_document_update_task, test_nudges,
                  test_copilot_bridge, test_copilot_guide,
                  test_copilot_approval_flow, test_copilot_memory_redaction,
                  test_settings_store, test_setup_and_package_contract,
                  test_power_automate_package,
                  test_screenpipe_current_api, test_chat_service,
                  test_chat_reference_images, test_ask_tools_and_action_cards, test_activity_state, test_llm_chat_protocol, test_ask_relevance, test_ask_agent_loop, test_chat_stream, test_browser_disabled_by_default, test_browser_integrations, test_dynamics_case_prefetch, test_tray_brain, test_desktop_shell, test_app_page, test_rightanswers_teach, test_sharepoint_links, test_bedrock_tool_fallbacks):
        try:
            suite()
        except Exception as exc:  # a crashing suite is a failure, not a stack trace
            FAILED.append(f"{suite.__name__} raised {type(exc).__name__}: {exc}")
            print(f"  ✗ {suite.__name__} raised {type(exc).__name__}: {exc}")

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("\nFailures:")
        for failure in FAILED:
            print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TEMP_DIR, ignore_errors=True)
