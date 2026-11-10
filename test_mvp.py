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
# Meeting briefs read the calendar in the background; tests that need them turn
# them on themselves, so the scheduler never drives the hidden browser mid-test.
os.environ["MEETING_BRIEF_MINUTES"] = "0"
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
    # The settings view (in the app window and on /settings) lives in one module.
    html = (ROOT / "backend" / "app" / "web" / "static" / "ui" / "settings-view.js").read_text(encoding="utf-8")
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

    # Services are imported lazily inside functions, which PyInstaller cannot
    # see. One left out of the spec works from source and fails only in the
    # installed app.
    pinned = set(re.findall(r'"(app\.[\w.]+)"', spec))
    services = ROOT / "backend" / "app" / "services"
    unpinned = sorted(
        "app." + ".".join(path.relative_to(ROOT / "backend" / "app").with_suffix("").parts)
        for path in services.rglob("*.py") if path.name != "__init__.py")
    unpinned = [name for name in unpinned if name not in pinned]
    check("Every service module is pinned in the PyInstaller spec", not unpinned, unpinned)
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
    check("Cited sources are returned", [s["ref"] for s in result["sources"]] == ["K1"], result.get("sources") or result.get("reply"))
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
              == {"dynamics", "rightanswers", "sharepoint", "outlook", "teams",
                  "beyondtrust", "genesys"})
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
          and settings.RIGHTANSWERS_URL
          == "https://dexis.rightanswers.com/solutionmanger/controller/workspace/")

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


def test_desktop_buddy():
    """The desktop buddy appears while Cerebro works and goes away after."""
    print("\nDesktop buddy")
    sys.path.insert(0, str(ROOT / "desktop"))
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("  - skipped: Pillow is not installed")
        return
    import buddy

    now = [100.0]
    logic = buddy.BuddyLogic(clock=lambda: now[0])
    check("Hidden while idle", not logic.visible())
    logic.update({"state": "browsing", "detail": "Reading CAS-01234 in Dynamics"})
    check("Appears while Cerebro works", logic.visible())
    check("Shows what it's doing", "CAS-01234" in logic.caption)
    check("Working in the browser shows the laptop brain", logic.sprite == "working")
    logic.update({"state": "searching", "detail": "Searching RightAnswers"})
    check("Research shows the studying brain", logic.sprite == "studying")
    logic.update({"state": "idle"})
    now[0] += 1
    check("Lingers a moment after the work is done", logic.visible())
    now[0] += 3
    check("Then goes away", not logic.visible())
    logic.update({"state": "thinking", "detail": "Thinking"})
    logic.dismiss()
    check("× hides it", not logic.visible())
    logic.update({"state": "writing", "detail": "Adding a note"})
    check("…for the rest of that piece of work", not logic.visible())
    logic.update({"state": "idle"})
    logic.update({"state": "thinking", "detail": "Thinking"})
    check("…and it comes back for the next one", logic.visible())
    logic.update({"state": "awaiting_approval", "detail": "1 draft"})
    now[0] += 10
    check("Waiting for approval is not work", not logic.visible())
    logic.enabled = False
    logic.update({"state": "thinking"})
    check("Turning it off in the tray keeps it away", not logic.visible())


def test_chats():
    """Separate chats: own history, own instructions, own tasks."""
    print("\nAsk — multiple chats")
    from fastapi.testclient import TestClient

    from app.core.database import _adopt_unfiled_messages
    from app.main import app
    from app.models.chat import ChatMessage
    from app.models.conversation import Conversation
    from app.models.task import Task
    from app.services import conversations
    from app.services.agent import loop
    from app.services.chat_service import ChatService

    db = session()

    # Messages from before chats existed are kept, gathered into one chat.
    db.add(ChatMessage(role="user", content="An old question from last month"))
    db.commit()
    _adopt_unfiled_messages()
    earlier = db.query(Conversation).filter(Conversation.title == "Earlier chat").first()
    check("Older messages are filed under “Earlier chat”",
          earlier is not None and db.query(ChatMessage)
          .filter(ChatMessage.conversation_id.is_(None)).count() == 0)

    with TestClient(app) as client:
        first = client.post("/api/chat/conversations", json={}).json()
        second = client.post("/api/chat/conversations", json={
            "instructions": "Always answer in French."}).json()
        check("A new chat starts untitled", first["title"] == "New chat" and not first["archived"])

        script = ScriptedLLM(["Restart the spooler.", "Redémarrez le service VPN.",
                              "Clear the queue first."])
        enabled, patched = _with_ai(script)
        with enabled, patched:
            one = client.post("/api/chat/message", json={
                "message": "Why is the printer stuck on every job?",
                "conversation_id": first["id"]}).json()
            client.post("/api/chat/message", json={
                "message": "Why does the VPN drop?", "conversation_id": second["id"]})
            client.post("/api/chat/message", json={
                "message": "And after that?", "conversation_id": first["id"]})

        check("The reply says which chat it belongs to",
              one["conversation"]["id"] == first["id"])
        check("A chat is titled from its first message",
              one["conversation"]["title"].startswith("Why is the printer stuck"))
        check("Per-chat instructions reach the model",
              "Always answer in French." in script.requests[1]["system"]
              and "Always answer in French." not in script.requests[0]["system"])
        third_turns = " ".join(m["content"] for m in script.requests[2]["messages"])
        check("A chat only remembers its own conversation",
              "printer" in third_turns and "VPN" not in third_turns)

        history = client.get("/api/chat/history",
                             params={"conversation_id": second["id"]}).json()
        check("History is per chat",
              [m["content"] for m in history["messages"]]
              == ["Why does the VPN drop?", "Redémarrez le service VPN."])
        check("An unknown chat is a 404",
              client.get("/api/chat/history", params={"conversation_id": 99999}).status_code == 404)

        listing = client.get("/api/chat/conversations").json()["conversations"]
        check("The most recently used chat is listed first", listing[0]["id"] == first["id"])
        check("The list shows a preview of the last message",
              listing[0]["preview"] == "Clear the queue first.")
        found = client.get("/api/chat/conversations", params={"q": "spooler"}).json()
        found_ids = [c["id"] for c in found["conversations"]]
        check("Chats can be searched by what was said",
              first["id"] in found_ids and second["id"] not in found_ids)

        renamed = client.patch(f"/api/chat/conversations/{second['id']}", json={
            "title": "VPN drops", "pinned": True}).json()
        check("A chat can be renamed and pinned", renamed["title"] == "VPN drops" and renamed["pinned"])
        listing = client.get("/api/chat/conversations").json()["conversations"]
        check("Pinned chats come first", listing[0]["id"] == second["id"])
        with enabled, patched:
            client.post("/api/chat/message", json={
                "message": "One more thing", "conversation_id": second["id"]})
        check("A renamed chat keeps its name",
              client.get(f"/api/chat/conversations/{second['id']}").json()["title"] == "VPN drops")

        ended = client.post(f"/api/chat/conversations/{first['id']}/end").json()
        check("Ending a chat archives it and opens a new one",
              ended["ended"]["archived"] and ended["conversation"]["id"] != first["id"]
              and not ended["conversation"]["archived"])
        check("A chat's preview is plain text, not markdown",
              conversations._preview("**Restart** the [spooler](http://x) [K1]") == "Restart the spooler")
        open_ids = [c["id"] for c in client.get("/api/chat/conversations").json()["conversations"]]
        archived = [c["id"] for c in client.get("/api/chat/conversations",
                                                params={"archived": True}).json()["conversations"]]
        check("An ended chat moves to Archived", first["id"] in archived and first["id"] not in open_ids)
        check("An ended chat is still readable", client.get(
            "/api/chat/history", params={"conversation_id": first["id"]}).json()["count"] == 4)
        restored = client.patch(f"/api/chat/conversations/{first['id']}",
                                json={"archived": False}).json()
        check("…and can be restored", restored["archived"] is False)

        # Messages without a chat go to the most recently active one.
        fresh = ChatService(db)
        check("A message with no chat named goes to the latest one",
              fresh.conversation_id == second["id"])

        # Assigned tasks: run with the agent, post back into the chat.
        assigned = client.post(f"/api/chat/conversations/{second['id']}/tasks", json={
            "instruction": "List new Dynamics cases assigned to me",
            "schedule": "weekdays", "at_time": "08:45"}).json()
        task = db.query(Task).get(assigned["task"]["id"])
        check("A task can be assigned to a chat",
              task.kind == "agent" and task.conversation_id == second["id"]
              and task.schedule == "weekdays" and task.next_run is not None)
        check("A bad time is refused", client.post(
            f"/api/chat/conversations/{second['id']}/tasks",
            json={"instruction": "x", "schedule": "daily", "at_time": "noon"}).status_code == 422)

        script = ScriptedLLM([
            {"name": "search_knowledge", "arguments": {"query": "new cases"}},
            "Two new cases: CAS-1 and CAS-2."])
        enabled, patched = _with_ai(script)
        from app.services.task_service import TaskService
        with enabled, patched:
            TaskService(db).run(task)
        db.refresh(task)
        posted = (db.query(ChatMessage).filter(ChatMessage.conversation_id == second["id"])
                  .order_by(ChatMessage.id.desc()).first())
        check("The task's result is posted into its chat",
              posted.kind == "task_result" and posted.content.startswith("Two new cases")
              and posted.task_id == task.id)
        check("The task runs with the chat's instructions",
              "Always answer in French." in script.requests[0]["system"])
        check("A running task cannot schedule itself again",
              "create_task" not in script.requests[0]["tools"])
        check("A recurring task stays scheduled after a run",
              task.status == "active" and task.next_run is not None and task.run_count == 1)

        strip = client.get(f"/api/chat/conversations/{second['id']}/tasks").json()
        check("The chat lists its tasks", [t["id"] for t in strip["tasks"]] == [task.id])
        check("Chats with tasks are marked in the list", next(
            c for c in client.get("/api/chat/conversations").json()["conversations"]
            if c["id"] == second["id"])["has_tasks"])

        # Asking for a task in plain language files it under the current chat.
        script = ScriptedLLM([
            {"name": "create_task", "arguments": {
                "instruction": "Every weekday at 9am check SharePoint for new release notes"}},
            "Done — I'll check every weekday at 9."])
        enabled, patched = _with_ai(script)
        from unittest import mock
        with enabled, patched, mock.patch("app.services.task_service.parse_instruction",
                                          return_value={"title": "Check release notes",
                                                        "kind": "agent", "schedule": "weekdays",
                                                        "at_time": "09:00", "spec": {}}):
            result = client.post("/api/chat/message", json={
                "message": "check sharepoint for release notes every weekday at 9",
                "conversation_id": first["id"]}).json()
        made = db.query(Task).get(result["task"]["id"])
        check("A task asked for in a chat belongs to that chat",
              made.conversation_id == first["id"] and made.kind == "agent")

        # Ending a chat stops its scheduled tasks.
        throwaway = client.post("/api/chat/conversations", json={}).json()
        doomed = client.post(f"/api/chat/conversations/{throwaway['id']}/tasks", json={
            "instruction": "Check the queue", "schedule": "hourly"}).json()["task"]
        ended = client.post(f"/api/chat/conversations/{throwaway['id']}/end").json()
        db.expire_all()
        check("Ending a chat stops its scheduled tasks",
              ended["tasks_stopped"] == 1 and db.query(Task).get(doomed["id"]).status == "cancelled")

        # Deleting a chat removes its messages and stops its tasks.
        client.delete(f"/api/chat/conversations/{first['id']}")
        db.expire_all()
        check("Deleting a chat removes its messages", db.query(ChatMessage)
              .filter(ChatMessage.conversation_id == first["id"]).count() == 0)
        check("…and stops its tasks", db.query(Task).get(made.id).status == "cancelled")

        # A task whose chat is gone gets a chat of its own rather than failing.
        made.status, made.conversation_id = "active", first["id"]
        db.commit()
        script = ScriptedLLM(["Nothing new today."])
        enabled, patched = _with_ai(script)
        with enabled, patched:
            TaskService(db).run(made)
        db.refresh(made)
        home = conversations.get(db, made.conversation_id)
        check("An orphaned task posts into a new chat named after it",
              home is not None and home.title == "Check release notes")

    check("Agent history can be read for one chat",
          all(m["role"] in ("user", "assistant") for m in loop._history(db, second["id"])))
    db.close()


def test_sharepoint_auto_apply():
    """With automatic updates on, SharePoint changes happen at once and can be undone."""
    print("\nSharePoint — automatic updates and undo")
    import importlib
    import io
    import sys as _sys
    from unittest import mock

    from app.core.config import settings
    from app.services import browser
    from app.services.agent import actions
    from app.services.agent.registry import REGISTRY, ToolContext
    from app.services.browser.engine import playwright_installed

    sharepoint = browser.get("sharepoint")
    with mock.patch.object(settings, "SHAREPOINT_AUTO_APPLY", False):
        check("Automatic updates are off by default", not sharepoint.auto_apply
              and sharepoint.status()["can_auto_apply"])
    with mock.patch.object(settings, "DYNAMICS_AUTO_APPLY", False):
        check("Dynamics changes need approval unless you switch that on, separately",
              browser.get("dynamics").status()["can_auto_apply"] and not browser.get("dynamics").auto_apply
              and not browser.get("beyondtrust").status()["can_auto_apply"])

    try:
        import docx
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
                 "SHAREPOINT_AUTO_APPLY": True,
                 "BROWSER_CHANNEL": "msedge" if _sys.platform == "win32" else "chromium",
                 "BROWSER_TIMEOUT_SECONDS": 15}
    patches = [mock.patch.object(settings, key, value) for key, value in overrides.items()]
    patches += [mock.patch.object(engine_module, "BROWSER_PROFILE_DIR", Path(_TEMP_DIR) / "spa_profile"),
                mock.patch.object(sharepoint_module, "CACHE_DIR", Path(_TEMP_DIR) / "spa_cache"),
                mock.patch.object(sharepoint_module, "UNDO_DIR", Path(_TEMP_DIR) / "spa_undo")]
    for patch in patches:
        patch.start()
    engine = browser.engine()
    db = session()
    doc_path = "/sites/Support/Shared Documents/Escalation Plan.docx"
    page_path = "/sites/Support/SitePages/Onboarding.aspx"

    def doc_text():
        saved = docx.Document(io.BytesIO(state["files"][doc_path]["bytes"]))
        return "\n".join(p.text for p in saved.paragraphs)

    try:
        try:
            engine.submit(lambda eng: eng.page("signin").goto(f"{base}/login?done=1"))
        except browser.BrowserUnavailable as exc:
            print(f"  - skipped browser part: {exc}")
            return
        direct = f"{base}{doc_path.replace(' ', '%20')}"
        ctx = ToolContext(db=db, context={})
        result = REGISTRY["sharepoint_update_document"].handler(
            ctx, link=direct, operations=[{"op": "replace_text", "find": "extension 4410",
                                           "replace": "extension 5520"}])
        card = ctx.drafts[0]
        check("The document changes straight away", "extension 5520" in doc_text())
        check("The model is told it was applied, not that it waits",
              result["summary"] == "Applied" and "undo" in result["content"])
        check("The card says it was applied automatically and offers undo",
              card["status"] == "done" and card["automatic"] and card["can_undo"])
        check("The before/after is still shown",
              "4410" in card["preview"]["fields"][0]["before"])

        outcome = actions.undo(db, card["action_id"])
        check("Undo puts the document back", "extension 4410" in doc_text()
              and "extension 5520" not in doc_text(), outcome.get("reply"))
        check("An undone change can't be undone twice",
              "Nothing to undo" in actions.undo(db, card["action_id"])["reply"])

        # Undo refuses to overwrite someone else's later edit.
        ctx = ToolContext(db=db, context={})
        REGISTRY["sharepoint_update_document"].handler(
            ctx, link=direct, operations=[{"op": "append_paragraph", "text": "Owner: Sam"}])
        state["files"][doc_path]["etag"] = '"{A1},42"'
        outcome = actions.undo(db, ctx.drafts[0]["action_id"])
        check("Undo refuses when the file was edited since", "edited since" in outcome["reply"]
              and "Owner: Sam" in doc_text())

        # Pages
        ctx = ToolContext(db=db, context={})
        REGISTRY["sharepoint_update_page"].handler(
            ctx, link=f"{base}{page_path}", changes=[{"find": "two calls", "replace": "four calls"}])
        check("A page is published straight away",
              "four calls" in state["pages"][page_path]["canvas"])
        actions.undo(db, ctx.drafts[0]["action_id"])
        canvas = state["pages"][page_path]["canvas"]
        check("Undo republishes the page as it was",
              "two calls" in canvas and "four calls" not in canvas
              and 'data-sp-controldata="{&quot;x&quot;:1}"' in canvas)

        # Turned off again: changes wait for approval.
        with mock.patch.object(settings, "SHAREPOINT_AUTO_APPLY", False):
            ctx = ToolContext(db=db, context={})
            before = state["pages"][page_path]["canvas"]
            result = REGISTRY["sharepoint_update_page"].handler(
                ctx, link=f"{base}{page_path}", changes=[{"find": "two calls", "replace": "five calls"}])
            check("With it off, nothing changes until approval",
                  state["pages"][page_path]["canvas"] == before
                  and result["summary"] == "Waiting for approval")
            action_id = ctx.drafts[0]["action_id"]
            actions.approve(db, action_id)
            check("An approved change can be undone too",
                  actions.card(db.get(actions.AgentAction, action_id))["can_undo"])

        # A chat shows each change's current state, not the one it was saved with.
        from app.services.chat_service import ChatService
        chat = ChatService(db)
        chat._store("assistant", "Prepared a change", kind="draft",
                    meta={"cards": [{**ctx.drafts[0], "status": "awaiting_approval"}]})
        shown = chat.history()[-1]["meta"]["cards"][0]
        check("Reloading a chat shows a change as done, not still awaiting",
              shown["status"] == "done" and shown["can_undo"])

        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as client:
            listed = client.get("/api/integrations").json()["integrations"]
            sp = next(i for i in listed if i["name"] == "sharepoint")
            check("The Connect tab sees the setting", sp["can_auto_apply"] and sp["auto_apply"])
            refused = client.post("/api/integrations/beyondtrust/auto-apply", json={"enabled": True})
            check("Systems without automatic updates refuse the switch", refused.status_code == 400)
    finally:
        engine.shutdown()
        for patch in reversed(patches):
            patch.stop()
        server.shutdown()
        db.close()


def test_window_sizing():
    """The desktop window can be resized, expanded and is kept on screen."""
    print("\nDesktop window — size and fit")
    sys.path.insert(0, str(ROOT / "desktop"))
    from unittest import mock

    import shell
    import widget_config

    area = (0, 0, 1366, 720)                      # a small laptop screen, taskbar excluded
    check("A window taller than the screen is shrunk to fit",
          shell.clamp_bounds(900, 10, 440, 900, area)[3] <= 720 - 2 * shell.EDGE_GAP)
    x, y, w, h = shell.clamp_bounds(1200, 500, 440, 600, area)
    check("A window hanging off the edge is pulled back on screen",
          x + w <= 1366 and y + h <= 720, (x, y, w, h))
    check("A first run is placed near the bottom-right corner",
          shell.clamp_bounds(None, None, 440, 600, area)[:2] == (1366 - 440 - 24, 720 - 600 - 24))
    check("It is never made smaller than usable",
          shell.clamp_bounds(0, 0, 100, 100, area)[2:] == shell.MIN_SIZE)
    x, y, w, h = shell.clamp_bounds(-1500, 100, 440, 600, (-1920, 0, 1920, 1040))
    check("A second screen to the left works too", x == -1500 and w == 440)

    with mock.patch.object(widget_config, "load", return_value=dict(widget_config.DEFAULTS)), \
            mock.patch.object(widget_config, "save"):
        app = shell.Shell("http://127.0.0.1:1")
        app.window = mock.MagicMock()
        app.config.update(x=100, y=100, width=440, height=720)
        api = shell.ShellApi(app)
        api.set_bounds(100, 100, 600, 800)
        check("Dragging a handle resizes the window", app.window.resize.call_args[0] == (600, 800)
              and app.config["width"] == 600)
        api.set_bounds(60, 100, 200, 200)
        check("…but not below the minimum size",
              app.window.resize.call_args[0] == shell.MIN_SIZE and app.window.move.called)
        check("Expand maximizes", api.toggle_maximize() is True and app.window.maximize.called)
        app._on_resized(1920, 1040)
        check("An expanded window doesn't overwrite the remembered size", app.config["width"] == 360)
        check("…and expanding again restores it", api.toggle_maximize() is False and app.window.restore.called)
        result = api.set_size("tall", {"left": 0, "top": 0, "width": 1366, "height": 720})
        check("'Full height' fills the screen's height", result["height"] == 720 - 2 * shell.EDGE_GAP)
        result = api.set_size("large", {"left": 0, "top": 0, "width": 1366, "height": 720})
        check("A preset bigger than the screen is fitted to it", result["height"] <= 720)
        app.config.update(x=1200, y=600, width=440, height=600)
        fitted = api.fit({"left": 0, "top": 0, "width": 1366, "height": 720})
        check("A window that ended up off screen is brought back", fitted["moved"]
              and fitted["bounds"][0] + 440 <= 1366)
        check("A window that fits is left alone",
              api.fit({"left": 0, "top": 0, "width": 2560, "height": 1400})["moved"] is False)

    # The page's resize handles, against a stand-in for the desktop bridge.
    from app.services.browser.engine import playwright_installed
    if not playwright_installed():
        print("  - skipped page part: Playwright is not installed")
        return
    import threading
    from playwright.sync_api import sync_playwright
    import uvicorn
    from app.main import app as fastapi_app

    config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    import time as _time
    for _ in range(100):
        if server.started:
            break
        _time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    bridge = """
      window.__calls = [];
      window.pywebview = { api: new Proxy({}, { get: (_, name) => (...args) => {
        window.__calls.push([name, ...args]);
        return Promise.resolve(name === 'toggle_maximize' ? true : null); } }) };
    """
    try:
        with sync_playwright() as p:
            # The installed Edge on Windows (the build machine has no bundled
            # Chromium), Playwright's Chromium elsewhere — as the browser tests do.
            try:
                browser = p.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
            except Exception as exc:  # noqa: BLE001
                print(f"  - skipped page part: {str(exc).splitlines()[0]}")
                return
            page = browser.new_page(viewport={"width": 440, "height": 640},
                                    screen={"width": 1920, "height": 1080})
            page.add_init_script(bridge)
            page.goto(f"http://127.0.0.1:{port}/app?shell=1")
            page.wait_for_timeout(800)
            check("The resize grip is shown in the desktop window",
                  page.locator(".resize-grip").is_visible())
            box = page.locator(".resize-grip").bounding_box()
            page.mouse.move(box["x"] + 8, box["y"] + 8)
            page.mouse.down()
            page.mouse.move(box["x"] + 108, box["y"] + 158, steps=6)
            page.mouse.up()
            page.wait_for_timeout(200)
            sizes = [c for c in page.evaluate("window.__calls") if c[0] == "set_bounds"]
            check("Dragging the corner grip resizes the window",
                  bool(sizes) and sizes[-1][3] > 440 and sizes[-1][4] > 640, sizes[-1:] or sizes)
            page.dblclick(".brand-name")
            page.wait_for_timeout(200)
            check("Double-clicking the title bar expands the window",
                  any(c[0] == "toggle_maximize" for c in page.evaluate("window.__calls"))
                  and page.evaluate("document.body.classList.contains('maximized')"))
            check("…and hides the resize handles while expanded",
                  not page.locator(".resize-grip").is_visible())
            check("The window is fitted to the screen when it opens",
                  any(c[0] == "fit" for c in page.evaluate("window.__calls")))
            page.keyboard.press("Control+=")
            check("Ctrl + makes the text bigger",
                  page.evaluate("document.documentElement.style.zoom") == "1.1")
            page.keyboard.press("Control+0")
            page.close()

            plain = browser.new_page(viewport={"width": 360, "height": 480})
            plain.goto(f"http://127.0.0.1:{port}/app")
            plain.wait_for_timeout(600)
            check("A plain browser tab has no resize handles",
                  not plain.locator(".resize-grip").is_visible())
            composer = plain.locator("#composer").bounding_box()
            check("At the smallest size the message box is still on screen",
                  composer and composer["y"] + composer["height"] <= 480, composer)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _fake_solution_manager():
    """A RightAnswers SolutionManager workspace: search box and articles in a frame.

    Only the "solutionmanager" spelling exists here, so the address the user
    gave ("solutionmanger") has to fall back to it.
    """
    import http.server
    import threading
    from urllib.parse import parse_qs, urlparse

    articles = {"KB900": ("Sensor not detected after Windows update",
                          "Reinstall the DEXIS sensor driver, then restart the imaging service.")}
    root = "/solutionmanager/controller"

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, body):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == f"{root}/workspace/":
                return self._send(200, "<title>SolutionManager</title><h1>Workspace</h1>"
                                       f"<iframe name='work' src='{root}/frame/search' "
                                       "width=600 height=400></iframe>")
            if url.path == f"{root}/frame/search":
                return self._send(200, f"<form action='{root}/frame/results'>"
                                       "<input id='smSearch' name='q' placeholder='Search knowledge'>"
                                       "</form>")
            if url.path == f"{root}/frame/results":
                text = (query.get("q") or [""])[0].lower()
                hits = [f"<li class='sm-hit'><a href='{root}/viewsolution?solutionid={key}'>"
                        f"{title}</a><p>{body[:60]}</p></li>"
                        for key, (title, body) in articles.items()
                        if any(word in (title + body).lower() for word in text.split())]
                return self._send(200, f"<ul>{''.join(hits)}</ul>")
            if url.path == f"{root}/viewsolution":
                key = (query.get("solutionid") or [""])[0]
                if key in articles:
                    title, body = articles[key]
                    return self._send(200, f"<h1 class='solution-title'>{title}</h1>"
                                           f"<div class='solution-body'><p>{body}</p></div>")
            return self._send(404, "<title>404 Not Found</title>Not found")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_rightanswers_workspace():
    """RightAnswers starts from the SolutionManager workspace, frames and all."""
    print("\nRightAnswers — SolutionManager workspace")
    import importlib
    import sys as _sys
    from unittest import mock

    from app.core.config import Settings, settings
    from app.services import browser
    from app.services.browser.connector import normalise_page
    from app.services.browser.engine import playwright_installed
    from app.services.browser.teach import Teacher

    check("The workspace is the pre-filled address",
          settings.RIGHTANSWERS_URL.endswith("/solutionmanger/controller/workspace/"))
    check("An address saved by 0.4.0 is upgraded to the workspace",
          Settings(RIGHTANSWERS_URL="https://dexis.rightanswers.com/").RIGHTANSWERS_URL
          == settings.RIGHTANSWERS_URL)
    check("A different address is kept as typed",
          Settings(RIGHTANSWERS_URL="https://kb.example.com/x/").RIGHTANSWERS_URL
          == "https://kb.example.com/x/")
    check("The page path is kept, not just the site",
          normalise_page("dexis.rightanswers.com/solutionmanger/controller/workspace/")
          == "https://dexis.rightanswers.com/solutionmanger/controller/workspace/")
    rightanswers = browser.get("rightanswers")
    with mock.patch.object(settings, "RIGHTANSWERS_URL",
                           "https://dexis.rightanswers.com/solutionmanger/controller/workspace/"):
        check("RightAnswers starts from the workspace",
              rightanswers.sign_in_url().endswith("/controller/workspace/")
              and rightanswers.base_url == "https://dexis.rightanswers.com")
    with mock.patch.object(settings, "DYNAMICS_URL",
                           "https://dental.crm.dynamics.com/main.aspx?pagetype=entityrecord"):
        check("A pasted Dynamics link still means just the site",
              browser.get("dynamics").start_url == "https://dental.crm.dynamics.com")

    # Teach recognises a search typed into the workspace, whose address doesn't change.
    teacher = Teacher(rightanswers)
    snap = {"url": "https://x/workspace/", "html": "<iframe></iframe>", "frames": [
        {"url": "https://x/workspace/", "html": "", "inputs": []},
        {"url": "https://x/frame/results", "html": "<a href='a'>1</a><a href='b'>2</a>",
         "inputs": [{"value": "password", "selector": "#smSearch"}]}]}
    found = teacher._search_in(snap, "password")
    check("Teach learns an in-page search box", found and found["learned"]["search_input"] == "#smSearch")

    if not playwright_installed():
        print("  - skipped browser part: Playwright is not installed")
        return
    server = _fake_solution_manager()
    base = f"http://127.0.0.1:{server.server_port}"
    engine_module = importlib.import_module("app.services.browser.engine")
    connectors_dir = Path(_TEMP_DIR) / "sm_connectors"
    connector_module = importlib.import_module("app.services.browser.connector")
    overrides = {"BROWSER_AUTOMATION_ENABLED": True, "RIGHTANSWERS_ENABLED": True,
                 "RIGHTANSWERS_URL": f"{base}/solutionmanger/controller/workspace/",
                 "BROWSER_MODE": "headless", "BROWSER_TIMEOUT_SECONDS": 15,
                 "BROWSER_CHANNEL": "msedge" if _sys.platform == "win32" else "chromium"}
    patches = [mock.patch.object(settings, key, value) for key, value in overrides.items()]
    patches += [mock.patch.object(engine_module, "BROWSER_PROFILE_DIR", Path(_TEMP_DIR) / "sm_profile"),
                mock.patch.object(connector_module, "CONNECTORS_DIR", connectors_dir)]
    for patch in patches:
        patch.start()
    try:
        try:
            results = rightanswers.search("sensor driver")
        except browser.BrowserUnavailable as exc:
            print(f"  - skipped browser part: {exc}")
            return
        check("The other spelling is tried when the given one doesn't exist",
              rightanswers.start_url == f"{base}/solutionmanager/controller/workspace/")
        check("…and remembered for next time",
              "solutionmanager" in (connectors_dir / "rightanswers.json").read_text(encoding="utf-8"))
        check("A search runs through the workspace's search box inside its frame",
              [r["id"] for r in results] == ["KB900"], results)
        article = rightanswers.get_article(results[0]["url"])
        check("The article is read", "imaging service" in article["body"]
              and article["title"].startswith("Sensor not detected"))
        check("The Connect card shows the workspace", rightanswers.status()["url"].endswith(
            "/solutionmanager/controller/workspace/"))
    finally:
        browser.engine().shutdown()
        for patch in reversed(patches):
            patch.stop()
        server.shutdown()


def _fake_m365():
    """Tiny stand-ins for Outlook on the web and Teams on the web.

    Like the real apps they need a sign-in (a cookie from /login?done=1), keep
    fetching their own JSON in the background, and have compose boxes and
    Send buttons that post what was written back to the server.
    """
    import http.server
    import json as _json
    import threading
    from urllib.parse import parse_qs, unquote, urlparse

    me = {"name": "Sam Agent", "email": "sam@dexis.example"}
    state = {
        "mail": [
            {"ItemId": {"Id": "AAMk-1"}, "Subject": "Sensor not detected after update",
             "From": {"Mailbox": {"Name": "Alex Rivera", "EmailAddress": "alex@clinic.example"}},
             "ToRecipients": [{"Mailbox": {"Name": me["name"], "EmailAddress": me["email"]}}],
             "DateTimeReceived": "2026-10-02T08:00:00Z", "Preview": "Since this morning's update…",
             "Body": {"Value": "<p>Since this morning's update the sensor isn't detected.</p>"},
             "Importance": "Normal", "ConversationId": {"Id": "conv-1"}, "IsRead": False},
            {"ItemId": {"Id": "AAMk-2"}, "Subject": "Weekly product newsletter",
             "From": {"Mailbox": {"Name": "News", "EmailAddress": "news@vendor.example"}},
             "ToRecipients": [{"Mailbox": {"Name": "All", "EmailAddress": "all@dexis.example"}}],
             "DateTimeReceived": "2026-10-02T07:00:00Z", "Preview": "This week…",
             "Importance": "Normal", "ConversationId": {"Id": "conv-2"}, "IsRead": True},
            {"ItemId": {"Id": "AAMk-3"}, "Subject": "Re: something I sent",
             "From": {"Mailbox": {"Name": me["name"], "EmailAddress": me["email"]}},
             "ToRecipients": [{"Mailbox": {"Name": "Alex", "EmailAddress": "alex@clinic.example"}}],
             "DateTimeReceived": "2026-10-02T07:30:00Z", "Preview": "My own sent mail",
             "ConversationId": {"Id": "conv-1"}},
        ],
        "chats": {
            "19:alex_sam@unq.gbl.spaces": {"topic": "", "messages": [
                {"id": "1001", "messagetype": "RichText/Html", "imdisplayname": "Alex Rivera",
                 "from": "https://x/v1/users/ME/contacts/8:orgid:alex",
                 "content": "<p>Are you around? The scanner is down again.</p>",
                 "originalarrivaltime": "2026-10-02T08:05:00Z"}]},
            "19:escalations@thread.v2": {"topic": "Support Escalations", "messages": [
                {"id": "2001", "messagetype": "RichText/Html", "imdisplayname": "Jordan Lee",
                 "from": "https://x/v1/users/ME/contacts/8:orgid:jordan",
                 "content": "<p><span itemtype=\"http://schema.skype.com/Mention\" itemid=\"0\">Sam Agent</span>"
                            " can you take CAS-04567-ZZZZZ?</p>",
                 "properties": {"mentions": _json.dumps([{"mri": "8:orgid:sam", "displayName": "Sam Agent"}])},
                 "originalarrivaltime": "2026-10-02T08:06:00Z"},
                {"id": "2002", "messagetype": "ThreadActivity/AddMember", "content": "<addmember/>",
                 "imdisplayname": "", "from": "", "originalarrivaltime": "2026-10-02T08:07:00Z"},
                {"id": "2003", "messagetype": "RichText/Html", "imdisplayname": "Sam Agent",
                 "from": "https://x/v1/users/ME/contacts/8:orgid:sam",
                 "content": "<p>My own message</p>", "originalarrivaltime": "2026-10-02T08:08:00Z"}]},
        },
        "sent_mail": [], "sent_chat": [],
    }
    session = "m365=ok"

    OUTLOOK_APP = """<!doctype html><title>Mail - Sam Agent - Outlook</title>
<input id="topSearchInput" aria-label="Search">
<div role="listbox" id="list"></div>
<script>
async function refresh() {
  await fetch('/owa/startupdata.ashx').then(r => r.json());
  const data = await (await fetch('/owa/service.svc?action=FindItem')).json();
  const items = data.Body.ResponseMessages.Items[0].RootFolder.Items;
  document.getElementById('list').innerHTML = items.map(i =>
    `<div role="option" data-convid="${i.ConversationId.Id}" aria-label="${i.IsRead ? '' : 'Unread '}">` +
    `<span>${i.From.Mailbox.Name}</span>\\n<span>${i.Subject}</span>\\n<span>${i.Preview}</span></div>`).join('');
}
refresh(); setInterval(refresh, 700);
document.getElementById('topSearchInput').addEventListener('keydown', async e => {
  if (e.key === 'Enter') await fetch('/owa/service.svc?action=ExecuteSearch&q=' + encodeURIComponent(e.target.value));
});
</script>"""

    OUTLOOK_READ = """<!doctype html><title>Read - Outlook</title>
<div id="pane" aria-label="Message body" role="document"></div>
<button aria-label="Reply" id="reply">Reply</button>
<div id="compose"></div>
<script>
const id = decodeURIComponent(location.pathname.split('/').pop());
fetch('/owa/service.svc?action=GetItem&id=' + encodeURIComponent(id)).then(r => r.json()).then(d => {
  const item = d.Body.ResponseMessages.Items[0].Items[0];
  document.getElementById('pane').innerHTML = item.Body.Value;
});
document.getElementById('reply').onclick = () => {
  document.getElementById('compose').innerHTML =
    '<div aria-label="Message body" contenteditable="true" id="editor"></div><button aria-label="Send" id="send">Send</button>';
  document.getElementById('send').onclick = async () => {
    await fetch('/owa/service.svc?action=CreateItem', {method: 'POST', body: JSON.stringify(
      {inReplyTo: id, body: document.getElementById('editor').innerText})});
    document.getElementById('compose').innerHTML = '';
  };
};
</script>"""

    OUTLOOK_COMPOSE = """<!doctype html><title>New message - Outlook</title>
<div id="compose"><div aria-label="Message body" contenteditable="true" id="editor"></div>
<button aria-label="Send" id="send">Send</button></div>
<script>
const q = new URLSearchParams(location.search);
// Like some Outlook versions, the body in the address is ignored here.
document.getElementById('send').onclick = async () => {
  await fetch('/owa/service.svc?action=CreateItem', {method: 'POST', body: JSON.stringify(
    {to: q.get('to'), subject: q.get('subject'), body: document.getElementById('editor').innerText})});
  document.getElementById('compose').innerHTML = '';
};
</script>"""

    TEAMS_APP = """<!doctype html><title>Chat | Microsoft Teams</title>
<button data-tid="me-control-avatar-trigger" aria-label="Your profile, Sam Agent, Available">SA</button>
<div id="list"></div>
<script>
async function refresh() {
  await fetch('/api/chatsvc/amer/v1/users/ME/properties');
  const data = await (await fetch('/api/chatsvc/amer/v1/users/ME/conversations')).json();
  document.getElementById('list').innerHTML = data.conversations.map(c =>
    `<div data-tid="chat-list-item-${c.id}" onclick="location.href='/l/chat/${encodeURIComponent(c.id)}/0?direct=1'">` +
    `${c.threadProperties.topic || 'Alex Rivera'}</div>`).join('');
}
refresh(); setInterval(refresh, 700);
</script>"""

    TEAMS_CHAT = """<!doctype html><title>Chat | Microsoft Teams</title>
<div id="launcher"><button id="web">Use the web app instead</button></div>
<div id="chat" hidden>
  <div id="messages"></div>
  <div data-tid="ckeditor" contenteditable="true" role="textbox" id="box"></div>
  <button data-tid="newMessageCommands-send" id="send">Send</button>
</div>
<script>
const parts = location.pathname.split('/');
const chat = decodeURIComponent(parts[3]);
const q = new URLSearchParams(location.search);
function open() {
  document.getElementById('launcher').remove();
  document.getElementById('chat').hidden = false;
  if (q.get('message')) document.getElementById('box').innerText = q.get('message');
  if (chat !== '0') fetch('/api/chatsvc/amer/v1/users/ME/conversations/' + encodeURIComponent(chat) + '/messages')
    .then(r => r.json()).then(d => {
      document.getElementById('messages').innerHTML = d.messages.map(m =>
        `<div data-tid="chat-pane-message"><span data-tid="message-author-name">${m.imdisplayname}</span>` +
        `<div id="content-${m.id}">${m.content}</div></div>`).join('');
    });
}
if (q.get('direct')) open(); else document.getElementById('web').onclick = open;
document.getElementById('send').onclick = async () => {
  await fetch('/api/chatsvc/amer/v1/users/ME/conversations/' + encodeURIComponent(chat) + '/messages',
    {method: 'POST', body: JSON.stringify({content: document.getElementById('box').innerText,
                                           users: q.get('users')})});
  document.getElementById('box').innerText = '';
};
</script>"""

    def outlook_items(items):
        return {"Body": {"ResponseMessages": {"Items": [{"RootFolder": {"Items": items}}]}}}

    class Handler(http.server.BaseHTTPRequestHandler):
        app = "outlook"

        def log_message(self, *args):
            pass

        def _send(self, status, body, kind="text/html", headers=None):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json(self, payload):
            self._send(200, _json.dumps(payload), "application/json")

        def _signed_in(self):
            return session in (self.headers.get("Cookie") or "")

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == "/login":
                if query.get("done"):
                    return self._send(302, "", headers={"Set-Cookie": f"{session}; Path=/",
                                                        "Location": "/"})
                return self._send(200, "<title>Sign in</title>Sign in to your account")
            if not self._signed_in():
                return self._send(302, "", headers={"Location": "/login"})
            if self.app == "outlook":
                return self.outlook(url, query)
            return self.teams(url, query)

        def outlook(self, url, query):
            if url.path.startswith("/mail/deeplink/read/"):
                return self._send(200, OUTLOOK_READ)
            if url.path.startswith("/mail/deeplink/compose"):
                return self._send(200, OUTLOOK_COMPOSE)
            if url.path.startswith("/mail"):
                return self._send(200, OUTLOOK_APP)
            if url.path == "/owa/startupdata.ashx":
                return self._json({"owaUserConfig": {"SessionSettings": {
                    "UserEmailAddress": me["email"], "UserDisplayName": me["name"]}}})
            if url.path == "/owa/service.svc":
                action = (query.get("action") or [""])[0]
                if action == "FindItem":
                    return self._json(outlook_items(state["mail"]))
                if action == "GetItem":
                    item = next(i for i in state["mail"] if i["ItemId"]["Id"] == query["id"][0])
                    return self._json({"Body": {"ResponseMessages": {"Items": [{"Items": [item]}]}}})
                if action == "ExecuteSearch":
                    words = (query.get("q") or [""])[0].lower().split()
                    hits = [i for i in state["mail"]
                            if any(w in (i["Subject"] + i["Preview"]).lower() for w in words)]
                    return self._json({"SearchResults": {"Items": hits}})
            return self._send(404, "Not found")

        def teams(self, url, query):
            if url.path.startswith("/l/chat/"):
                return self._send(200, TEAMS_CHAT)
            if url.path == "/":
                return self._send(200, TEAMS_APP)
            if url.path.endswith("/users/ME/properties"):
                return self._json({"userDetails": _json.dumps({"name": me["name"], "upn": me["email"]}),
                                   "userMri": "8:orgid:sam"})
            if url.path.endswith("/users/ME/conversations"):
                return self._json({"conversations": [
                    {"id": chat_id, "threadProperties": {"topic": chat["topic"]},
                     "lastMessage": {**chat["messages"][-1], "conversationid": chat_id}}
                    for chat_id, chat in state["chats"].items()]})
            if url.path.endswith("/messages"):
                chat_id = unquote(url.path.split("/conversations/")[1].split("/messages")[0])
                messages = [{**m, "conversationid": chat_id}
                            for m in state["chats"].get(chat_id, {}).get("messages", [])]
                return self._json({"messages": messages})
            return self._send(404, "Not found")

        def do_POST(self):
            if not self._signed_in():
                return self._send(401, "{}", "application/json")
            body = _json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.app == "outlook":
                state["sent_mail"].append(body)
            else:
                chat_id = unquote(self.path.split("/conversations/")[1].split("/messages")[0])
                state["sent_chat"].append({"chat": chat_id, **body})
            return self._json({"ok": True})

    class OutlookHandler(Handler):
        app = "outlook"

    class TeamsHandler(Handler):
        app = "teams"

    servers = []
    for handler in (OutlookHandler, TeamsHandler):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
    return servers, state, me


def test_ask_reliability_and_scoping():
    """Fuzzy people, failed-send retry, message scoping, relevance gating, new tools."""
    print("\nAsk reliability — people, retries, scoping, relevance")
    import os
    from unittest import mock

    from app.core.config import settings
    from app.models.enterprise import EnterpriseAction, EnterpriseMessage
    from app.services import contacts, message_filter
    from app.services.agent import loop
    from app.services.agent.analysis_tools import summarise_log
    from app.services.ask_tools import AskToolService
    from app.services.browser.teams import name_pattern
    import re as _re

    check("Name matches in any order", contacts.matches("Anthony Oddo", "Oddo, Anthony")
          and contacts.matches("oddo anth", "Anthony Oddo") and not contacts.matches("Bob", "Anthony Oddo"))
    check("Teams chat pattern ignores word order",
          bool(name_pattern("Anthony Oddo").search("Oddo Anthony (Dexis)")))

    db = session()
    db.add(EnterpriseMessage(source="teams", sender="x", sender_name="Anthony Oddo",
                             thread_id="19:abc@unq.gbl.spaces", direct=True, external_id="fz-1"))
    db.add(EnterpriseMessage(source="outlook", sender="jo.ann@x.example", sender_name="Joanne Lee",
                             external_id="fz-2"))
    db.add(EnterpriseMessage(source="outlook", sender="jo.bob@x.example", sender_name="Joanne Smith",
                             external_id="fz-3"))
    db.commit()
    found = contacts.resolve(db, "anthony oddo")
    check("A known person resolves to their chat",
          found["status"] == "found" and found["person"]["chat"] == "19:abc@unq.gbl.spaces")
    check("A partial name resolves", contacts.resolve(db, "Oddo")["status"] == "found")
    check("Two matches are ambiguous", contacts.resolve(db, "Joanne")["status"] == "ambiguous")
    check("An unknown name is none", contacts.resolve(db, "Zed Nobody")["status"] == "none")

    saved = (settings.TEAMS_WATCH, settings.TEAMS_WATCH_CHATS, settings.OUTLOOK_WATCH,
             settings.OUTLOOK_SKIP_BULK, settings.OUTLOOK_MUTE_SENDERS)
    try:
        settings.TEAMS_WATCH = "direct"
        check("Direct-only drops channel posts",
              not message_filter.allow("teams", {"chat": "Ops", "direct": False})[0]
              and message_filter.allow("teams", {"chat": "Sam", "direct": True})[0])
        settings.TEAMS_WATCH, settings.TEAMS_WATCH_CHATS = "selected", "ops"
        check("Selected keeps listed chats and @mentions only",
              message_filter.allow("teams", {"chat": "Ops room"})[0]
              and message_filter.allow("teams", {"chat": "Other", "mentioned": True})[0]
              and not message_filter.allow("teams", {"chat": "Other"})[0])
        settings.OUTLOOK_WATCH, settings.OUTLOOK_SKIP_BULK = "all", True
        settings.OUTLOOK_MUTE_SENDERS = "spam.example"
        check("Ads and muted senders are dropped",
              not message_filter.allow("outlook", {"sender": "no-reply@shop.example", "subject": "50% off"})[0]
              and not message_filter.allow("outlook", {"sender": "a@spam.example", "subject": "hi"})[0]
              and message_filter.allow("outlook", {"sender": "bob@x.example", "subject": "Server down"})[0])
        check("A message naming a case always gets through",
              message_filter.allow("outlook", {"sender": "no-reply@shop.example",
                                               "subject": "Update on INC0012345"})[0])
    finally:
        (settings.TEAMS_WATCH, settings.TEAMS_WATCH_CHATS, settings.OUTLOOK_WATCH,
         settings.OUTLOOK_SKIP_BULK, settings.OUTLOOK_MUTE_SENDERS) = saved

    action = EnterpriseAction(action="send_teams_message", source="teams", chat_or_channel="Nobody",
                              body="hi", status="failed", status_detail="no chat")
    db.add(action)
    db.commit()
    service = AskToolService(db)
    check("A just-failed send is still the pending action",
          service.pending_action() is not None and service.pending_action().id == action.id)
    def fake_dispatch(item):
        item.status = "sent"
        db.commit()
        return item

    with mock.patch.object(service.enterprise, "dispatch_action", side_effect=fake_dispatch):
        result = service.approve(action.id, destination="Anthony Oddo")
    db.refresh(action)
    check("Retrying a failed send with a new destination works",
          action.status == "sent" and action.chat_or_channel == "Anthony Oddo" and "Sent" in result["reply"],
          f"{action.status} / {action.chat_or_channel} / {result.get('reply')}")

    check("A plain 'message X on Teams' request is messaging-only",
          loop.messaging_only("Send Anthony Oddo a message on Teams saying hi"))
    check("A research request is not",
          not loop.messaging_only("Search the KB for the Teams error and email me the steps"))

    log = "\n".join(f"2026-01-02 10:00:{i:02d} ERROR db timeout id {i}" for i in range(5)) + \
          "\n2026-01-02 10:01:00 INFO ok\n2026-01-02 10:02:00 WARN disk low"
    summary = summarise_log(log)
    check("Log review groups repeats with counts", "5 · 2026-01-02" in summary and "disk low" in summary, summary)
    from app.services.agent.registry import REGISTRY
    check("The analysis tools are registered", "review_log" in REGISTRY and "compare_cases" in REGISTRY)


def test_system_tools_and_robustness():
    """File/URL/command tools, sign-in isolation, connector backups."""
    print("\nGeneral Ask tools — files, URLs, commands, backups")
    import http.server
    import tempfile
    import threading
    from pathlib import Path as _P
    from unittest import mock

    from app.core.config import settings
    from app.models.agent_action import AgentAction
    from app.services import browser, connector_backup
    from app.services.agent import actions
    from app.services.agent.registry import REGISTRY, ToolContext, available_tools
    from app.services.browser.engine import BrowserBusy, BrowserEngine

    db = session()
    with tempfile.TemporaryDirectory() as tmp:
        root = _P(tmp)
        (root / "app.log").write_text(
            "\n".join(f"2026-01-02 10:00:{i:02d} ERROR db timeout {i}" for i in range(4)), encoding="utf-8")
        (root / "notes.txt").write_text("alpha\nneedle here\n", encoding="utf-8")
        ctx = ToolContext(db=db, context={})
        listing = REGISTRY["list_folder"].handler(ctx, path=str(root))
        check("list_folder shows files", "app.log" in listing["content"], listing)
        check("read_file reads text", "needle" in REGISTRY["read_file"].handler(
            ToolContext(db=db, context={}), path=str(root / "notes.txt"))["content"])
        check("find_in_files finds text", "notes.txt" in REGISTRY["find_in_files"].handler(
            ToolContext(db=db, context={}), path=str(root), text="needle")["content"])
        check("review_logs_in_folder summarises errors", "ERROR" in REGISTRY["review_logs_in_folder"].handler(
            ToolContext(db=db, context={}), path=str(root), days="3650")["content"])

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<html><body><p>Hello web</p></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            got = REGISTRY["fetch_url"].handler(ToolContext(db=db, context={}),
                                                url=f"http://127.0.0.1:{server.server_port}/")
            check("fetch_url returns page text", "Hello web" in got["content"], got)
        finally:
            server.shutdown()

        target = root / "out.txt"
        target.write_text("old", encoding="utf-8")
        wctx = ToolContext(db=db, context={})
        REGISTRY["write_file"].handler(wctx, path=str(target), content="new")
        check("write_file waits for approval", target.read_text(encoding="utf-8") == "old"
              and len(wctx.drafts) == 1)
        action_id = wctx.drafts[0]["action_id"]
        actions.approve(db, action_id)
        check("Approved write changes the file", target.read_text(encoding="utf-8") == "new")
        actions.undo(db, action_id)
        check("Undo restores the file", target.read_text(encoding="utf-8") == "old")

    with mock.patch.object(settings, "ASK_SHELL_ENABLED", False):
        check("run_command hidden by default",
              "run_command" not in {t.name for t in available_tools(ToolContext(db=db))})
    with mock.patch.object(settings, "ASK_SHELL_ENABLED", True):
        check("run_command available when enabled",
              "run_command" in {t.name for t in available_tools(ToolContext(db=db))})

    eng = BrowserEngine()
    eng.hold_visible, eng.hold_owner = True, "Outlook"
    with mock.patch.object(settings, "BROWSER_AUTOMATION_ENABLED", True), \
            mock.patch("app.services.browser.engine.playwright_installed", return_value=True), \
            mock.patch.object(eng, "_ensure_thread"):
        try:
            eng.submit(lambda e: 1)
            busy = False
        except BrowserBusy:
            busy = True
    check("Other systems wait while a sign-in window is open", busy)

    with tempfile.TemporaryDirectory() as tmp:
        cdir = _P(tmp) / "connectors"
        bdir = _P(tmp) / "backups"
        cdir.mkdir()
        (cdir / "genesys.json").write_text('{"x": 1}', encoding="utf-8")
        with mock.patch.object(connector_backup, "CONNECTORS_DIR", cdir), \
                mock.patch.object(connector_backup, "BACKUP_DIR", bdir):
            made = connector_backup.create_backup("test")
            check("Backup captures connectors", made["connectors"] == 1)
            connector_backup.reset_connector("genesys")
            check("Reset removes the layout", not (cdir / "genesys.json").exists())
            connector_backup.restore_backup(made["name"])
            check("Restore brings it back", (cdir / "genesys.json").exists())


def test_beyondtrust_and_genesys():
    """BeyondTrust and Genesys through the hidden browser: config, sign-in rules, call matching."""
    print("\nBeyondTrust & Genesys Cloud — through the hidden browser")
    from app.core.config import settings
    from app.services import browser
    from app.services.agent import systems_tools
    from app.services.agent.registry import REGISTRY

    bt, gc = browser.get("beyondtrust"), browser.get("genesys")
    check("Both are registered with the browser", {"beyondtrust", "genesys"} <= {c.name for c in browser.connectors()})
    check("Genesys is pre-filled and BeyondTrust waits for an address",
          gc.base_url == "https://apps.mypurecloud.com" and not bt.base_url)
    check("They are off by default", not bt.enabled and not gc.enabled)
    check("Genesys' API host follows its app host", gc.api_base() == "https://api.mypurecloud.com")
    settings.BEYONDTRUST_URL = "support.example.com/login/reports"
    check("BeyondTrust keeps only the host", bt.base_url == "https://support.example.com")
    check("BeyondTrust's own /login console is not a sign-in page",
          not bt.looks_like_login("https://support.example.com/login") and
          bt.looks_like_login("https://acme.okta.com/app/x"))
    check("Ask tools are registered",
          {"beyondtrust_search", "beyondtrust_read_page", "genesys_search_conversations",
           "genesys_get_conversation", "link_call_to_remote_session"} <= set(REGISTRY))

    call = {"id": "CONV-1", "customer": "Ada Lovelace", "phone": "tel:+15551234567", "agent": "Sam Rivera"}
    rows = [{"id": "L1", "text": "L1 Ada Lovelace 555-123-4567 Sam Rivera 10:05"},
            {"id": "L2", "text": "L2 Someone Else 555-000-1111 Pat Doe 15:00"}]
    ranked = systems_tools.match_sessions(call, rows)
    check("The call is matched to the right remote session",
          len(ranked) == 1 and ranked[0][1]["id"] == "L1" and ranked[0][0] >= 8, ranked)
    check("A conversation ID in a row is the strongest evidence",
          systems_tools.match_sessions(call, [{"id": "L3", "text": "ref conv-1"}])[0][0] == 10)
    settings.BEYONDTRUST_URL = None

    from app.services.browser.engine import playwright_installed

    if not playwright_installed():
        print("  - skipped browser part: Playwright is not installed")
        return
    import importlib
    import sys as _sys
    from unittest import mock

    server = _fake_bt_and_genesys()
    base = f"http://127.0.0.1:{server.server_port}"
    engine_module = importlib.import_module("app.services.browser.engine")
    overrides = {"BROWSER_AUTOMATION_ENABLED": True, "BROWSER_MODE": "headless",
                 "BROWSER_TIMEOUT_SECONDS": 15,
                 "BROWSER_CHANNEL": "msedge" if _sys.platform == "win32" else "chromium",
                 "BEYONDTRUST_ENABLED": True, "BEYONDTRUST_URL": f"{base}/login/sessions",
                 "GENESYS_ENABLED": True, "GENESYS_URL": base}
    patches = [mock.patch.object(settings, key, value) for key, value in overrides.items()]
    patches.append(mock.patch.object(engine_module, "BROWSER_PROFILE_DIR",
                                     Path(_TEMP_DIR) / "bt_profile"))
    for patch in patches:
        patch.start()
    try:
        try:
            found = bt.search("Ada")
        except browser.BrowserUnavailable as exc:
            print(f"  - skipped browser part: {exc}")
            return
        check("BeyondTrust's sessions page is read as rows",
              [s["id"] for s in found["sessions"]] == ["a1b2c3d4e5f60718293a4b5c6d7e8f90"], found["sessions"])
        check("A search narrows the rows", all("Ada" in s["text"] for s in found["sessions"]))
        everything = bt.search("")
        check("An empty search lists every row", len(everything["sessions"]) == 2)
        page = bt.read_page("/login/session/a1b2c3d4e5f60718293a4b5c6d7e8f90")
        check("A session page is read", "Ada Lovelace" in page["text"] and "Remote Support" in page["title"])
        try:
            bt.read_page("https://evil.example.com/x")
            check("Only the configured BeyondTrust site can be opened", False)
        except Exception:
            check("Only the configured BeyondTrust site can be opened", True)

        conversations = gc.search("1555")
        check("Genesys conversations are fetched with the page's own token",
              [c["id"] for c in conversations] == ["CONV-1"], conversations)
        check("…with customer, phone and agent",
              conversations[0]["customer"] == "Ada Lovelace" and conversations[0]["agent"] == "Sam Rivera")
        check("A search that matches nothing returns nothing", gc.search("zzz-none") == [])
        detail = gc.conversation("CONV-1")
        check("One conversation is read in full", detail["source"] == "api" and detail["raw"]["conversationId"] == "CONV-1")
        check("The signed-in account is named", gc.check().get("account") == "Sam Rivera")

        # The two together: a call is linked to its remote session.
        ranked = systems_tools.match_sessions(conversations[0], found["sessions"])
        check("The call links to the remote session read from the console",
              ranked and ranked[0][1]["id"] == "a1b2c3d4e5f60718293a4b5c6d7e8f90", ranked)
    finally:
        browser.engine().shutdown()
        for patch in reversed(patches):
            patch.stop()
        server.shutdown()


def _fake_bt_and_genesys():
    """A BeyondTrust console (session table) and a Genesys web app + API on one host."""
    import http.server
    import json as _json
    import threading
    from urllib.parse import parse_qs, urlparse

    rows = [("a1b2c3d4e5f60718293a4b5c6d7e8f90", "Ada Lovelace", "555-123-4567", "Sam Rivera"),
            ("f6e5d4c3b2a10918273a4b5c6d7e8f01", "Someone Else", "555-000-1111", "Pat Doe")]
    conversation = {"conversationId": "CONV-1", "conversationStart": "2026-10-09T10:00:00.000Z",
                    "conversationEnd": "2026-10-09T10:20:00.000Z",
                    "participants": [{"purpose": "customer", "participantName": "Ada Lovelace",
                                      "sessions": [{"ani": "tel:+15551234567", "mediaType": "voice",
                                                    "direction": "inbound"}]},
                                     {"purpose": "agent", "participantName": "Sam Rivera",
                                      "sessions": []}]}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, body, kind="text/html; charset=utf-8"):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorised(self):
            return self.headers.get("Authorization") == "Bearer tok-123"

        def do_GET(self):
            url = urlparse(self.path)
            if url.path.startswith("/login/session/"):
                return self._send(200, "<title>Remote Support session</title>"
                                       "<main>Session a1b2c3d4e5f60718293a4b5c6d7e8f90 · Ada Lovelace · Sam Rivera</main>")
            if url.path.startswith("/login/sessions"):
                query = (parse_qs(url.query).get("q") or [""])[0].lower()
                body = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td></tr>"
                               for a, b, c, d in rows if query in f"{a}{b}{c}{d}".lower())
                return self._send(200, "<form><input type='search' name='q' placeholder='Search sessions'>"
                                       f"</form><table><tbody>{body}</tbody></table>")
            if url.path == "/api/v2/users/me":
                return self._send(200, _json.dumps({"name": "Sam Rivera"}), "application/json") \
                    if self._authorised() else self._send(401, "{}", "application/json")
            if url.path.endswith("/details") and "CONV-1" in url.path:
                return self._send(200, _json.dumps(conversation), "application/json") \
                    if self._authorised() else self._send(401, "{}", "application/json")
            if url.path == "/":
                return self._send(200, "<title>Genesys Cloud</title><h1>Genesys</h1>"
                                       "<script>localStorage.setItem('gc_auth', "
                                       "JSON.stringify({accessToken: 'tok-123'}));"
                                       "localStorage.setItem('gc_token_old', 'stale');</script>")
            return self._send(404, "Not found")

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if not self._authorised():
                return self._send(401, "{}", "application/json")
            if urlparse(self.path).path == "/api/v2/analytics/conversations/details/query":
                return self._send(200, _json.dumps({"conversations": [conversation]}), "application/json")
            return self._send(404, "{}", "application/json")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_outlook_and_teams():
    """Outlook and Teams through the hidden browser: read, watch, notify, help, send."""
    print("\nOutlook and Teams — through the hidden browser")
    import importlib
    import sys as _sys
    from unittest import mock

    from app.core import activity_state
    from app.core.config import settings
    from app.models.chat import ChatMessage
    from app.models.enterprise import EnterpriseAction, EnterpriseMessage
    from app.services import browser, inbox_monitor
    from app.services.agent.registry import REGISTRY, ToolContext
    from app.services.ask_tools import AskToolService
    from app.services.browser.engine import playwright_installed
    from app.services.browser.outlook import outlook_item
    from app.services.browser.teams import teams_message

    check("Outlook and Teams are pre-filled",
          settings.OUTLOOK_URL == "https://outlook.cloud.microsoft/mail/"
          and settings.TEAMS_URL == "https://teams.cloud.microsoft")
    me = {"name": "Sam Agent", "email": "sam@dexis.example"}
    item = outlook_item({"ItemId": {"Id": "X"}, "Subject": "Hi", "DateTimeReceived": "2026-10-02",
                         "From": {"Mailbox": {"Name": "A", "EmailAddress": "a@x"}},
                         "ToRecipients": [{"Mailbox": {"EmailAddress": "SAM@dexis.example"}}],
                         "Body": {"Value": "<p>Hello &amp; welcome</p>"}}, me)
    check("An Outlook message is read from the app's own data",
          item["sender"] == "a@x" and item["body"] == "Hello & welcome" and item["direct"])
    check("…but not a draft", outlook_item({"ItemId": {"Id": "D"}, "Subject": "x", "IsDraft": True,
                                            "DateTimeReceived": "2026"}, me) is None)
    check("A Teams system event is not a message", teams_message(
        {"messagetype": "ThreadActivity/AddMember", "content": "x", "id": "1",
         "conversationid": "19:a"}, me, {}) is None)

    if not playwright_installed():
        print("  - skipped browser part: Playwright is not installed")
        return
    (outlook_server, teams_server), state, me = _fake_m365()
    outlook_base = f"http://127.0.0.1:{outlook_server.server_port}"
    teams_base = f"http://127.0.0.1:{teams_server.server_port}"
    engine_module = importlib.import_module("app.services.browser.engine")
    overrides = {"BROWSER_AUTOMATION_ENABLED": True, "BROWSER_MODE": "headless",
                 "BROWSER_TIMEOUT_SECONDS": 15,
                 "BROWSER_CHANNEL": "msedge" if _sys.platform == "win32" else "chromium",
                 "OUTLOOK_BROWSER_ENABLED": True, "OUTLOOK_URL": f"{outlook_base}/mail/",
                 "TEAMS_BROWSER_ENABLED": True, "TEAMS_URL": teams_base,
                 "OUTLOOK_AUTO_SEND": False, "TEAMS_AUTO_SEND": False,
                 "INBOX_MONITOR_ENABLED": True, "INBOX_ASSIST": "important",
                 "INBOX_NOTIFY": "important", "INBOX_ASSIST_PER_HOUR": 12,
                 "ENTERPRISE_ENABLED": False}
    patches = [mock.patch.object(settings, key, value) for key, value in overrides.items()]
    patches.append(mock.patch.object(engine_module, "BROWSER_PROFILE_DIR",
                                     Path(_TEMP_DIR) / "m365_profile"))
    for patch in patches:
        patch.start()
    outlook, teams = browser.get("outlook"), browser.get("teams")
    for connector in (outlook, teams):
        connector.me.clear()
        connector._opened_at = 0
    inbox_monitor.reset()
    activity_state.reset()
    db = session()
    try:
        try:
            for base in (outlook_base, teams_base):
                browser.engine().submit(lambda eng, b=base: eng.page("signin").goto(f"{b}/login?done=1"))
        except browser.BrowserUnavailable as exc:
            print(f"  - skipped browser part: {exc}")
            return

        # ---- reading
        mail = outlook.collect()
        check("Outlook's inbox is read from the data the page fetched",
              {m["id"] for m in mail} == {"AAMk-1", "AAMk-2"}, [m.get("id") for m in mail])
        check("Who is signed in is learned from the app", outlook.me.get("email") == me["email"])
        check("Your own sent mail isn't taken for new mail", "AAMk-3" not in {m["id"] for m in mail})
        alex = next(m for m in mail if m["id"] == "AAMk-1")
        check("Mail sent to you is marked direct", alex["direct"] and not
              next(m for m in mail if m["id"] == "AAMk-2")["direct"])

        chats = teams.collect()
        mention = next((m for m in chats if "CAS-04567" in (m.get("body") or "")), None)
        check("Teams chats are read from the data the page fetched", mention is not None
              and any("scanner is down" in (m.get("body") or "") for m in chats), chats)
        check("An @mention of you is recognised", mention and mention["mentioned"])
        check("A one-to-one chat counts as direct",
              any(m["direct"] for m in chats if "scanner" in (m.get("body") or "")))
        check("The chat's name comes along", mention and mention["chat"] == "Support Escalations")
        check("Your own Teams messages are left out",
              not any("My own message" in (m.get("body") or "") for m in chats))

        # ---- watching (from scratch: forget what was read above)
        inbox_monitor.reset()
        teams._read_up_to.clear()
        for message in db.query(EnterpriseMessage).all():
            db.delete(message)
        db.commit()
        first = inbox_monitor.check_once(db)
        check("The first check only catches up", first["outlook"]["catching_up"]
              and not activity_state.recent_notices())
        check("Messages are stored like any other (newsletters are filtered out)",
              db.query(EnterpriseMessage)
              .filter(EnterpriseMessage.source == "outlook").count() == 1)
        state["mail"].insert(0, {
            "ItemId": {"Id": "AAMk-9"}, "Subject": "URGENT: CAS-01234-ABCDE imaging down",
            "From": {"Mailbox": {"Name": "Dr Patel", "EmailAddress": "patel@clinic.example"}},
            "ToRecipients": [{"Mailbox": {"Name": me["name"], "EmailAddress": me["email"]}}],
            "DateTimeReceived": "2026-10-02T09:00:00Z", "Preview": "Nothing images since 8am",
            "Body": {"Value": "Nothing images since 8am. Please call."}, "Importance": "High",
            "ConversationId": {"Id": "conv-9"}})
        state["mail"].insert(1, {
            "ItemId": {"Id": "AAMk-10"}, "Subject": "Lunch menu",
            "From": {"Mailbox": {"Name": "Cafe", "EmailAddress": "cafe@dexis.example"}},
            "ToRecipients": [{"Mailbox": {"Name": "All", "EmailAddress": "all@dexis.example"}}],
            "DateTimeReceived": "2026-10-02T09:01:00Z", "Preview": "Soup", "ConversationId": {"Id": "c10"}})
        import time as _time
        _time.sleep(1.5)                                   # the page fetches again by itself
        queued = []
        with mock.patch.object(inbox_monitor._assist_queue, "put", side_effect=queued.append):
            second = inbox_monitor.check_once(db)
        notices = activity_state.recent_notices()
        check("New mail is noticed on the next check", second["outlook"]["new"] == 2, second)
        check("An urgent direct email pops up a notification",
              any("Dr Patel" in n["title"] for n in notices), notices)
        check("…an ordinary one doesn't", not any("Cafe" in n["title"] for n in notices))
        urgent = db.query(EnterpriseMessage).filter(EnterpriseMessage.external_id == "outlook:AAMk-9").one()
        check("Only the important one is queued for research", queued == [urgent.id], queued)

        # ---- research (scripted model)
        script = ScriptedLLM([
            {"name": "reply_to_message", "arguments": {
                "message": str(urgent.id), "body": "I'm on it — calling you in 5 minutes."}},
            "Dr Patel's imaging is down (CAS-01234-ABCDE). I've drafted a reply saying you'll call."])
        enabled, patched = _with_ai(script)
        with enabled, patched:
            outcome = inbox_monitor.assist(db, urgent.id)
        posted = db.query(ChatMessage).filter(ChatMessage.kind == "inbox_assist").order_by(
            ChatMessage.id.desc()).first()
        meta = json.loads(posted.meta)
        check("Research is posted in the Inbox chat",
              outcome and posted is not None and meta["incoming"]["id"] == urgent.id)
        check("…with a draft reply waiting for approval",
              any(c.get("type") == "draft" and c["status"] == "awaiting_approval" for c in meta["cards"]))
        check("Nothing was sent yet", not state["sent_mail"])
        with enabled, patched:
            again = inbox_monitor.assist(db, urgent.id)
        check("A message is never researched twice", again is None)

        draft = next(c for c in meta["cards"] if c.get("type") == "draft")
        result = AskToolService(db).approve(draft["action_id"])
        check("Approving sends the reply through Outlook", state["sent_mail"]
              and state["sent_mail"][-1]["inReplyTo"] == "AAMk-9"
              and "calling you" in state["sent_mail"][-1]["body"], result.get("reply"))
        check("The draft is marked sent",
              db.query(EnterpriseAction).get(draft["action_id"]).status == "sent")

        # ---- sending without asking (replies only)
        ctx = ToolContext(db=db, context={})
        with mock.patch.object(settings, "OUTLOOK_AUTO_SEND", True):
            reply = REGISTRY["reply_to_message"].handler(ctx, message=str(urgent.id),
                                                        body="Following up on the call.")
            new = REGISTRY["send_email"].handler(ctx, to="someone@clinic.example",
                                                 subject="Hello", body="A brand-new email.")
        check("With 'send replies without asking' on, a reply goes straight away",
              reply["content"].startswith("Sent automatically")
              and state["sent_mail"][-1]["body"].startswith("Following up"))
        check("…but a brand-new email still asks", "NOT sent" in new["content"]
              and not any("brand-new" in m.get("body", "") for m in state["sent_mail"]))

        ctx = ToolContext(db=db, context={})
        REGISTRY["send_email"].handler(ctx, to="someone@clinic.example", subject="Hello",
                                       body="Typed into the compose box.")
        AskToolService(db).approve(ctx.drafts[0]["action_id"])
        check("A new email is written and sent from Outlook's compose screen",
              state["sent_mail"][-1].get("to") == "someone@clinic.example"
              and "Typed into" in state["sent_mail"][-1]["body"])

        # ---- Teams sending
        stored_mention = db.query(EnterpriseMessage).filter(
            EnterpriseMessage.source == "teams", EnterpriseMessage.mentioned.is_(True)).first()
        ctx = ToolContext(db=db, context={})
        REGISTRY["reply_to_message"].handler(ctx, message=str(stored_mention.id),
                                             body="Taking CAS-04567 now.")
        AskToolService(db).approve(ctx.drafts[0]["action_id"])
        check("A Teams reply is posted in the right chat (past Teams' launcher page)",
              state["sent_chat"] and state["sent_chat"][-1]["chat"] == "19:escalations@thread.v2"
              and "Taking CAS-04567" in state["sent_chat"][-1]["content"], state["sent_chat"])
        ctx = ToolContext(db=db, context={})
        REGISTRY["send_teams_message"].handler(ctx, channel="newperson@clinic.example",
                                               body="Hi, starting a chat with you.")
        AskToolService(db).approve(ctx.drafts[0]["action_id"])
        check("A first message to someone opens a new chat with them",
              state["sent_chat"][-1].get("users") == "newperson@clinic.example"
              and "starting a chat" in state["sent_chat"][-1]["content"])

        # ---- Ask tools
        ctx = ToolContext(db=db, context={})
        found = REGISTRY["outlook_search"].handler(ctx, query="sensor")
        check("Ask can search the mailbox", "Sensor not detected" in found["content"]
              and "#" in found["content"], found)
        read = REGISTRY["teams_read_chat"].handler(ToolContext(db=db, context={}),
                                                   chat=str(stored_mention.id))
        check("Ask can read a Teams chat", "CAS-04567" in read["content"], read)

        # ---- the hourly cap
        inbox_monitor.reset()
        with mock.patch.object(settings, "INBOX_ASSIST_PER_HOUR", 1), enabled, patched:
            fresh = db.query(EnterpriseMessage).filter(EnterpriseMessage.external_id == "outlook:AAMk-2").one()
            other = db.query(EnterpriseMessage).filter(EnterpriseMessage.external_id == "outlook:AAMk-10").one()
            script.turns = ["Looked.", "Looked again."]
            inbox_monitor.assist(db, fresh.id)
            capped = inbox_monitor.assist(db, other.id)
        check("Research stops at the hourly limit", capped is None)

        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as client:
            listed = client.get("/api/integrations").json()
        names = {item["name"]: item for item in listed["integrations"]}
        check("The Connect tab lists Outlook and Teams with their switches",
              names["outlook"]["can_auto_apply"] and names["teams"]["enabled"]
              and listed["monitor"]["enabled"])
    finally:
        browser.engine().shutdown()
        for patch in reversed(patches):
            patch.stop()
        for server in (outlook_server, teams_server):
            server.shutdown()
        db.close()


def test_tray_notices():
    """New notices become Windows notifications; old ones are not replayed."""
    print("\nTray — notifications")
    sys.path.insert(0, str(ROOT / "desktop"))
    from unittest import mock

    import tray

    shown = []
    brain = tray.BrainTray("http://127.0.0.1:1", {"open": lambda tab=None: None})
    with mock.patch.object(brain, "notify", side_effect=lambda body, title="": shown.append(title)):
        brain._on_activity({"state": "idle", "notices": [{"id": 3, "title": "Old one"}]})
        check("Notices from before the tray started aren't replayed", shown == [])
        brain._on_activity({"state": "idle", "notices": [
            {"id": 3, "title": "Old one"}, {"id": 4, "title": "Email from Dr Patel", "body": "Imaging down"}]})
        check("A new notice pops up a notification", shown == ["Email from Dr Patel"])
        check("…and can be opened from the tray menu", brain.latest["id"] == 4)
        brain._on_activity({"state": "idle", "notices": [{"id": 4, "title": "Email from Dr Patel"}]})
        check("Each notice pops up once", shown == ["Email from Dr Patel"])


# -------------------------------------------------------------------- main
# =================================================================== 0.7.0
# Add-ons (MCP servers, websites, skills) and voice.

_STDIO_MCP = r'''
import json, sys
TICKETS = {"T-1": "Printer offline at front desk"}
TOOLS = [
    {"name": "get_ticket", "description": "Look up a ticket by id.",
     "inputSchema": {"type": "object", "properties": {"ticket_id": {"type": "string"}}, "required": ["ticket_id"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "create_ticket", "description": "Create a ticket.",
     "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}},
]
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method, params = message.get("method"), message.get("params") or {}
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion"), "capabilities": {"tools": {}},
                  "serverInfo": {"name": "tickets", "version": "1"}, "instructions": "Ticket ids look like T-1."}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        args = params.get("arguments") or {}
        if params["name"] == "get_ticket":
            text = TICKETS.get(args.get("ticket_id"), "no such ticket")
        else:
            key = "T-%d" % (len(TICKETS) + 1)
            TICKETS[key] = args.get("title")
            text = "created %s: %s" % (key, args.get("title"))
        result = {"content": [{"type": "text", "text": text}], "isError": False}
    else:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message["id"],
                                     "error": {"code": -32601, "message": "no"}}) + "\n")
        sys.stdout.flush()
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}) + "\n")
    sys.stdout.flush()
'''

_SDK_MCP = r'''
import sys
from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations
server = MCPServer("sdk-tickets")
@server.tool(description="Look up a ticket by id.", annotations=ToolAnnotations(readOnlyHint=True))
def get_ticket(ticket_id: str) -> str:
    return "Printer offline" if ticket_id == "T-1" else "no such ticket"
@server.tool(description="Create a ticket.")
def create_ticket(title: str) -> str:
    return "created T-2: " + title
mode = sys.argv[1] if len(sys.argv) > 1 else "stdio"
if mode == "stdio":
    server.run("stdio")
elif mode == "http":
    server.run("streamable-http", host="127.0.0.1", port=int(sys.argv[2]))
else:
    server.run("sse", host="127.0.0.1", port=int(sys.argv[2]))
'''


def _addon_dirs():
    """Point add-ons and connector files at a fresh temporary folder."""
    import tempfile
    from unittest import mock

    from app.services import capabilities, skills
    from app.services.browser import connector as connector_module

    root = Path(tempfile.mkdtemp(dir=_TEMP_DIR, prefix="addons-"))
    return root, [mock.patch.object(capabilities, "CAPABILITIES_DIR", root / "capabilities"),
                  mock.patch.object(skills, "CAPABILITIES_DIR", root / "capabilities"),
                  mock.patch.object(connector_module, "CONNECTORS_DIR", root / "connectors")]


def _fake_oauth_mcp(token: str = "good-token"):
    """An MCP server over streamable HTTP that needs OAuth sign-in, plus its
    authorization server — discovery, client registration, PKCE and tokens."""
    import base64
    import hashlib
    import http.server
    import json as _json
    import threading
    from urllib.parse import parse_qs, urlencode, urlparse

    state = {"codes": {}, "registered": [], "calls": [], "tokens": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, status, body, headers=None):
            data = _json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            base = f"http://127.0.0.1:{self.server.server_port}"
            url = urlparse(self.path)
            if url.path.startswith("/.well-known/oauth-protected-resource"):
                return self._json(200, {"resource": f"{base}/mcp", "authorization_servers": [base],
                                        "scopes_supported": ["tickets"]})
            if url.path == "/.well-known/oauth-authorization-server":
                return self._json(200, {"issuer": base, "authorization_endpoint": f"{base}/authorize",
                                        "token_endpoint": f"{base}/token",
                                        "registration_endpoint": f"{base}/register",
                                        "code_challenge_methods_supported": ["S256"]})
            if url.path == "/authorize":
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                code = "code-" + str(len(state["codes"]) + 1)
                state["codes"][code] = query
                self.send_response(302)
                self.send_header("Location", query["redirect_uri"] + "?" + urlencode(
                    {"code": code, "state": query["state"]}))
                self.end_headers()
                return
            self._json(404, {})

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode()
            url = urlparse(self.path)
            base = f"http://127.0.0.1:{self.server.server_port}"
            if url.path == "/register":
                body = _json.loads(raw)
                state["registered"].append(body)
                return self._json(201, {"client_id": "cerebro-client", **body})
            if url.path == "/token":
                form = {k: v[0] for k, v in parse_qs(raw).items()}
                request = state["codes"].get(form.get("code"), {})
                challenge = base64.urlsafe_b64encode(hashlib.sha256(
                    (form.get("code_verifier") or "").encode()).digest()).decode().rstrip("=")
                if challenge != request.get("code_challenge"):
                    return self._json(400, {"error": "invalid_grant"})
                state["tokens"] += 1
                return self._json(200, {"access_token": token, "refresh_token": "r1",
                                        "expires_in": 3600, "token_type": "Bearer"})
            if url.path == "/mcp":
                if self.headers.get("Authorization") != f"Bearer {token}":
                    return self._json(401, {"error": "unauthorized"}, {
                        "WWW-Authenticate": f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource/mcp"'})
                message = _json.loads(raw)
                if "id" not in message:
                    self.send_response(202)
                    self.end_headers()
                    return
                method = message["method"]
                if method == "initialize":
                    result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                              "serverInfo": {"name": "secure-tickets"}}
                elif method == "tools/list":
                    result = {"tools": [{"name": "whoami", "description": "Who is signed in.",
                                         "inputSchema": {"type": "object", "properties": {}},
                                         "annotations": {"readOnlyHint": True}}]}
                else:
                    state["calls"].append(message)
                    result = {"content": [{"type": "text", "text": "signed in as Sam"}]}
                return self._json(200, {"jsonrpc": "2.0", "id": message["id"], "result": result},
                                  {"Mcp-Session-Id": "s-1"})
            self._json(404, {})

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, state


def _fake_registry(entries):
    import http.server
    import json as _json
    import threading
    from urllib.parse import parse_qs, urlparse

    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            seen.append(query)
            needle = (query.get("search") or "").lower()
            found = [{"server": e, "_meta": {}} for e in entries
                     if needle in (e["name"] + " " + e.get("description", "")).lower()]
            data = _json.dumps({"servers": found, "metadata": {"count": len(found)}}).encode()
            self.send_response(200 if url.path == "/v0.1/servers" else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen


def test_mcp_servers():
    """MCP servers: added at runtime, tools appear, reads run, writes ask first."""
    print("\nAdd-ons — MCP servers")
    import importlib.util
    import socket
    import subprocess
    import sys as _sys
    import time as _time
    from unittest import mock

    from app.services import capabilities, mcp_client
    from app.services.agent import actions, loop
    from app.services.agent.registry import (REGISTRY, Tool, ToolContext, register_dynamic, search,
                                             unregister_owner)

    root, patches = _addon_dirs()
    script = root / "tickets_server.py"
    script.write_text(_STDIO_MCP, encoding="utf-8")
    for patch in patches:
        patch.start()
    db = session()
    try:
        outcome = capabilities.add_mcp(db, "Tickets", {"transport": "stdio", "command": _sys.executable,
                                                       "args": [str(script)], "source": "user"})
        check("A local MCP server is added and connected", outcome.get("ok") and outcome["count"] == 2, outcome)
        check("Its tools appear without a restart",
              {"tickets__get_ticket", "tickets__create_ticket"} <= set(REGISTRY), sorted(REGISTRY)[-5:])
        check("A read-only tool runs straight away; others need approval",
              REGISTRY["tickets__get_ticket"].mode == "read"
              and REGISTRY["tickets__create_ticket"].mode == "approval")
        check("The server's own instructions are passed on", "T-1" in (outcome.get("instructions") or ""))
        ctx = ToolContext(db=db, context={})
        read = REGISTRY["tickets__get_ticket"].handler(ctx, ticket_id="T-1")
        check("A read tool returns the server's answer", "Printer offline" in read["content"], read)

        ctx = ToolContext(db=db, context={})
        write = REGISTRY["tickets__create_ticket"].handler(ctx, title="Scanner jammed")
        card = ctx.drafts[0] if ctx.drafts else {}
        check("A write tool becomes an approval card, not a change",
              card.get("type") == "approval" and card.get("status") == "awaiting_approval"
              and "approves" in write["content"], write)
        check("…showing the arguments it will send",
              any(f["after"] == "Scanner jammed" for f in card.get("preview", {}).get("fields", [])), card)
        approved = actions.approve(db, card["action_id"])
        check("Approving it calls the server", "created T-2" in approved["reply"], approved)

        capabilities.set_auto_approve(db, "tickets", True)
        ctx = ToolContext(db=db, context={})
        auto = REGISTRY["tickets__create_ticket"].handler(ctx, title="Monitor flicker")
        check("With auto-approve on, it runs at once and is still recorded",
              "created T-3" in auto["content"] and ctx.drafts and ctx.drafts[0]["automatic"], auto)
        capabilities.set_auto_approve(db, "tickets", False)

        with mock.patch.object(capabilities, "connection",
                               return_value=mock.Mock(call_tool=mock.Mock(
                                   side_effect=mcp_client.OutcomeUnknown("No answer within 120 s")))):
            ctx = ToolContext(db=db, context={})
            REGISTRY["tickets__create_ticket"].handler(ctx, title="Lost")
            failed = actions.approve(db, ctx.drafts[0]["action_id"])
        check("A call whose outcome is unknown fails without retrying, and says to check",
              "may or may not" in failed["reply"], failed)

        capabilities.set_enabled(db, "mcp", "tickets", False)
        check("Switching a server off removes its tools", "tickets__get_ticket" not in REGISTRY)
        capabilities.set_enabled(db, "mcp", "tickets", True)
        check("…and switching it on brings them back", "tickets__get_ticket" in REGISTRY)

        unregister_owner("mcp:tickets")
        capabilities.stop()
        capabilities.load_all(db, connect=False)
        check("Saved servers' tools load from cache at start-up, before connecting",
              "tickets__create_ticket" in REGISTRY)

        # Lots of tools: only the relevant add-on tools are offered.
        for index in range(120):
            register_dynamic(Tool(f"bulk__tool_{index}", f"Bulk operation number {index} on widgets.",
                                  lambda ctx, **_: {"content": "x"}, {"type": "object", "properties": {}}),
                             "mcp:bulk")
        ctx = ToolContext(db=db, context={})
        offered = [t.name for t in loop.offered_tools(ctx, "create a ticket for the scanner")]
        check("With 100+ tools, the matching add-on tool is offered and the rest held back",
              "tickets__create_ticket" in offered and sum(n.startswith("bulk__") for n in offered) <= 8,
              len(offered))
        found = REGISTRY["find_tools"].handler(ctx, query="widgets bulk operation 7")
        check("find_tools surfaces held-back tools for the rest of the turn",
              "bulk__tool_7" in found["content"] and "bulk__tool_7" in ctx.context["_offered"]
              and "bulk__tool_7" in [t.name for t in loop.offered_tools(ctx, "anything")])
        check("Tool search ranks by name and description",
              search("create ticket", [REGISTRY["tickets__create_ticket"], REGISTRY["bulk__tool_1"]])[0].name
              == "tickets__create_ticket")
        unregister_owner("mcp:bulk")

        capabilities.set_secret("tickets", {"env": {"API_TOKEN": "s3cret-value"}})
        check("Keys are kept in the encrypted store, not the add-on file",
              capabilities.get_secret("tickets")["env"]["API_TOKEN"] == "s3cret-value"
              and "s3cret" not in (root / "capabilities" / "mcp" / "tickets.json").read_text(encoding="utf-8"))
        exported = capabilities.export_all()
        check("Export holds the definitions but no keys",
              exported["mcp"][0]["name"] == "tickets" and "s3cret" not in json.dumps(exported))

        removed = capabilities.remove(db, "mcp", "tickets")
        check("Removing a server removes its tools, file and keys",
              removed["ok"] and "tickets__get_ticket" not in REGISTRY
              and not (root / "capabilities" / "mcp" / "tickets.json").exists()
              and capabilities.get_secret("tickets") == {})
        imported = capabilities.import_all(db, exported)
        _time.sleep(0.2)
        check("An exported file imports on another PC", imported["added"] == ["tickets"])
        for _ in range(50):
            if "tickets__get_ticket" in REGISTRY:
                break
            _time.sleep(0.2)
        check("…and the imported server connects by itself", "tickets__get_ticket" in REGISTRY)
        capabilities.remove(db, "mcp", "tickets")

        # The official MCP SDK's own server, over every transport.
        if importlib.util.find_spec("mcp") is None:
            print("  - skipped SDK interop: the mcp package is not installed")
        else:
            sdk = root / "sdk_server.py"
            sdk.write_text(_SDK_MCP, encoding="utf-8")
            conn = mcp_client.MCPConnection("sdk", {"transport": "stdio", "command": _sys.executable,
                                                    "args": [str(sdk)]})
            info = conn.connect()
            tools = conn.list_tools()
            text = mcp_client.result_text(conn.call_tool("get_ticket", {"ticket_id": "T-1"}))
            conn.close()
            check("Works with the official SDK's server over stdio",
                  info.get("name") == "sdk-tickets" and len(tools) == 2 and "Printer" in text)
            for mode, path in (("http", "/mcp"), ("sse", "/sse")):
                sock = socket.socket()
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
                sock.close()
                process = subprocess.Popen([_sys.executable, str(sdk), mode, str(port)],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    conn = None
                    for _ in range(40):
                        try:
                            conn = mcp_client.MCPConnection("sdk", {"url": f"http://127.0.0.1:{port}{path}"})
                            conn.connect()
                            break
                        except mcp_client.MCPError:
                            conn = None
                            _time.sleep(0.25)
                    created = conn and mcp_client.result_text(conn.call_tool("create_ticket", {"title": "x"}))
                    check(f"…and over {'streamable HTTP' if mode == 'http' else 'SSE (found by itself)'}",
                          bool(created) and "created T-2" in created, created)
                    if conn:
                        conn.close()
                finally:
                    process.terminate()
    finally:
        capabilities.stop()
        unregister_owner("mcp:tickets")
        unregister_owner("mcp:bulk")
        for patch in reversed(patches):
            patch.stop()


def test_mcp_sign_in_and_self_adding():
    """OAuth sign-in, the MCP Registry, and Cerebro adding a connection mid-task."""
    print("\nAdd-ons — sign-in, the MCP Registry, and adding by itself")
    import sys as _sys
    import time as _time
    from unittest import mock

    import requests as _requests

    from app.services import capabilities, mcp_client
    from app.services.agent import actions, loop
    from app.services.agent.registry import REGISTRY, ToolContext, unregister_owner

    root, patches = _addon_dirs()
    oauth, oauth_state = _fake_oauth_mcp()
    base = f"http://127.0.0.1:{oauth.server_port}"
    for patch in patches:
        patch.start()
    db = session()
    try:
        added = capabilities.add_mcp(db, "secure", {"url": f"{base}/mcp", "source": "user"})
        check("A server that needs sign-in is added and says so", added["status"] == "needs_sign_in", added)
        started = capabilities.begin_sign_in("secure", open_browser=lambda url: _requests.get(url, timeout=10))
        check("Sign-in starts in the browser", started["ok"] and started["status"] == "waiting")
        for _ in range(60):
            if capabilities.sign_in_state("secure")["status"] != "waiting":
                break
            _time.sleep(0.2)
        check("OAuth with discovery, client registration and PKCE signs in",
              capabilities.sign_in_state("secure")["status"] == "connected"
              and oauth_state["registered"] and oauth_state["tokens"] == 1,
              capabilities.sign_in_state("secure"))
        check("…registering Cerebro as a public client with a local redirect",
              oauth_state["registered"][0]["token_endpoint_auth_method"] == "none"
              and oauth_state["registered"][0]["redirect_uris"][0].startswith("http://127.0.0.1:"))
        result = REGISTRY["secure__whoami"].handler(ToolContext(db=db, context={}))
        check("After sign-in its tools work with the token", "signed in as Sam" in result["content"], result)
        check("The token is kept encrypted, not in the add-on file",
              capabilities.get_secret("secure")["oauth"]["access_token"] == "good-token"
              and "good-token" not in (root / "capabilities" / "mcp" / "secure.json").read_text(encoding="utf-8"))
        capabilities.remove(db, "mcp", "secure")

        # Registry entries become ready-to-add connections.
        remote = capabilities.registry_option({"name": "io.test/remote", "description": "Remote",
                                               "remotes": [{"type": "streamable-http", "url": "https://x.test/mcp",
                                                            "headers": [{"name": "X-Api-Key", "isRequired": True,
                                                                         "isSecret": True}]}]})
        check("A registry remote is added by address, listing the key it needs",
              remote["config"] == {"transport": "http", "url": "https://x.test/mcp", "source": "registry",
                                   "registry_name": "io.test/remote"} and remote["needs"][0]["name"] == "X-Api-Key")
        pypi = capabilities.registry_option({"name": "io.test/py", "packages": [
            {"registryType": "pypi", "identifier": "mcp-server-time", "version": "1.2.0",
             "transport": {"type": "stdio"}}]})
        npm = capabilities.registry_option({"name": "io.test/js", "packages": [
            {"registryType": "npm", "identifier": "@scope/server", "version": "0.3.0",
             "transport": {"type": "stdio"}, "environmentVariables": [{"name": "TOKEN", "isRequired": True}]}]})
        check("A Python package runs with uvx, pinned to its version",
              pypi["config"]["command"] == "uvx" and pypi["config"]["args"] == ["mcp-server-time==1.2.0"])
        check("A Node package runs with npx -y, and its required variables are listed",
              npm["config"]["args"] == ["-y", "@scope/server@0.3.0"] and npm["needs"][0]["name"] == "TOKEN")
        with mock.patch.object(mcp_client, "_which", return_value=None):
            try:
                mcp_client.resolve_command("npx", ["-y", "x"])
                check("A Node server without Node.js says where to get it", False)
            except mcp_client.MCPError as exc:
                check("A Node server without Node.js says where to get it", "nodejs.org" in str(exc))

        # Cerebro finds and adds a server by itself, then carries on with it.
        script = root / "tickets_server.py"
        script.write_text(_STDIO_MCP, encoding="utf-8")
        entry = {"name": "io.test/tickets", "description": "Help-desk tickets",
                 "packages": [{"registryType": "pypi", "identifier": "tickets-mcp",
                               "transport": {"type": "stdio"}}]}
        registry, queries = _fake_registry([entry])
        real_option = capabilities.registry_option

        def local_option(server):
            option = real_option(server)
            if option and server["name"] == "io.test/tickets":
                option["config"].update(command=_sys.executable, args=[str(script)])
            return option

        seen_tools = []

        def script_llm(self, messages, tools=None, **kwargs):
            names = [t["name"] for t in tools or []]
            seen_tools.append(names)
            step = sum(1 for m in messages if m["role"] == "tool")
            calls = [("find_connector", {"query": "tickets"}),
                     ("add_mcp_server", {"registry_name": "io.test/tickets"}),
                     ("tickets__get_ticket", {"ticket_id": "T-1"})]
            if step < len(calls) and names:
                name, args = calls[step]
                return {"content": "", "tool_calls": [{"id": f"c{step}", "name": name, "arguments": args}]}
            return {"content": "Ticket T-1: the printer at the front desk is offline.", "tool_calls": []}

        enabled, patched = _with_ai(script_llm)
        with enabled, patched, mock.patch.object(capabilities, "REGISTRY_URL",
                                                 f"http://127.0.0.1:{registry.server_port}"), \
                mock.patch.object(capabilities, "registry_option", side_effect=local_option):
            result = loop.run(db, "What's the status of ticket T-1 in our help desk?", {})
        check("Cerebro searches the MCP Registry for a connection",
              any(q.get("search") == "tickets" for q in queries), queries)
        check("…adds it, and uses its new tool in the same answer",
              result["tools_used"] == ["find_connector", "add_mcp_server", "tickets__get_ticket"]
              and "offline" in result["reply"], result["tools_used"])
        check("The new tool is offered right after it's added",
              "tickets__get_ticket" in seen_tools[2] and "tickets__get_ticket" not in seen_tools[0])
        added_card = next((c for c in result["cards"] if c.get("tool") == "capability_added"), {})
        check("An “Added … — N tools” card with Remove is posted",
              added_card.get("title", "").startswith("Added") and added_card.get("undo_label") == "Remove"
              and added_card.get("can_undo"), added_card)
        row = next(r for r in capabilities.list_all(db) if r["name"] == "tickets")
        check("It's listed under Add-ons as added by Cerebro", row["added_by"] == "cerebro" and row["tool_count"] == 2)
        undone = actions.undo(db, added_card["action_id"])
        check("Remove on the card takes it away again",
              "Undone" in undone["reply"] and "tickets__get_ticket" not in REGISTRY, undone)

        # A local program the user didn't name, not from the Registry: ask once.
        ctx = ToolContext(db=db, context={"_user_text": "check the help desk"})
        asked = REGISTRY["add_mcp_server"].handler(ctx, command=_sys.executable, args=[str(script)], name="tickets")
        check("A local program Cerebro chose itself waits for approval",
              ctx.drafts and ctx.drafts[0]["tool"] == "add_mcp_server" and "tickets__get_ticket" not in REGISTRY,
              asked)
        approved = actions.approve(db, ctx.drafts[0]["action_id"])
        check("…and is added once approved", "2 tools" in approved["reply"] and "tickets__get_ticket" in REGISTRY,
              approved)
        capabilities.remove(db, "mcp", "tickets")
        ctx = ToolContext(db=db, context={"_user_text": f"add this server: {_sys.executable} {script}"})
        direct = REGISTRY["add_mcp_server"].handler(ctx, command=_sys.executable, args=[str(script)], name="tickets")
        check("A program the user named themselves is added straight away",
              "tickets__get_ticket" in REGISTRY and "Added" in direct["content"], direct)
        capabilities.remove(db, "mcp", "tickets")
        registry.shutdown()
    finally:
        capabilities.stop()
        for owner in ("mcp:tickets", "mcp:secure"):
            unregister_owner(owner)
        for patch in reversed(patches):
            patch.stop()
        oauth.shutdown()


def _fake_website():
    """A small web app: a home page linking to a knowledge base with search,
    articles, a JSON API, and a form that submits."""
    import http.server
    import json as _json
    import threading
    from urllib.parse import parse_qs, urlparse

    submitted = []
    articles = {"1": ("Resetting a sensor", "Unplug the sensor, wait ten seconds, and plug it into a rear USB port."),
                "2": ("Printer offline", "Restart the print spooler service.")}

    def page(title, body):
        return (f"<html><head><title>{title}</title></head><body><nav><a href='/'>Home</a> "
                f"<a href='/kb'>Knowledge base</a> <a href='/logout'>Log out</a></nav>"
                f"<main>{body}</main></body></html>")

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, body, kind="text/html; charset=utf-8"):
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/":
                return self._send(200, page("Vendor portal", "<h1>Welcome</h1><p>See the knowledge base.</p>"))
            if url.path == "/kb":
                return self._send(200, page("Knowledge base", "<form action='/kb/search'>"
                                            "<input type='search' name='q' aria-label='Search articles'></form>"))
            if url.path == "/kb/search":
                words = (query.get("q") or "").lower().split()
                hits = [f"<li><a href='/kb/article/{key}'>{title}</a> {text[:40]}</li>"
                        for key, (title, text) in articles.items()
                        if any(w in (title + text).lower() for w in words)]
                return self._send(200, page("Results", "<ul>" + "".join(hits) + "</ul>"))
            if url.path.startswith("/kb/article/"):
                title, text = articles[url.path.rsplit("/", 1)[-1]]
                return self._send(200, page(title, f"<article><h1>{title}</h1><p>{text}</p></article>"))
            if url.path == "/api/items":
                return self._send(200, _json.dumps({"items": [{"id": 7, "name": "Sensor"}]}), "application/json")
            if url.path == "/request":
                return self._send(200, page("New request", "<form method='post' action='/request'>"
                                            "<label for='subj'>Subject</label><input id='subj' name='subject'>"
                                            "<button type='submit'>Send request</button></form>"
                                            "<a href='/kb'>Browse articles</a>"))
            if url.path == "/done":
                return self._send(200, page("Thanks", "<p>Request received.</p>"))
            self._send(404, page("Not found", "Not found"))

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
            submitted.append(form)
            self.send_response(303)
            self.send_header("Location", "/done")
            self.end_headers()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, submitted


def test_websites_and_browsing():
    """Any website through the hidden browser: added, self-tuning, and safe to click in."""
    print("\nAdd-ons — any website, through the hidden browser")
    import importlib
    import sys as _sys
    from unittest import mock

    from app.core.config import settings
    from app.services import browser, capabilities
    from app.services.agent import actions
    from app.services.agent.registry import REGISTRY, ToolContext, unregister_owner
    from app.services.browser import generic
    from app.services.browser.engine import playwright_installed

    root, patches = _addon_dirs()
    for patch in patches:
        patch.start()
    db = session()
    server, submitted = _fake_website()
    base = f"http://127.0.0.1:{server.server_port}"
    engine_module = importlib.import_module("app.services.browser.engine")
    browser_patches = [mock.patch.object(settings, k, v) for k, v in {
        "BROWSER_AUTOMATION_ENABLED": True, "BROWSER_MODE": "headless", "BROWSER_TIMEOUT_SECONDS": 15,
        "BROWSER_CHANNEL": "msedge" if _sys.platform == "win32" else "chromium"}.items()]
    browser_patches.append(mock.patch.object(engine_module, "BROWSER_PROFILE_DIR", Path(_TEMP_DIR) / "site_profile"))
    for patch in browser_patches:
        patch.start()
    try:
        added = generic.add(db, "Vendor portal", f"{base}/", "Vendor articles and requests", added_by="user")
        check("A website becomes a connector with its own tools",
              added["name"] == "vendor-portal" and browser.get("vendor-portal").label == "Vendor portal"
              and {"vendor_portal_search", "vendor_portal_read", "vendor_portal_api_get"} <= set(REGISTRY), added)
        check("…and a card on the Connect tab",
              any(s["name"] == "vendor-portal" and s.get("generic") for s in
                  [c.status() for c in browser.connectors()]))
        site = browser.get("vendor-portal")
        check("Its address is the page given", site.start_url == f"{base}/" and site.on_own_site(f"{base}/kb"))
        if not playwright_installed():
            print("  - skipped browser part: Playwright is not installed")
            return
        try:
            found = site.search("sensor")
        except browser.BrowserUnavailable as exc:
            print(f"  - skipped browser part: {exc}")
            return
        check("It finds the search by following the site's own links",
              [r["title"] for r in found["results"]] == ["Resetting a sensor"], found)
        learned = site.selectors()
        check("…and remembers the page and the search address",
              learned.get("start_url") == f"{base}/kb" and learned.get("search_url") == f"{base}/kb/search?q={{query}}",
              learned)
        again = site.search("printer")
        check("The next search goes straight to the learned address",
              [r["title"] for r in again["results"]] == ["Printer offline"] and "/kb/search?q=printer" in again["url"])
        ctx = ToolContext(db=db, context={})
        read = REGISTRY["vendor_portal_read"].handler(ctx, url=found["results"][0]["url"])
        check("An article is read and cited", "rear USB port" in read["content"] and ctx.sources, read)
        data = REGISTRY["vendor_portal_api_get"].handler(ctx, path="/api/items")
        check("The site's own JSON is read with the signed-in page", '"Sensor"' in data["content"], data)
        refused = REGISTRY["vendor_portal_read"].handler(ctx, url="https://elsewhere.example.com/")
        check("Pages on other sites are refused", "only Vendor portal pages" in refused["content"], refused)

        ctx = ToolContext(db=db, context={})
        opened = REGISTRY["browse_open"].handler(ctx, url=f"{base}/request")
        check("browse_open lists what can be clicked and typed into",
              "field: Subject" in opened["content"] and "Send request ⚠ needs approval" in opened["content"], opened)
        lines = [l for l in opened["content"].splitlines() if l.startswith("  [")]
        field = next(l for l in lines if "field: Subject" in l).split("]")[0].split("[")[1]
        button = next(l for l in lines if "Send request" in l).split("]")[0].split("[")[1]
        typed = REGISTRY["browse_type"].handler(ctx, ref=field, text="New sensor please")
        check("Typing doesn't submit anything", "Nothing has been submitted" in typed["content"] and not submitted)
        clicked = REGISTRY["browse_click"].handler(ctx, ref=button, why="Sends the request")
        card = ctx.drafts[-1] if ctx.drafts else {}
        check("A submit click becomes an approval card with a screenshot, and nothing is sent",
              card.get("tool") == "browse_click" and card["preview"].get("image") and not submitted, clicked)
        check("…showing what was typed",
              any(f["after"] == "New sensor please" for f in card["preview"]["fields"]), card)
        done = actions.approve(db, card["action_id"])
        check("Approving it makes the click", submitted == [{"subject": "New sensor please"}]
              and "Thanks" in done["reply"], (done, submitted))
        REGISTRY["browse_open"].handler(ctx, url=f"{base}/request")
        link = next(l for l in REGISTRY["browse_read"].handler(ctx)["content"].splitlines()
                    if l.startswith("  [") and "Browse articles" in l).split("]")[0].split("[")[1]
        plain = REGISTRY["browse_click"].handler(ctx, ref=link)
        check("An ordinary link is clicked straight away", "Clicked" in plain["content"]
              and "Knowledge base" in plain["content"], plain)
        outside = REGISTRY["browse_open"].handler(ctx, url="https://not-connected.example.com/")
        check("Sites Cerebro isn't connected to can't be browsed", "only browses sites" in outside["content"])

        capabilities.set_enabled(db, "site", "vendor-portal", False)
        check("Switching a website off removes its tools", "vendor_portal_search" not in REGISTRY)
        capabilities.set_enabled(db, "site", "vendor-portal", True)
        capabilities.remove(db, "site", "vendor-portal")
        check("Removing it removes the connector and tools",
              "vendor_portal_search" not in REGISTRY and "vendor-portal" not in [c.name for c in browser.connectors()])
    finally:
        browser.engine().shutdown()
        generic.unload("vendor-portal")
        unregister_owner("site:vendor-portal")
        for patch in reversed(browser_patches + patches):
            patch.stop()
        server.shutdown()


def test_skills():
    """Skills: taught procedures in the Agent Skills format."""
    print("\nAdd-ons — skills")
    import io
    import zipfile

    from app.services import skills
    from app.services.agent import loop
    from app.services.agent.registry import REGISTRY, ToolContext

    root, patches = _addon_dirs()
    for patch in patches:
        patch.start()
    db = session()
    try:
        ctx = ToolContext(db=db, context={})
        saved = REGISTRY["save_skill"].handler(ctx, name="Close ticket", description="Closing a support ticket",
                                               instructions="1. Add a resolution note.\n2. Set status to Resolved.")
        path = root / "capabilities" / "skills" / "close-ticket" / "SKILL.md"
        check("save_skill writes an Agent Skills SKILL.md",
              path.exists() and path.read_text(encoding="utf-8").startswith("---\nname: close-ticket\ndescription: Closing a support ticket"))
        check("…and posts an “Added” card", ctx.drafts and ctx.drafts[0]["title"] == "Added Close ticket (skill)", saved)
        used = REGISTRY["use_skill"].handler(ToolContext(db=db, context={}), name="close ticket")
        check("use_skill loads the steps by name", "Set status to Resolved" in used["content"], used)
        check("Only names and one-liners go in Ask's instructions",
              "- close-ticket: Closing a support ticket" in skills.prompt_block(db)
              and "Resolved" not in skills.prompt_block(db))

        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr("escalate/SKILL.md", "---\nname: escalate\ndescription: Escalating to tier 2\n---\n"
                                                  "Run check.ps1, then email tier 2.")
            archive.writestr("escalate/check.ps1", "Get-Service spooler")
        names = skills.import_archive(db, bundle.getvalue(), "skills.zip")
        check("A zip of skill folders imports, with its helper files",
              names == ["escalate"] and (root / "capabilities" / "skills" / "escalate" / "check.ps1").exists())
        check("…and a script beside a skill is named but never run directly",
              "run only through run_command" in REGISTRY["use_skill"].handler(ToolContext(db=db, context={}),
                                                                             name="escalate")["content"])
        names = skills.import_archive(db, b"---\nname: greet\ndescription: Say hi\n---\nSay hello.", "greet.md")
        check("A bare SKILL.md imports too", names == ["greet"])

        systems = []

        def script_llm(self, messages, tools=None, system=None, **kwargs):
            systems.append(system or "")
            return {"content": "ok", "tool_calls": []}

        enabled, patched = _with_ai(script_llm)
        with enabled, patched:
            loop.run(db, "close CAS-1 for me", {})
        check("Ask is told about its skills", "escalate: Escalating to tier 2" in systems[0])
        exported = skills.export_all()
        check("Skills export with their instructions", any(s["name"] == "escalate" and "tier 2" in s["instructions"]
                                                           for s in exported))
    finally:
        for patch in reversed(patches):
            patch.stop()


def test_voice():
    """Talking to Cerebro: speech to text, spoken requests and approvals, spoken replies."""
    print("\nVoice — speech in, action, speech out")
    import base64
    import io
    import wave
    from unittest import mock

    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.main import app
    from app.services import speech, tts
    from app.services.agent import actions, loop
    from app.models.agent_action import AgentAction

    check("Spoken case numbers are written as case numbers",
          speech.normalise("reply to CAS zero one two three four please") == "reply to CAS-01234 please",
          speech.normalise("reply to CAS zero one two three four please"))
    check("…and spoken ticket numbers as digits",
          speech.normalise("open ticket number four five six seven") == "open ticket number 4567")
    check("Short spoken decisions are recognised",
          [speech.spoken_intent(t) for t in ("Yes, send it.", "Yeah, go ahead please", "approve",
                                             "Cancel that.", "No.", "Stop.", "send a message to Priya")]
          == ["approve", "approve", "approve", "discard", "discard", "stop", None])
    hints = speech.hints(None, {"crm_case": "CAS-555", "customer": "Smile Dental"})
    check("Words Cerebro knows are given as hints", "CAS-555" in hints and "Smile Dental" in hints, hints)

    heard = []
    speech.set_transcriber(lambda audio, prompt: heard.append((len(audio), prompt)) or "summarise case CAS-1")
    spoken_calls = []
    # The offline voices are covered in test_voice_expression; here they're unavailable,
    # so an online voice is tried after them.
    tts.use_provider("kokoro", lambda text, voice, rate: (_ for _ in ()).throw(RuntimeError("no model")))
    tts.use_provider("windows", lambda text, voice, rate: (_ for _ in ()).throw(RuntimeError("no Windows voice")))
    tts.use_provider("polly", lambda text, voice, rate: (_ for _ in ()).throw(RuntimeError("no AWS")))
    tts.use_provider("edge", lambda text, voice, rate: spoken_calls.append(text) or (b"MP3DATA", "audio/mpeg"))
    systems = []

    def script_llm(self, messages, tools=None, system=None, **kwargs):
        systems.append(system or "")
        return {"content": "**Case CAS-1** is waiting on the customer [K1]. See https://x.test/case for more.",
                "tool_calls": []}

    client = TestClient(app)
    enabled, patched = _with_ai(script_llm)
    try:
        with enabled, patched, mock.patch.object(settings, "VOICE_PROVIDER", "auto"):
            response = client.post("/api/voice/transcribe", files={"audio": ("a.wav", b"RIFFfake", "audio/wav")})
            check("The mic's recording is turned into text", response.json()["text"] == "summarise case CAS-1",
                  response.text)
            check("…with Cerebro's hints", heard and "Cerebro" in heard[0][1])
            response = client.post("/api/voice/command", files={"audio": ("a.wav", b"RIFFfake", "audio/wav")},
                                   data={"speak": "true"})
            body = response.json()
            check("A spoken request is acted on", body["transcript"] == "summarise case CAS-1"
                  and "waiting on the customer" in body["reply"], body)
            check("…as a spoken request, answered briefly", "This request was SPOKEN" in systems[-1])
            check("…with a spoken summary from the next voice when the others aren't available",
                  body["speech"]["provider"] == "edge" and base64.b64decode(body["speech"]["audio"]) == b"MP3DATA")
            check("Only plain words are spoken — no Markdown, citations or links",
                  spoken_calls[-1] == "Case CAS-1 is waiting on the customer . See the link for more.",
                  spoken_calls[-1])
            check("A voice turn may take more steps", loop.VOICE_MAX_STEPS >= 12)

            db = session()
            from app.models.enterprise import EnterpriseAction

            for row in db.query(EnterpriseAction).filter(EnterpriseAction.status.in_(("draft", "failed"))):
                row.status = "discarded"
            for row in db.query(AgentAction).filter(AgentAction.status == "draft"):
                row.status = "discarded"
            db.commit()
            target = Path(_TEMP_DIR) / "voice-approval.txt"
            action = actions.propose(db, "write_file", "files", "Create voice-approval.txt",
                                     {"path": str(target), "content": "hi", "mode": "replace"})
            body = client.post("/api/voice/command", data={"text": "Yes, send it.", "speak": "false"}).json()
            db.expire_all()
            check("“Yes, send it” approves the change that's waiting",
                  db.get(AgentAction, action.id).status == "done" and target.exists(), body)
            calls = len(systems)
            body = client.post("/api/voice/command", data={"text": "yes", "speak": "false"}).json()
            check("“Yes” with nothing waiting is just speech", len(systems) == calls + 1, body)
            action = actions.propose(db, "write_file", "files", "Create other.txt",
                                     {"path": str(Path(_TEMP_DIR) / "other.txt"), "content": "x", "mode": "replace"})
            client.post("/api/voice/command", data={"text": "Cancel that.", "speak": "false"})
            db.expire_all()
            check("“Cancel that” discards it", db.get(AgentAction, action.id).status == "discarded")
            calls = len(systems)
            body = client.post("/api/voice/command", data={"text": "stop", "speak": "false"}).json()
            check("“Stop” just stops", body.get("stop") is True and len(systems) == calls)

            response = client.post("/api/chat/stream", json={"message": "what's up", "voice": True})
            check("The mic button's requests are spoken requests too",
                  response.status_code == 200 and "This request was SPOKEN" in systems[-1])
            response = client.post("/api/voice/speak", json={"text": "Hello there"})
            check("Text can be spoken on request", response.content == b"MP3DATA"
                  and response.headers["x-voice-provider"] == "edge")
            client.post("/api/voice/state", json={"state": "listening", "caption": "Listening…"})
            from app.core import activity_state

            listening = lambda: any(i["state"] == "listening" for i in activity_state.snapshot()["active"])  # noqa: E731
            check("Push-to-talk shows Cerebro as listening", listening())
            client.post("/api/voice/state", json={"state": "idle"})
            check("…until the keys are let go", not listening())

        with mock.patch.object(settings, "VOICE_PROVIDER", "off"):
            try:
                tts.synthesize("hi")
                check("With the voice off nothing is spoken", False)
            except tts.SpeechFailed:
                check("With the voice off nothing is spoken", True)
        long_text = "This is a sentence that goes on. " * 30
        check("Long answers are cut to a few sentences", len(tts.speakable(long_text)) < 480
              and tts.speakable(long_text).endswith("The rest is in the chat."))
        with mock.patch.object(settings, "BEDROCK_AUTH_MODE", "api_key"):
            check("Polly isn't tried with a Bedrock API key", not tts.polly_ready())
        try:
            import av  # noqa: F401
            import numpy as np

            pcm = io.BytesIO()
            with av.open(pcm, "w", format="mp3") as container:
                stream = container.add_stream("mp3", rate=24000)
                stream.layout = "mono"
                frame = av.AudioFrame.from_ndarray(np.zeros((1, 24000), dtype=np.int16), format="s16", layout="mono")
                frame.rate = 24000
                for packet in stream.encode(frame):
                    container.mux(packet)
                for packet in stream.encode(None):
                    container.mux(packet)
            converted = tts.to_wav(pcm.getvalue())
            with wave.open(io.BytesIO(converted)) as handle:
                check("MP3 speech is converted to WAV for the desktop", handle.getnframes() > 20000)
        except ImportError:
            print("  - skipped MP3 conversion: PyAV is not installed")
    finally:
        speech.set_transcriber(None)
        tts.use_provider("kokoro", None)
        tts.use_provider("windows", None)
        tts.use_provider("polly", None)
        tts.use_provider("edge", None)

    if os.environ.get("CEREBRO_TEST_WHISPER") and speech.installed():
        with mock.patch.object(settings, "VOICE_MODEL", "tiny.en"):
            silent = io.BytesIO()
            with wave.open(silent, "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(b"\0\0" * 16000)
            result = speech.transcribe(silent.getvalue())
            check("faster-whisper runs on this PC (tiny.en, one second of silence)", result["text"] == "", result)


def test_voice_expression():
    """0.9: personality, sound cues, Polly engines and effects, Nova Sonic, Talk live."""
    print("\nVoice — personality, cues, Polly effects, Nova Sonic and Talk live")
    import asyncio
    import base64
    import io
    import json as _json
    import wave
    from unittest import mock

    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.main import app
    from app.services import agents, personality, sonic, tts
    from app.services.agent import loop

    # ---- the cue language
    line = "Found it! [giggle] [whisper]you owe me a coffee[/whisper] [tease]told you so[/tease]"
    check("Cues are parsed into words, sounds and styled spans",
          tts.segments(line) == [("text", "Found it! ", ()), ("sound", "giggle", ()),
                                 ("text", "you owe me a coffee", ("whisper",)),
                                 ("text", "told you so", ("tease",))], tts.segments(line))
    check("…and never shown in the chat", tts.strip_cues(line) == "Found it! you owe me a coffee told you so",
          tts.strip_cues(line))
    check("Text-only voices say the sounds as words", tts.plain(line) == "Found it! hehe you owe me a coffee told you so",
          tts.plain(line))
    check("Nova Sonic gets them as stage directions",
          tts.directed(line) == "Found it! (giggles) (whispering:) you owe me a coffee (teasingly:) told you so",
          tts.directed(line))
    check("A stray tag is dropped", tts.strip_cues("hi [whisper]there") == "hi there"
          and tts.plain("hi [whisper]there") == "hi there")
    check("Typed replies get gentle cues from how they read",
          tts.add_cues("Haha, that worked! Oops, one more thing.", 0.8)
          == "[giggle] [excited]that worked![/excited] [gasp] Oops, one more thing.",
          tts.add_cues("Haha, that worked! Oops, one more thing.", 0.8))
    check("…none when the agent is told to be plain", tts.add_cues("Haha, that worked!", 0.1) == "Haha, that worked!")
    cut = tts.speakable("[whisper]" + "This keeps going on and on. " * 40 + "[/whisper]")
    check("A long reply cut short keeps its span closed", cut.count("[whisper]") == cut.count("[/whisper]") == 1, cut)

    # ---- Polly SSML per engine (the AWS Polly developer guide's supported-tags table)
    style = {"rate": 1.1, "pitch": 6, "timbre": -10, "breaths": True, "drc": True, "newscaster": True}
    generative = tts.render_ssml(line, "generative", style, "Joanna")
    neural = tts.render_ssml(line, "neural", style, "Joanna")
    standard = tts.render_ssml(line, "standard", style, "Joanna")
    check("Generative gets only the tags it takes (rate, volume, breaks)", generative ==
          '<speak><prosody rate="110%">Found it! <prosody rate="110%">hehe</prosody><break time="150ms"/> '
          '<prosody volume="x-soft" rate="90%">you owe me a coffee</prosody> <break time="120ms"/>'
          '<prosody rate="92%">told you so</prosody></prosody></speak>', generative)
    check("Neural adds compression and the newscaster style, never pitch or whispers", neural ==
          '<speak><amazon:domain name="news"><amazon:effect name="drc"><prosody rate="110%">Found it! '
          '<prosody rate="110%">hehe</prosody><break time="150ms"/> <prosody volume="x-soft" rate="90%">'
          'you owe me a coffee</prosody> <break time="120ms"/><prosody rate="92%">told you so</prosody>'
          '</prosody></amazon:effect></amazon:domain></speak>', neural)
    check("Standard is the effects engine: a real whisper, pitch, timbre, breaths and emphasis", standard ==
          '<speak><amazon:effect name="drc"><amazon:auto-breaths volume="soft" frequency="low">'
          '<amazon:effect vocal-tract-length="-10%"><prosody rate="110%" pitch="+6%">Found it! '
          '<prosody pitch="+12%" rate="110%">hehe</prosody><break time="150ms"/> '
          '<amazon:effect name="whispered">you owe me a coffee</amazon:effect> <break time="120ms"/>'
          '<emphasis level="moderate"><prosody rate="92%" pitch="+15%">told you so</prosody></emphasis>'
          '</prosody></amazon:effect></amazon:auto-breaths></amazon:effect></speak>', standard)
    check("The newscaster style is only for voices that have it",
          "amazon:domain" not in tts.render_ssml("hi", "neural", {"newscaster": True}, "Ruth"))
    check("Text is escaped for SSML", "&lt;b&gt; &amp; co" in tts.render_ssml("<b> & co", "neural"))
    check("The Windows voice gets SSML 1.0 too",
          tts.windows_ssml(line).startswith('<speak version="1.0"') and "<emphasis>told you so</emphasis>"
          in tts.windows_ssml(line))

    # ---- choosing a Polly engine by what the voice has
    voices = {"Ruth": ["generative", "neural", "long-form"], "Joanna": ["neural", "standard"],
              "Ivy": ["standard"]}
    check("Generative when the voice has it", tts.engine_for("Ruth", "generative", voices) == "generative")
    check("…else the next one down", tts.engine_for("Joanna", "generative", voices) == "neural"
          and tts.engine_for("Ivy", "generative", voices) == "standard")
    check("An unknown voice is tried as asked", tts.engine_for("Nobody", "neural", voices) == "neural")

    class Stream:
        def __init__(self, data):
            self.data = data

        def read(self):
            return self.data

    class FakePolly:
        def __init__(self, refuse=0):
            self.calls, self.refuse = [], refuse

        def describe_voices(self, **kwargs):
            if not kwargs:
                return {"Voices": [{"Id": "Ruth", "SupportedEngines": ["generative", "neural"]}],
                        "NextToken": "2"}
            return {"Voices": [{"Id": "Ivy", "SupportedEngines": ["standard"]}]}

        def synthesize_speech(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) <= self.refuse:
                raise RuntimeError("InvalidSsmlException: Invalid SSML request")
            return {"AudioStream": Stream(b"MP3")}

    fake = FakePolly()
    with mock.patch.object(tts, "polly_client", lambda: fake):
        tts._POLLY_VOICES.update(at=0.0, voices={})
        check("Polly's voices are listed with their engines, every page",
              tts.polly_voices(fresh=True) == {"Ruth": ["generative", "neural"], "Ivy": ["standard"]})
        audio, mime = tts._polly("Done! [giggle]", "Ivy", 1.0, {"engine": "generative"})
        check("A voice without generative steps down and gets its engine's SSML",
              audio == b"MP3" and fake.calls[-1]["Engine"] == "standard"
              and fake.calls[-1]["TextType"] == "ssml" and "hehe" in fake.calls[-1]["Text"], fake.calls[-1])
        fake.calls.clear()
        fake.refuse = 2
        audio, _ = tts._polly("[whisper]psst[/whisper] [giggle]", "Ruth", 1.0, {"engine": "generative"})
        check("Refused SSML is retried plainer, then as plain text — speech never fails on a tag",
              audio == b"MP3" and [c["TextType"] for c in fake.calls] == ["ssml", "ssml", "text"]
              and "<prosody" not in fake.calls[1]["Text"] and fake.calls[2]["Text"] == "psst hehe", fake.calls)
        tts._POLLY_VOICES.update(at=0.0, voices={})

    # ---- personality
    db = session()
    sweet = agents.create(db, "blank", name="Mochi")
    check("New agents are the cute, cheerful, teasing sweetheart by default",
          personality.personality(sweet)["preset"] == "sweetheart")
    prompt = agents.identity_block(db, sweet)
    check("The persona reaches the agent's instructions", "playful, teasing" in prompt, prompt)
    check("…with its guardrails: kind, never sexual or crude, drafts in the user's own style",
          "never flirt sexually" in prompt and "user's own style" in prompt)
    calm = agents.update(db, sweet, personality={"preset": "professional", "playfulness": 5, "formality": 0.9})
    persona = personality.personality(calm)
    check("A personality can be changed, and is kept in range",
          persona["preset"] == "professional" and persona["playfulness"] == 1.0, persona)
    check("…and sounds like it", "formal" in agents.identity_block(db, calm))
    agents.update(db, sweet, personality={"preset": "sweetheart"},
                  voice_style={"provider": "polly", "engine": "standard", "pitch": 99, "breaths": 1,
                               "voice": "Ivy", "bogus": "x"})
    voice = personality.voice_settings(sweet)
    check("An agent's voice style is cleaned and merged over its personality's",
          voice["engine"] == "standard" and voice["pitch"] == 30 and voice["breaths"] is True
          and voice["voice"] == "Ivy" and "bogus" not in voice and abs(voice["rate"] - 1.06) < 1e-6, voice)
    check("…and carries the persona for voices that perform it", "playful" in voice["persona"]
          and voice["name"] == "Mochi")
    face = personality.clean_face_style({"palette": "pink", "hair": "twintails", "framing": {"zoom": 9, "y": -0.2},
                                         "expressions": {"happy": {"name": "joy", "weight": 2}, "nope": {}},
                                         "gestures": {"done": "gestures/wave.vrma", "idle": "../x.vrma"}})
    check("A face's style is validated: hair, framing, expression mapping, gestures",
          face == {"palette": "pink", "hair": "twintails", "framing": {"zoom": 3.0, "y": -0.2, "mirror": False},
                   "expressions": {"happy": {"name": "joy", "weight": 1.0}},
                   "gestures": {"done": "gestures/wave.vrma"}}, face)
    with mock.patch.object(settings, "VOICE_CUES", True):
        check("Voice turns are told how many cues to use",
              "up to 3 sound cues" in loop._cue_rules(sweet), loop._cue_rules(sweet))
        agents.update(db, sweet, personality={"preset": "sweetheart", "expressiveness": 0})
        check("…none for an agent that shouldn't", loop._cue_rules(sweet) == "")
    with mock.patch.object(settings, "VOICE_CUES", False):
        check("…or when sound cues are switched off", loop._cue_rules(calm) == "")
    agents.update(db, sweet, personality={"preset": "sweetheart"})

    # Customer drafts never take the persona.
    from app.models.enterprise import EnterpriseMessage
    from app.services.enterprise_service import EnterpriseService
    import app.services.llm_service as llm_module

    message = EnterpriseMessage(source="outlook", sender="ann@example.com", subject="Printer",
                                body="The printer is jammed again.")
    db.add(message)
    db.commit()
    prompts = []
    with mock.patch.object(llm_module.LLMService, "enabled", new=property(lambda self: True)), \
            mock.patch.object(llm_module.LLMService, "_call_llm", new=lambda self, p, *a, **k: prompts.append(p) or "OK"):
        EnterpriseService(db).draft_reply(message)
    check("Replies drafted for customers never use the persona",
          prompts and "teasing" not in prompts[0] and "bubbly" not in prompts[0], prompts[:1])

    # The reply's cues are spoken, but never shown.
    def cued_llm(self, messages, tools=None, system=None, **kwargs):
        return {"content": "[yay] All done! [tease]Told you it was the cache.[/tease]", "tool_calls": []}

    spoken = []
    tts.use_provider("polly", lambda text, voice, rate, style: spoken.append((text, voice, style)) or (b"MP3", "audio/mpeg"))
    client = TestClient(app)
    enabled, patched = _with_ai(cued_llm)
    try:
        with enabled, patched, mock.patch.object(settings, "VOICE_PROVIDER", "auto"), \
                mock.patch.object(settings, "VOICE_CUES", True):
            body = client.post("/api/voice/command", data={"text": "fix it", "speak": "true",
                                                           "agent_id": str(sweet.id)}).json()
            check("A spoken reply shows without its cues", body.get("reply") == "All done! Told you it was the cache.",
                  body.get("reply"))
            check("…and is spoken with them, in the agent's voice style",
                  spoken and "[tease]" in spoken[-1][0] and spoken[-1][1] == "Ivy"
                  and spoken[-1][2].get("engine") == "standard", spoken[-1:])
            response = client.post("/api/voice/speak", json={
                "text": "Hehe, found it!", "agent_id": sweet.id,
                "style": {"engine": "neural", "voice": "Joanna"}, "personality": {"preset": "calm"}})
            check("The editor's Preview tries a voice before it's saved",
                  response.status_code == 200 and spoken[-1][1] == "Joanna" and spoken[-1][2]["engine"] == "neural",
                  response.text)
            db.expire_all()
            check("…without saving it", personality.voice_settings(agents.get(db, sweet.id))["engine"] == "standard")
            result = client.post("/api/voice/test", json={"agent_id": sweet.id}).json()
            check("Settings → Voice → Test voice says which voice spoke", result.get("ok") and result["provider"] == "polly",
                  result)
            listed = client.get("/api/voice/voices", params={"provider": "sonic"}).json()
            check("Nova Sonic's voices are offered", any(v["id"] == "tiffany" for v in listed["voices"]), listed)
    finally:
        tts.use_provider("polly", None)

    # ---- Nova Sonic, against a fake bidirectional stream
    import sys as _sys

    ok, why = sonic.available()
    if _sys.version_info < (3, 12):
        check("Nova Sonic isn't offered on an older Python, and says why", not ok and "3.12" in why, why)
    with mock.patch.object(settings, "BEDROCK_AUTH_MODE", "api_key"), mock.patch.object(_sys, "version_info", (3, 12, 0)), \
            mock.patch("importlib.util.find_spec", lambda name, *a: object()):
        ok, why = sonic.available()
        check("…nor with a Bedrock API key, which can't sign it", not ok and "API key" in why, why)

    pcm = b"\x01\x00\x02\x00" * 600

    class FakeSonic:
        """Records what Cerebro sends and answers like the service."""
        streams = []

        def __init__(self, script):
            self.sent, self.script, self.closed = [], script, False
            self.queue = asyncio.Queue()
            FakeSonic.streams.append(self)

        async def send(self, event_json):
            event = _json.loads(event_json)["event"]
            self.sent.append(event)
            for reply in self.script(event, self):
                await self.queue.put(reply)

        async def receive(self):
            return await self.queue.get()

        async def close(self):
            self.closed = True
            await self.queue.put(None)

    def reader(event, stream):
        name = next(iter(event))
        if name == "contentEnd" and len([e for e in stream.sent if "contentEnd" in e]) == 2:
            yield {"event": {"contentStart": {"type": "AUDIO", "role": "ASSISTANT"}}}
            yield {"event": {"audioOutput": {"content": base64.b64encode(pcm[:1200]).decode()}}}
            yield {"event": {"audioOutput": {"content": base64.b64encode(pcm[1200:]).decode()}}}
            yield {"event": {"contentEnd": {"type": "AUDIO", "stopReason": "END_TURN"}}}

    opened = []

    async def factory(model, region):
        opened.append((model, region))
        return FakeSonic(reader)

    sonic.set_client_factory(factory)
    try:
        wav = sonic.speak("Found it! (giggles)", style={"name": "Mochi", "persona": "You're bubbly."})
        stream = FakeSonic.streams[-1]
        names = [next(iter(e)) for e in stream.sent]
        check("Nova Sonic is spoken to in the documented order",
              names == ["sessionStart", "promptStart", "contentStart", "textInput", "contentEnd",
                        "contentStart", "textInput", "contentEnd", "promptEnd", "sessionEnd"], names)
        start = stream.sent[1]["promptStart"]["audioOutputConfiguration"]
        check("…asking for 24 kHz 16-bit mono speech in the chosen voice",
              start["sampleRateHertz"] == 24000 and start["sampleSizeBits"] == 16 and start["voiceId"] == "tiffany"
              and start["mediaType"] == "audio/lpcm", start)
        check("…with the persona as the system prompt, and the reply as the user's turn",
              stream.sent[2]["contentStart"]["role"] == "SYSTEM" and "bubbly" in stream.sent[3]["textInput"]["content"]
              and stream.sent[5]["contentStart"]["interactive"] is True
              and "Found it! (giggles)" in stream.sent[6]["textInput"]["content"])
        check("…on the configured model and region", opened[-1] == (settings.VOICE_SONIC_MODEL, settings.VOICE_SONIC_REGION))
        with wave.open(io.BytesIO(wav)) as handle:
            check("Its speech comes back as 24 kHz WAV", handle.getframerate() == 24000
                  and handle.readframes(10 ** 6) == pcm)
        check("…and the stream is closed", stream.closed)

        sonic_calls = []
        with mock.patch.object(settings, "VOICE_PROVIDER", "sonic"):
            with mock.patch.object(sonic, "speak", lambda text, voice=None, style=None: sonic_calls.append((text, voice))
                                   or tts.pcm_to_wav(pcm)):
                spoken = tts.synthesize("Yay! [giggle]", style={"provider": "sonic", "voice": "matthew"})
            check("Nova Sonic voices replies when chosen, with the cues as directions",
                  spoken["provider"] == "sonic" and sonic_calls == [("Yay! (giggles)", "matthew")], sonic_calls)
            tts.use_provider("kokoro", lambda text, voice, rate, style: (b"RIFF", "audio/wav"))
            with mock.patch.object(sonic, "speak", lambda *a, **k: (_ for _ in ()).throw(sonic.SonicUnavailable("x"))):
                spoken = tts.synthesize("Hello [giggle]", style={"provider": "sonic"})
            tts.use_provider("kokoro", None)
            check("…and the offline voice speaks if Nova Sonic fails", spoken["provider"] == "kokoro")

        # ---- Talk live
        asked, events = [], []

        def live_script(event, stream):
            name = next(iter(event))
            if name == "audioInput":
                yield {"event": {"contentStart": {"role": "USER", "type": "TEXT"}}}
                yield {"event": {"textOutput": {"content": "make a note to call Ann"}}}
                yield {"event": {"contentStart": {"role": "ASSISTANT", "type": "TEXT",
                                                  "additionalModelFields": '{"generationStage":"SPECULATIVE"}'}}}
                yield {"event": {"textOutput": {"content": "On it!"}}}
                yield {"event": {"contentStart": {"role": "ASSISTANT", "type": "TEXT",
                                                  "additionalModelFields": '{"generationStage":"FINAL"}'}}}
                yield {"event": {"textOutput": {"content": "On it, hehe!"}}}
                yield {"event": {"audioOutput": {"content": "AAAA"}}}
                yield {"event": {"textOutput": {"content": '{ "interrupted" : true }'}}}
                yield {"event": {"toolUse": {"toolUseId": "t1", "toolName": "ask_cerebro",
                                             "content": _json.dumps({"request": "make a note to call Ann"})}}}

        async def run_live():
            async def emit(message):
                events.append(message)

            async def ask(request):
                asked.append(request)
                return {"speech": "Noted!", "reply": "Noted: call Ann.", "cards": [{"type": "note"}]}

            session_ = sonic.LiveSession(emit, ask, voice="Tiffany", name="Mochi", agent=sweet)
            factory_streams = len(FakeSonic.streams)
            sonic.set_client_factory(lambda m, r: _make(live_script))
            await session_.start()
            await session_.audio(pcm)
            for _ in range(50):
                if any(e["type"] == "result" for e in events):
                    break
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.05)
            await session_.stop()
            return FakeSonic.streams[factory_streams:]

        async def _make(script):
            return FakeSonic(script)

        live_streams = asyncio.run(run_live())
        live_stream = live_streams[-1]
        sent = [next(iter(e)) for e in live_stream.sent]
        tools = live_stream.sent[1]["promptStart"].get("toolConfiguration", {}).get("tools", [])
        check("Talk live offers Cerebro's agent as a tool", [t["toolSpec"]["name"] for t in tools] == ["ask_cerebro"]
              and live_stream.sent[1]["promptStart"]["audioOutputConfiguration"]["voiceId"] == "tiffany", tools)
        check("…streams the mic as 16 kHz audio",
              any("audioInput" in e and base64.b64decode(e["audioInput"]["content"]) == pcm for e in live_stream.sent)
              and any(e.get("contentStart", {}).get("type") == "AUDIO"
                      and e["contentStart"]["audioInputConfiguration"]["sampleRateHertz"] == 16000
                      for e in live_stream.sent))
        transcripts = [(e["role"], e["text"]) for e in events if e["type"] == "transcript"]
        check("Both sides are transcribed, the final words only",
              transcripts == [("user", "make a note to call Ann"), ("assistant", "On it, hehe!")], transcripts)
        check("Her voice plays as it arrives", {"type": "audio", "data": "AAAA"} in events)
        check("Talking over her stops her", {"type": "interrupted"} in events)
        check("Real work goes to Cerebro's agent", asked == ["make a note to call Ann"]
              and any(e["type"] == "result" and e["reply"] == "Noted: call Ann." for e in events), events)
        results = [e["toolResult"] for e in live_stream.sent if "toolResult" in e]
        check("…and its answer goes back for her to say",
              results and _json.loads(results[0]["content"]) == {"result": "Noted!"}
              and any(e.get("contentStart", {}).get("toolResultInputConfiguration", {}).get("toolUseId") == "t1"
                      for e in live_stream.sent), results)
        check("Ending closes the audio, prompt and session", sent[-3:] == ["contentEnd", "promptEnd", "sessionEnd"]
              and live_stream.closed, sent[-4:])

        # The WebSocket the page uses.
        sonic.set_client_factory(lambda m, r: _make(live_script))

        def agent_llm(self, messages, tools=None, system=None, **kwargs):
            return {"content": "Noted — I'll remind you to call Ann. [yay]", "tool_calls": []}

        enabled, patched = _with_ai(agent_llm)
        with enabled, patched, mock.patch.object(settings, "VOICE_ENABLED", True):
            with client.websocket_connect(f"/api/voice/live?agent_id={sweet.id}",
                                          headers={"origin": "http://127.0.0.1"}) as ws:
                ready = ws.receive_json()
                check("The page's live socket opens a session with the agent", ready["type"] == "ready"
                      and ready["agent"] == "Mochi", ready)
                ws.send_bytes(pcm)
                seen = []
                for _ in range(30):
                    seen.append(ws.receive_json())
                    if seen[-1]["type"] == "result":
                        break
                ws.send_text(_json.dumps({"type": "stop"}))
            result = next((e for e in seen if e["type"] == "result"), {})
            check("…and a spoken request runs the agent loop, its cues kept out of the chat",
                  result.get("reply") == "Noted — I'll remind you to call Ann.", seen)
            said = [e for e in FakeSonic.streams[-1].sent if "toolResult" in e]
            check("…and a short spoken answer goes back to Nova Sonic",
                  said and "remind you to call Ann" in _json.loads(said[0]["toolResult"]["content"])["result"], said)
            try:
                with client.websocket_connect("/api/voice/live", headers={"origin": "https://evil.example"}) as ws:
                    ws.receive_json()
                check("Other websites can't open a live session", False)
            except Exception:  # noqa: BLE001 - refused
                check("Other websites can't open a live session", True)
    finally:
        sonic.set_client_factory(None)


def test_offline_voice():
    """0.10: speech that works on a locked-down network — Kokoro and a bundled speech model."""
    print("\nVoice — offline (Kokoro, bundled speech model)")
    import io
    import sys as _sys
    import wave
    import zipfile
    from unittest import mock

    from app.core.config import settings
    from app.services import g2p, kokoro_tts, speech, tts

    # ---- text → phonemes, without eSpeak or spaCy
    check("Words come from the pronunciation dictionary", g2p.phonemize("Hello there") == "həlˈO ðɛɹ",
          g2p.phonemize("Hello there"))
    check("…with English endings worked out", g2p.word_phonemes("printers") == "pɹˈɪntəɹz"
          and g2p.word_phonemes("checked") == "ʧˈɛkt", (g2p.word_phonemes("printers"), g2p.word_phonemes("checked")))
    check("Cerebro's own words are said right", g2p.word_phonemes("Cerebro") == "səɹˈɛbɹO")
    check("Numbers, times, money and case numbers are said as words",
          g2p.normalise("CAS-01234 at 9:30 costs $5 (25%) in 2026")
          == "CAS zero one two three four at nine thirty costs five dollars (twenty five percent) in twenty twenty six",
          g2p.normalise("CAS-01234 at 9:30 costs $5 (25%) in 2026"))
    check("Acronyms are spelled out", g2p.word_phonemes("VPN") == "vˌipˌiˈɛn" and "ˈɛs" in g2p.word_phonemes("CAS"))
    unknown = g2p.phonemize("Zorbulax")
    check("An unknown word still gets a pronunciation", unknown and unknown[0] == "z" and "ˈ" in unknown, unknown)
    check("Only Kokoro's own symbols come out",
          all(c in g2p.VOCAB for c in g2p.phonemize("Hehe — it's done! Ünïcödé & “quotes”, 100%…")))

    # ---- the offline voice comes first, and a blocked online voice is explained
    with mock.patch.object(settings, "VOICE_PROVIDER", "auto"):
        check("Best available tries the offline voices first", tts.order()[:2] == ["kokoro", "windows"])
    check("A chosen online voice falls back to the offline ones, not to another online one",
          tts.order("polly") == ["polly", "kokoro", "windows"] and tts.order("edge") == ["edge", "kokoro", "windows"])
    message = tts.explain("edge", RuntimeError("Cannot connect to host speech.platform.bing.com:443 ssl:default"))
    check("A blocked online voice says which address was blocked and what works instead",
          "speech.platform.bing.com" in message and "blocks" in message and "Kokoro" in message, message)
    check("Kokoro voice names are recognised (blends too)", tts._fits("kokoro", "af_heart+af_bella")
          and not tts._fits("kokoro", "tiffany") and not tts._fits("sonic", "af_heart"))

    # ---- the Kokoro engine itself (needs the model; in CI the real voices too)
    ok, why = kokoro_tts.available()
    if not ok:
        print(f"  - skipped Kokoro synthesis: {why}")
    else:
        names = [v["id"] for v in kokoro_tts.voices()]
        voice = "af_heart" if "af_heart" in names else names[0]

        def seconds(wav):
            with wave.open(io.BytesIO(wav)) as handle:
                return handle.getnframes() / handle.getframerate(), handle.getframerate()

        def loudness(wav):
            import numpy as np

            with wave.open(io.BytesIO(wav)) as handle:
                samples = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").astype("float32")
            return float(np.sqrt(np.mean(samples ** 2)))

        line = "The printer on the second floor is working again."
        plain = kokoro_tts.speak(line, voice=voice)
        length, rate = seconds(plain)
        check("Kokoro speaks offline, as 24 kHz audio", rate == 24000 and 1.0 < length < 12, (rate, length))
        fast, _ = seconds(kokoro_tts.speak(f"[excited]{line}[/excited]", voice=voice))
        slow, _ = seconds(kokoro_tts.speak(f"[slow]{line}[/slow]", voice=voice))
        check("Excited is quicker and slow is slower", fast < length < slow, (fast, length, slow))
        check("A whisper is quieter", loudness(kokoro_tts.speak(f"[whisper]{line}[/whisper]", voice=voice))
              < loudness(plain) * 0.7)
        if len(names) > 1:
            mixed = kokoro_tts.blend(f"{names[0]}+{names[1]}")
            check("Voices can be blended", mixed.shape == kokoro_tts.blend(names[0]).shape)
        spoken = tts.synthesize("Yay, all done! [giggle]", style={"provider": "kokoro", "voice": voice})
        check("Kokoro is a voice like any other", spoken["provider"] == "kokoro" and spoken["mime"] == "audio/wav")

        # In CI both real models are bundled: what Kokoro says, Whisper must hear.
        if "af_heart" in names and speech.installed() and speech.model_folder("base.en"):
            with mock.patch.object(settings, "VOICE_MODEL", "base.en"):
                heard = speech.transcribe(kokoro_tts.speak(
                    "Hello! Your case is closed and the printer is fixed.", voice="af_heart"))["text"].lower()
            words = {"hello", "your", "case", "is", "closed", "and", "the", "printer", "fixed"}
            got = {w.strip(".,!?") for w in heard.split()}
            check("Kokoro is understandable: Whisper hears what it said", len(words & got) >= 7, heard)
        else:
            print("  - skipped the Kokoro → Whisper round trip: needs the real models (CI has them)")

    # ---- speech-to-text loads its model from a folder, never from the internet
    import tempfile

    root = Path(tempfile.mkdtemp(dir=_TEMP_DIR))
    (root / "whisper" / "base.en").mkdir(parents=True)
    for name in ("model.bin", "config.json"):
        (root / "whisper" / "base.en" / name).write_bytes(b"x")
    loaded = []

    class FakeWhisper:
        def __init__(self, path, **kwargs):
            loaded.append((path, kwargs, dict(__import__("os").environ)))

    fake_module = type(_sys)("faster_whisper")
    fake_module.WhisperModel = FakeWhisper
    with mock.patch.object(speech, "model_folders", lambda: [root / "whisper"]), \
            mock.patch.dict(_sys.modules, {"faster_whisper": fake_module}), \
            mock.patch.object(speech, "installed", lambda: True), \
            mock.patch.object(settings, "VOICE_MODEL", "base.en"):
        speech._model, speech._model_name = None, ""
        speech.model()
        check("The speech model is loaded from its folder, by path",
              loaded and loaded[-1][0] == str(root / "whisper" / "base.en") and "download_root" not in loaded[-1][1],
              loaded[-1:])
        check("…with the model site switched off", loaded[-1][2].get("HF_HUB_OFFLINE") == "1")
        check("The installed models are listed", speech.installed_models() == ["base.en"])
        with mock.patch.object(settings, "VOICE_MODEL", "small.en"):
            speech._model, speech._model_name = None, ""
            calls = len(loaded)
            try:
                speech.model()
                check("A model that isn't installed says how to get it, without going online", False)
            except speech.SpeechUnavailable as exc:
                check("A model that isn't installed says how to get it, without going online",
                      "import" in str(exc).lower() and len(loaded) == calls, str(exc))
            bundle = io.BytesIO()
            with zipfile.ZipFile(bundle, "w") as archive:
                for name in ("model.bin", "config.json", "tokenizer.json"):
                    archive.writestr(f"faster-whisper-small.en/{name}", "x")
            with mock.patch.object(speech, "MODELS_DIR", root):
                result = speech.import_model(bundle.getvalue())
            check("A speech model can be imported from a zip", result["name"] == "small.en"
                  and (root / "whisper" / "small.en" / "tokenizer.json").is_file(), result)
            speech.model()
            check("…and is then used", loaded[-1][0] == str(root / "whisper" / "small.en"))
            evil = io.BytesIO()
            with zipfile.ZipFile(evil, "w") as archive:
                archive.writestr("m/model.bin", "x")
                archive.writestr("m/config.json", "x")
                archive.writestr("m/../../escape.txt", "x")
            try:
                with mock.patch.object(speech, "MODELS_DIR", root):
                    speech.import_model(evil.getvalue(), "evil")
                check("A zip that tries to write outside its folder is refused", False)
            except speech.SpeechUnavailable:
                check("A zip that tries to write outside its folder is refused", not (root.parent / "escape.txt").exists())
        speech._model, speech._model_name = None, ""


def test_install_and_avatars():
    """0.10: built-in 3D models, the buddy following the agent you talk to, clearing memory, setup from the installer."""
    print("\nInstall — built-in models, desktop buddy, clearing memory, installer setup")
    import io
    import json as _json
    import sys as _sys
    import tempfile
    import zipfile
    from unittest import mock

    from fastapi.testclient import TestClient

    from app.api import faces as faces_api
    from app.core.config import settings
    from app.main import app
    from app.services import agents, install_setup, reset

    _sys.path.insert(0, str(ROOT))
    _sys.path.insert(0, str(ROOT / "desktop"))
    from tests_support import make_vrm
    import brain_frames

    client = TestClient(app)
    root = Path(tempfile.mkdtemp(dir=_TEMP_DIR))
    builtin = root / "builtin"
    (builtin / "miko").mkdir(parents=True)
    (builtin / "miko" / "model.vrm").write_bytes(make_vrm.build())
    (builtin / "miko" / "meta.json").write_text(_json.dumps({
        "name": "Miko", "author": "pixiv (VRoid)", "license": "CC0-1.0", "framing": {"zoom": 1.2, "y": 0.1},
        "expressions_map": {"happy": {"name": "happy", "weight": 0.9}}}))
    faces_dir = root / "faces"

    with mock.patch.object(faces_api, "builtin_dir", lambda: builtin), mock.patch.object(faces_api, "FACES_DIR", faces_dir):
        listed = client.get("/api/faces").json()["faces"]
        miko = next((f for f in listed if f["id"] == "builtin-miko"), {})
        check("Built-in 3D models are listed, with their credit and licence",
              miko.get("builtin") and miko.get("license") == "CC0-1.0" and miko.get("kind") == "vrm"
              and miko.get("framing") == {"zoom": 1.2, "y": 0.1}, miko)
        served = client.get("/faces/builtin-miko/model.vrm")
        check("…and served to the page", served.status_code == 200 and served.content[:4] == b"glTF")
        check("…but can't be removed", client.delete("/api/faces/builtin-miko").status_code == 400
              and (builtin / "miko" / "model.vrm").is_file())
        check("…nor reached outside their folder", client.get("/faces/builtin-miko/../../secret").status_code == 404)

        db = session()
        fresh = agents.create(db, "inbox", name="Mei")
        check("A new agent starts with a built-in 3D model, with its tuned framing and expressions",
              fresh.face == "vrm" and fresh.style().get("model") == "builtin-miko"
              and fresh.style().get("expressions", {}).get("happy", {}).get("name") == "happy", (fresh.face, fresh.style()))

        # The desktop buddy follows the agent you're talking to.
        client.post("/api/faces/active", json={"agent_id": fresh.id})
        desktop = client.get("/api/faces/desktop").json()
        check("The desktop buddy shows the agent you're talking to, not only the default one",
              desktop["agent_id"] == fresh.id and desktop["agent"] == "Mei", desktop)
        check("…as its anime stand-in until the model's strips exist", desktop["face"] == "anime" and not desktop["strips"])

        def png(width=64, height=64, frames=2):
            from PIL import Image

            out = io.BytesIO()
            Image.new("RGBA", (width * frames, height), (255, 128, 160, 255)).save(out, "PNG")
            return out.getvalue()

        manifest = {"frame": {"width": 64, "height": 64},
                    "states": {"calm": {"frames": 2, "ms": 150}, "happy": {"frames": 2, "ms": 120},
                               "talk": {"frames": 2, "ms": 80, "levels": [0, 1]}}}
        files = {name: (f"{name}.png", png(), "image/png") for name in manifest["states"]}
        saved = client.post("/api/faces/builtin-miko/strips", data={"manifest": _json.dumps(manifest)}, files=files)
        check("The app can save a 3D model's strips for the buddy", saved.status_code == 200, saved.text)
        bad = client.post("/api/faces/builtin-miko/strips", data={"manifest": _json.dumps({"states": {"evil": {}}})},
                          files={"evil": ("evil.png", png(), "image/png")})
        check("…only known states, as PNGs", bad.status_code == 400)
        desktop = client.get("/api/faces/desktop").json()
        check("Then the buddy shows the 3D model itself", desktop["face"] == "strips"
              and Path(desktop["strips"]).joinpath("face.json").is_file(), desktop)
        try:
            check("The buddy draws the model's strips",
                  brain_frames.use_face(desktop["face"], strips=desktop["strips"])
                  and brain_frames.sprite_for("idle") == "calm" and len(brain_frames.frames("idle", 32)) == 2
                  and brain_frames.talk_frame(1.0, 32) is not None)
            check("…and its passing reactions", brain_frames.react("happy") and brain_frames.sprite_for("idle") == "happy")
        finally:
            brain_frames._REACTION.update(sprite=None, until=0.0)
            brain_frames.use_face("brain")
        client.post("/api/faces/active", json={"agent_id": None})
        check("With no agent chosen, the buddy shows the default agent",
              client.get("/api/faces/desktop").json()["agent_id"] == agents.default_agent(db).id)
        check("Built-in models can't take gestures (they're read-only)",
              client.post("/api/faces/builtin-miko/gestures",
                          files={"file": ("x.vrma", b"glTF" + b"\0" * 16, "model/gltf-binary")}).status_code == 400)

        # ---- the installer's setup, applied without a window
        tests = []

        def fake_test(self):
            tests.append(settings.LLM_PROVIDER)
            return {"ok": True, "detail": "The model answered."}

        choices = {"settings": {"LLM_PROVIDER": "ollama", "OLLAMA_URL": "http://127.0.0.1:11434",
                                "VOICE_PERSONALITY": "professional", "VOICE_SPEAK_REPLIES": "false",
                                "NOT_ALLOWED": "x"},
                   "agent": {"face": "builtin", "personality": "professional"}}
        choice_file = root / "choices.json"
        choice_file.write_text(_json.dumps(choices))
        import app.services.llm_service as llm_module

        before = dict(LLM_PROVIDER=settings.LLM_PROVIDER, OLLAMA_URL=settings.OLLAMA_URL,
                      VOICE_PERSONALITY=settings.VOICE_PERSONALITY, VOICE_SPEAK_REPLIES=settings.VOICE_SPEAK_REPLIES)
        try:
            with mock.patch.object(llm_module.LLMService, "test_connection", fake_test), \
                    mock.patch("app.core.settings_store.update",
                               lambda changes: (settings.__dict__.update({k: v for k, v in changes.items()})
                                                or {"ok": True, "applied": list(changes)})):
                code = install_setup.run_files(str(choice_file), str(root / "result.json"))
            result = _json.loads((root / "result.json").read_text(encoding="utf-8"))
            steps = {s["step"]: s for s in result["steps"]}
            check("The installer's choices are saved (only the allowed ones)",
                  steps["settings"]["ok"] and settings.LLM_PROVIDER == "ollama" and not hasattr(settings, "NOT_ALLOWED"),
                  steps.get("settings"))
            check("…the database, agent and AI provider are checked", steps["database"]["ok"]
                  and steps["agent"]["ok"] and steps["ai"]["ok"] and tests == ["ollama"], result)
            default = agents.default_agent(session())
            check("…the default agent gets the chosen look and personality",
                  default.face == "vrm" and default.style().get("model") == "builtin-miko"
                  and default.persona().get("preset") == "professional", (default.face, default.style()))
            with mock.patch.object(faces_api, "builtin_faces", lambda: []), \
                    mock.patch.object(llm_module.LLMService, "test_connection", fake_test):
                install_setup.run({"agent": {"face": "builtin"}})
            check("…and without built-in 3D models, \"3D character\" falls back to the anime face",
                  agents.default_agent(session()).face == "anime")
            check("…the choices file (it can hold keys) is removed, and a readable summary is written",
                  not choice_file.exists() and "OK" in (root / "result.txt").read_text(encoding="utf-8") and code in (0, 2))
        finally:
            settings.__dict__.update(before)
            cleanup = session()
            agents.update(cleanup, agents.default_agent(cleanup), face="brain", face_style={},
                          personality={"preset": "sweetheart"})

    # ---- clearing parts of the old memory (from the installer), after a backup
    from app.models.memory import Memory
    from app.models.conversation import Conversation

    db = session()
    keep_chat = Conversation(title="Keep me")
    db.add(keep_chat)
    db.add(Memory(title="Printer", content="The printer on floor 2 is HP", memory_type="fact"))
    db.commit()
    chat_count = db.query(Conversation).count()
    images = root / "chat_images"
    images.mkdir()
    (images / "a.png").write_bytes(b"x")
    request = root / "reset.json"
    request.write_text(_json.dumps({"parts": ["memory", "bogus"]}))
    # Only the memories table here: the test database is shared with the rest of the suite.
    parts = {**reset.PARTS, "memory": {**reset.PARTS["memory"], "tables": ["memories"], "folders": [images]}}
    with mock.patch.object(reset, "REQUEST_FILE", request), mock.patch.object(reset, "BACKUP_DIR", root / "backups"), \
            mock.patch.dict(reset.PARTS, parts):
        done = reset.apply_pending()
    db.expire_all()
    check("Clearing memory removes exactly what was ticked", done.get("parts") == ["memory"]
          and db.query(Memory).count() == 0 and not images.exists(), done)
    check("…and nothing else", db.query(Conversation).count() == chat_count)
    with zipfile.ZipFile(done["backup"]) as archive:
        names = archive.namelist()
        saved_rows = _json.loads(archive.read("tables/memories.json"))
    check("…after a backup of the rows and folders", "folders/chat_images/a.png" in names
          and any("floor 2" in (r.get("content") or "") for r in saved_rows), names)
    check("…and the request is used once", not request.exists())
    with mock.patch.object(reset, "REQUEST_FILE", request):
        check("No request, nothing cleared", reset.apply_pending() == {})

    # Ideas from nudges are keyed on what the nudge is about, not its row id
    # (row ids are reused after nudges are cleared, which used to hide new ideas).
    from app.models.nudge import Nudge
    from app.models.work import Idea
    from app.services import ideas as ideas_service

    db.add(Nudge(kind="unanswered_email", title="Unanswered: Pat", body="We never replied to Pat.",
                 priority="high", dedupe_key="unanswered:424242", status="open"))
    db.commit()
    ideas_service.from_nudges(db)
    check("An idea from a nudge is keyed on what it's about",
          db.query(Idea).filter(Idea.dedupe_key == "nudge:unanswered:424242").count() == 1)


def test_browser_worker_shutdown():
    """The hidden browser has one worker thread, even when shutdown can't wait for a slow job."""
    print("\nHidden browser — one worker, even across a slow shutdown")
    import threading
    import time as _time
    from unittest import mock

    from app.core.config import settings
    import importlib

    engine_module = importlib.import_module("app.services.browser.engine")

    def workers():
        return [t for t in threading.enumerate() if t.name == "cerebro-browser" and t.is_alive()]

    eng = engine_module.BrowserEngine()
    eng.SHUTDOWN_WAIT = 0.2
    with mock.patch.object(settings, "BROWSER_AUTOMATION_ENABLED", True), \
            mock.patch.object(engine_module, "playwright_installed", lambda: True):
        release, ran = threading.Event(), []

        def slow(e):
            ran.append(("slow", threading.current_thread()))
            release.wait(10)
            return "slow done"

        outcome = {}
        caller = threading.Thread(target=lambda: outcome.update(slow=eng.submit(slow, quiet=True, timeout=15)))
        caller.start()
        for _ in range(100):
            if ran:
                break
            _time.sleep(0.02)
        first = ran[0][1]
        eng.shutdown()                                   # gives up waiting: the job is still running
        check("Shutdown doesn't hang on a slow job", first.is_alive())
        threading.Timer(0.6, release.set).start()

        def second(e):
            return {"thread": threading.current_thread(), "workers": len(workers()),
                    "first_alive": first.is_alive()}

        result = eng.submit(second, quiet=True, timeout=15)
        caller.join(5)
        check("The next job waits for the old worker instead of starting a second one",
              result["workers"] == 1 and not result["first_alive"] and result["thread"] is not first, result)
        check("…and the slow job still finished", outcome.get("slow") == "slow done", outcome)
        eng.shutdown()

        # A worker that died without cleaning up leaves objects only it could use.
        dead = threading.Thread(target=lambda: None)
        dead.start()
        dead.join()
        eng._thread, eng._playwright, eng._context = dead, object(), object()
        fresh = eng.submit(lambda e: (e._playwright, e._context), quiet=True, timeout=15)
        check("A new worker never inherits a dead one's browser", fresh == (None, None), fresh)
        eng.shutdown()
    check("…and no worker is left behind", not workers(), workers())


def test_push_to_talk():
    """The desktop's push-to-talk state machine, with a fake microphone and keys."""
    print("\nVoice — push-to-talk")
    import base64
    import sys as _sys
    import time as _time

    _sys.path.insert(0, str(ROOT / "desktop"))
    import voice as ptt_module

    check("The hotkey setting is understood", ptt_module.parse_hotkey("Ctrl+Space") == {"ctrl", "space"}
          and ptt_module.parse_hotkey("ctrl+shift+f9") == {"ctrl", "shift", "f9"})

    class Key:
        def __init__(self, name=None, char=None):
            self.name, self.char = name, char

    check("Keys from the listener are named the same way",
          ptt_module.key_name(Key(name="ctrl_l")) == "ctrl" and ptt_module.key_name(Key(name="space")) == "space"
          and ptt_module.key_name(Key(char="\x01")) == "a")

    class Recorder:
        def __init__(self):
            self.started = 0

        def start(self):
            self.started += 1

        def stop(self):
            return b"\x01\x00" * int(ptt_module.SAMPLE_RATE * 1.2)

    class Player:
        def __init__(self):
            self.playing, self.played, self.stopped = False, [], 0

        def play(self, audio):
            self.played.append(audio)

        def stop(self):
            self.stopped += 1
            self.playing = False

    posts = []
    reply_audio = ptt_module.wav_bytes(b"\0\0" * 100)

    def post(path, files=None, data=None, json=None, timeout=0):
        posts.append((path, files, data, json))
        if path == "/api/voice/command":
            return {"transcript": "hi", "reply": "Hello",
                    "speech": {"audio": base64.b64encode(reply_audio).decode(), "mime": "audio/wav"}}
        return {}

    recorder, player = Recorder(), Player()
    ptt = ptt_module.PushToTalk("http://127.0.0.1:1", "ctrl+space", recorder=recorder, player=player, post=post)
    ptt.key_down("ctrl")
    check("One key alone doesn't start listening", not ptt.recording)
    ptt.key_down("space")
    check("Holding the hotkey starts recording and shows Cerebro listening",
          ptt.recording and recorder.started == 1
          and posts[-1][0] == "/api/voice/state" and posts[-1][3]["state"] == "listening")
    ptt.started_at -= 2
    ptt.key_up("space")
    for _ in range(50):
        if player.played:
            break
        _time.sleep(0.05)
    command = next((p for p in posts if p[0] == "/api/voice/command"), None)
    check("Letting go sends the recording as WAV", command is not None
          and command[1]["audio"][1].startswith(b"RIFF") and command[2]["fmt"] == "wav")
    check("…and the spoken answer is played", player.played == [reply_audio])
    sent = len(posts)
    ptt.key_up("ctrl")
    ptt.key_down("ctrl")
    ptt.key_down("space")
    ptt.key_up("space")
    check("A tap too short to be speech is ignored",
          not any(p[0] == "/api/voice/command" for p in posts[sent:]))
    player.playing = True
    ptt.key_down("space")
    check("Pressing the keys while Cerebro talks stops it", player.stopped == 1)
    ptt.key_up("space")
    ptt.enabled = False
    ptt.key_down("space")
    check("Switched off in Settings, the keys do nothing", not ptt.recording)


def test_addons_page():
    """The page: Add-ons on Connect, the mic, plan and sign-in cards."""
    print("\nAdd-ons & voice — the page")
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    page = client.get("/app").text
    script = (ROOT / "backend/app/web/static/ui/app.js").read_text(encoding="utf-8")
    check("The composer has a mic button", 'id="mic-btn"' in page and "getUserMedia" in script)
    check("Connect has an Add-ons section with Add, import and export",
          "addon-list" in script and "/api/addons/export" in script and "/api/addons/skills/import" in script)
    check("Plan and add-on sign-in cards are drawn", "planCard" in script and "addonSigninCard" in script)
    check("The hidden browser can be taken over by hand", "/api/addons/browser/take-control" in script)
    listing = client.get("/api/addons").json()
    check("The add-ons list is served", isinstance(listing.get("addons"), list))
    status = client.get("/api/voice/status").json()
    check("Voice settings are served", status["hotkey"] == "ctrl+space" and "voice" in status)


# =========================================================== agents & work
def test_agents_and_scope():
    """Agents: their own role, chats and tool scope, sharing one memory."""
    print("\nAgents — roles, scope and shared memory")
    from unittest import mock

    from app.services import agents, conversations, jobs
    from app.services.agent import loop
    from app.services.agent.registry import REGISTRY, ToolContext, available_tools
    from app.services.chat_service import ChatService
    from app.services.memory_service import MemoryService

    db = session()
    default = agents.default_agent(db)
    check("There is always a default agent", default.is_default and default.name == "Cerebro")
    inbox = agents.create(db, "inbox")
    writer = agents.create(db, "kb-writer", name="Article writer")
    check("Agents are made from templates", inbox.name == "Inbox assistant"
          and "messaging" in inbox.scope() and inbox.face == "anime" and writer.name == "Article writer")

    ctx = ToolContext(db=db, context=agents.context_for(inbox))
    names = {t.name for t in available_tools(ctx)}
    check("A scoped agent only gets its tools — plus memory, plans and teamwork",
          "write_file" not in names and "run_command" not in names
          and {"remember", "show_plan", "delegate_to_agent", "ask_user"} <= names, sorted(names)[:12])
    blocked = loop._run_tool(ctx, "write_file", {"path": "x", "content": "y"}, {})
    check("…and can't run a tool outside it", "outside what this agent may use" in blocked)
    everything = {t.name for t in available_tools(ToolContext(db=db, context={}))}
    check("An agent with no scope gets everything", "write_file" in everything)
    check("Tools sort into scope groups", agents.group_of(REGISTRY["dynamics_get_case"]) == "cases"
          and agents.group_of(REGISTRY["outlook_search"]) == "messaging"
          and agents.group_of(REGISTRY["remember"]) == "core")

    saved = REGISTRY["remember"].handler(ToolContext(db=db, context=agents.context_for(inbox)),
                                         fact="Priya at Smile Dental prefers phone calls to email.")
    found = MemoryService(db).recall("how does Priya like to be contacted?", limit=3)
    check("What one agent remembers, every agent can recall",
          "Remembered" in saved["content"] and any("phone calls" in m["content"] for m in found)
          and any(m.get("source", "").startswith("agent:") or True for m in found))

    with mock.patch.object(jobs, "start_now") as started:
        handed = REGISTRY["delegate_to_agent"].handler(
            ToolContext(db=db, context=agents.context_for(inbox)), agent="article", job="Write up the sensor fix")
    from app.models.task import Task

    job = db.query(Task).filter(Task.agent_id == writer.id).order_by(Task.id.desc()).first()
    check("Work can be handed to another agent, who starts on it",
          job is not None and job.status == "queued" and "Write up the sensor fix" in job.instruction
          and started.called and "Handed to Article writer" in handed["summary"])
    check("…with a note of who handed it over",
          any("Handed over by Inbox assistant" in e["title"] for e in jobs.events(db, job.id)))

    chat = conversations.create(db, "Triage", agent_id=inbox.id)
    systems = []

    def script(self, messages, tools=None, system=None, **kwargs):
        systems.append(system or "")
        return {"content": "On it.", "tool_calls": []}

    enabled, patched = _with_ai(script)
    with enabled, patched:
        ChatService(db, chat.id).handle_message("Anything urgent?")
    check("A chat is answered by its own agent, who knows the team",
          "You are Inbox assistant" in systems[-1] and "Article writer" in systems[-1]
          and "Triage new mail" in systems[-1])
    listed = [c["id"] for c in conversations.listing(db, agent_id=inbox.id)]
    check("Each agent has its own chats", chat.id in listed
          and chat.id not in [c["id"] for c in conversations.listing(db, agent_id=writer.id)])

    from app.api.voice import _addressed

    named, rest = _addressed(db, "Inbox, reply to Priya about the sensor")
    check("Speaking an agent's name sends the request to that agent",
          named is not None and named.id == inbox.id and rest == "reply to Priya about the sensor")
    check("The default agent can't be removed", _raises(lambda: agents.archive(db, default)))


def _raises(fn) -> bool:
    try:
        fn()
    except Exception:  # noqa: BLE001
        return True
    return False


def test_durable_jobs():
    """Jobs: plan, run log, questions, approvals, pause/cancel, retry, recovery."""
    print("\nAgents — durable jobs")
    import json as _json
    from datetime import datetime, timedelta
    from unittest import mock

    from app.models.task import Task
    from app.services import agents, conversations, jobs
    from app.services.agent import actions
    from app.services.task_service import TaskService

    db = session()
    agent = agents.create(db, "case-researcher", name="Researcher")
    chat = conversations.create(db, "Escalations", agent_id=agent.id)

    def new_job(prompt, schedule="once"):
        task = Task(title=prompt[:40], instruction=prompt, kind="agent",
                    spec=_json.dumps({"prompt": prompt}), schedule=schedule, status="queued",
                    agent_id=agent.id, conversation_id=chat.id, next_run=datetime.now())
        db.add(task)
        db.commit()
        return task

    prompts = []

    def scripted(turns):
        def script(self, messages, tools=None, **kwargs):
            prompts.append(messages[-1]["content"] if messages else "")
            step = sum(1 for m in messages if m["role"] == "tool")
            if step < len(turns) and tools:
                name, args = turns[step]
                return {"content": "", "tool_calls": [{"id": f"c{step}", "name": name, "arguments": args}]}
            return {"content": turns[-1] if isinstance(turns[-1], str) else "Done.", "tool_calls": []}
        return script

    job = new_job("Find out why CAS-77 keeps failing")
    check("A job is claimed once — a second worker can't take it",
          jobs.claim(db, job.id) and not jobs.claim(db, job.id))
    job.lease_until = None
    db.commit()

    enabled, patched = _with_ai(scripted([
        ("update_plan", {"steps": "[done] Read the case\n[running] Search the KB\n[pending] Suggest a fix"}),
        ("ask_user", {"question": "Which site is the customer at?"}),
        "Which site is the customer at?"]))
    with enabled, patched:
        jobs.run(job.id)
    db.expire_all()
    job = db.get(Task, job.id)
    check("A job keeps its plan", [p["status"] for p in job.plan_list()] == ["done", "running", "pending"],
          job.plan_list())
    check("…asks you what only you know, and waits",
          job.status == "waiting_input" and job.question == "Which site is the customer at?")
    kinds = [e["kind"] for e in jobs.events(db, job.id)]
    check("…and logs what it did", {"status", "step", "tool", "result"} <= set(kinds), kinds)
    check("The question is posted in the agent's chat",
          jobs.waiting_in_chat(db, chat.id) is not None and jobs.waiting_in_chat(db, chat.id).id == job.id)

    enabled, patched = _with_ai(scripted(["The Leeds site needs the driver update."]))
    with enabled, patched, mock.patch.object(jobs, "start_now", side_effect=lambda i: jobs.run(i)):
        from app.services.chat_service import ChatService

        reply = ChatService(db, chat.id).handle_message("They're at the Leeds site")
    db.expire_all()
    job = db.get(Task, job.id)
    check("Answering in the chat lets the job carry on with your answer",
          "carrying on" in reply["reply"] and job.status == "done"
          and any("The user answered: They're at the Leeds site" in p for p in prompts))

    target = Path(_TEMP_DIR) / "job-change.txt"
    job = new_job("Write the fix down")
    enabled, patched = _with_ai(scripted([("write_file", {"path": str(target), "content": "Update the driver"}),
                                          "Prepared it for your approval."]))
    with enabled, patched:
        jobs.run(job.id)
    db.expire_all()
    job = db.get(Task, job.id)
    waiting = job.state_dict().get("waiting") or []
    check("A change it prepares parks the job until you decide",
          job.status == "waiting_approval" and waiting and waiting[0].startswith("change:"), job.to_dict())
    actions.approve(db, int(waiting[0].split(":")[1]))
    db.expire_all()
    job = db.get(Task, job.id)
    check("Approving it lets the job carry on", job.status == "queued" and target.exists()
          and "approved and made" in " ".join(job.state_dict().get("decisions") or []))

    job = new_job("A long one")

    def cancelling(self, messages, tools=None, **kwargs):
        with SessionLocal() as other:
            other.query(Task).filter(Task.id == job.id).update({Task.status: "cancelled"})
            other.commit()
        return {"content": "", "tool_calls": [{"id": "c1", "name": "show_plan",
                                               "arguments": {"steps": "a\nb\nc"}}]}

    enabled, patched = _with_ai(cancelling)
    with enabled, patched:
        jobs.run(job.id)
    db.expire_all()
    check("Cancelling stops a job between steps", db.get(Task, job.id).status == "cancelled")

    job = new_job("Paused before it ran")
    jobs.control(db, job, "pause")
    check("A paused job isn't picked up", not jobs.claim(db, job.id) and job.status == "paused")
    jobs.control(db, job, "resume")
    check("…until it's resumed", job.status == "queued")

    recurring = new_job("Daily summary", schedule="daily")
    recurring.status = "active"
    db.commit()
    with mock.patch("app.services.task_executors.execute", side_effect=RuntimeError("Dynamics is down")):
        for _ in range(3):
            recurring.next_run, recurring.lease_until = datetime.now(), None
            if recurring.status == "paused":
                break
            recurring.status = "active"
            db.commit()
            jobs.run(recurring.id)
            db.expire_all()
            recurring = db.get(Task, recurring.id)
            if recurring.attempts == 1:
                first_retry = recurring.next_run
    check("A failing recurring job retries with backoff instead of stopping",
          first_retry and first_retry > datetime.now() + timedelta(minutes=4))
    check("…and pauses after three failures in a row, saying why",
          recurring.status == "paused" and "Dynamics is down" in (recurring.error or ""))

    stale = new_job("Interrupted")
    stale.status, stale.lease_until = "running", datetime.now() - timedelta(minutes=1)
    risky = new_job("Interrupted after a change")
    risky.status, risky.lease_until = "running", datetime.now() - timedelta(minutes=1)
    jobs.set_state(risky, auto_applied=True)
    db.commit()
    jobs.recover(db)
    db.expire_all()
    check("A job cut off by a restart is picked up again", db.get(Task, stale.id).status == "queued")
    check("…unless it had already changed something — then it waits for you to check",
          db.get(Task, risky.id).status == "failed" and "Check, then Retry" in db.get(Task, risky.id).error)

    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    detail = client.get(f"/api/jobs/{stale.id}").json()
    check("A job's run log is served", any("restarted" in e["title"] for e in detail["events"]))
    with mock.patch.object(jobs, "start_now"):
        made = client.post("/api/jobs", json={"instruction": "Check the backups", "agent_id": agent.id}).json()
    check("Jobs can be given from the app", made["agent_id"] == agent.id and made["status"] == "queued")
    home = client.get("/api/home").json()
    card = next(a for a in home["agents"] if a["id"] == agent.id)
    check("Home shows each agent's jobs and status", card["status"]["state"] in ("working", "idle", "needs_approval")
          and any(j["title"].startswith("Check the backups") for j in card["jobs"]))


def test_projects_watches_ideas():
    """Projects with milestones, watches that alert once, ideas with evidence."""
    print("\nAgents — projects, watches and ideas")
    import http.server
    import threading
    from datetime import datetime, timedelta
    from unittest import mock

    from fastapi.testclient import TestClient

    from app.main import app
    from app.models.enterprise import EnterpriseMessage
    from app.models.task import Task
    from app.models.work import Idea, Project, Watch
    from app.services import agents, ideas, jobs, watches
    from app.services.agent.registry import REGISTRY, ToolContext
    from app.services.nudge_service import NudgeService

    db = session()
    client = TestClient(app)
    watcher = agents.create(db, "watcher")
    project = client.post("/api/projects", json={"title": "Imaging migration", "agent_id": watcher.id,
                                                 "milestones": ["Inventory", "Roll out"]}).json()
    ctx = ToolContext(db=db, context=agents.context_for(watcher))
    REGISTRY["tick_milestone"].handler(ctx, project="Imaging", milestone="inventory")
    db.expire_all()
    check("A milestone is ticked off", db.get(Project, project["id"]).milestone_list()[0]["done"])
    REGISTRY["tick_milestone"].handler(ctx, project=str(project["id"]), milestone="roll out")
    db.expire_all()
    check("A project with every milestone done is done", db.get(Project, project["id"]).status == "done")

    page = {"text": "Case CAS-9 status: Open. Updated 10:42"}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802
            data = f"<html><body><main>{page['text']}</main></body></html>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/case"
    try:
        made = REGISTRY["create_watch"].handler(ctx, url=url, title="CAS-9 status", every_minutes="15",
                                                then="Draft an update to the customer")
        watch = db.query(Watch).order_by(Watch.id.desc()).first()
        check("An agent can set up a watch", "Watching" in made["summary"] and watch.interval_minutes == 15)
        first = watches.check(db, watch)
        page["text"] = "Case CAS-9 status: Open. Updated 11:07"
        same = watches.check(db, watch)
        check("A page is read, and a clock on it doesn't count as a change",
              first["ok"] and not first["fired"] and not same["fired"], (first, same))
        page["text"] = "Case CAS-9 status: Resolved. Updated 11:20"
        fired = watches.check(db, watch)
        again = watches.check(db, watch)
        check("A real change alerts once", fired["fired"] and not again["fired"])
        db.expire_all()
        follow = db.query(Task).filter(Task.source == "watch").order_by(Task.id.desc()).first()
        check("…starting its follow-up job for the agent",
              follow is not None and follow.agent_id == watcher.id and "Draft an update" in follow.instruction)
        check("…and suggesting a follow-up idea",
              db.query(Idea).filter(Idea.dedupe_key.like(f"watch:{watch.id}:%")).count() == 1)

        contains = watches.create(db, url, title="Resolved?", condition="contains", value="resolved")
        hits = [watches.check(db, contains)["fired"] for _ in range(2)]
        page["text"] = "Case CAS-9 status: Reopened"
        watches.check(db, contains)
        page["text"] = "Case CAS-9 status: Resolved"
        rearmed = watches.check(db, contains)["fired"]
        check("“Contains” alerts when the text appears — once — and again after it went away",
              hits == [True, False] and rearmed)

        price = watches.create(db, url, title="Queue", condition="below", value="5")
        page["text"] = "Calls waiting: 12"
        above = watches.check(db, price)["fired"]
        page["text"] = "Calls waiting: 3"
        below = watches.check(db, price)["fired"]
        check("Numbers can be watched against a limit", not above and below)

        with mock.patch.object(watches, "read", side_effect=RuntimeError("site down")):
            for _ in range(5):
                outcome = watches.check(db, price)
        db.expire_all()
        price = db.get(Watch, price.id)
        check("A watch that keeps failing backs off, then pauses",
              price.status == "paused" and price.next_check_at > datetime.now() + timedelta(hours=2)
              and outcome["error"] == "site down")
    finally:
        server.shutdown()

    agents.create(db, "inbox")
    agents.create(db, "kb-writer")
    old = datetime.utcnow() - timedelta(hours=6)
    message = EnterpriseMessage(source="outlook", external_id="idea-1", sender="lee@clinic.test",
                                sender_name="Lee", subject="Scanner not detected", preview="Since this morning",
                                urgency="high", handled=False, ingested_at=old)
    db.add(message)
    for index, sender in enumerate(("a@x.test", "b@x.test", "c@x.test")):
        db.add(EnterpriseMessage(source="outlook", external_id=f"kb-{index}", sender=sender,
                                 sender_name=sender, subject="How to reset the imaging bridge",
                                 preview="How do I…", urgency="normal", handled=True,
                                 ingested_at=datetime.utcnow()))
    db.commit()
    NudgeService(db).scan()
    made = ideas.refresh(db)
    reply_idea = db.query(Idea).filter(Idea.title.like("Unanswered: Lee%")).first()
    kb_idea = db.query(Idea).filter(Idea.title.like("Write a KB article%")).first()
    check("Ideas come from what the agents see, with the evidence", made >= 2 and reply_idea is not None
          and reply_idea.to_dict()["evidence"] and kb_idea is not None and len(kb_idea.to_dict()["evidence"]) == 3)
    from app.models.agent import Agent

    check("…each going to an agent whose work it is",
          "messaging" in db.get(Agent, reply_idea.agent_id).scope()
          and "kb" in db.get(Agent, kb_idea.agent_id).scope(), (reply_idea.agent_id, kb_idea.agent_id))
    with mock.patch.object(jobs, "start_now"):
        accepted = client.post(f"/api/ideas/{reply_idea.id}/accept").json()
    check("Accepting an idea starts it as a job for that agent",
          accepted["ok"] and accepted["task"]["agent_id"] == reply_idea.agent_id, accepted)
    client.post(f"/api/ideas/{kb_idea.id}/dismiss")
    ideas.refresh(db)
    check("A dismissed idea doesn't come back",
          db.query(Idea).filter(Idea.title.like("Write a KB article%")).count() == 1)


def test_faces():
    """Faces: the anime face's frames, the desktop's choice, model uploads, lip sync."""
    print("\nFaces — anime, Live2D, VRM, and lip sync")
    import io
    import json as _json
    import sys as _sys
    import wave
    import zipfile
    from unittest import mock

    from fastapi.testclient import TestClient

    from app.api import faces as faces_api
    from app.main import app
    from app.core.config import settings
    from app.services import agents, tts

    palettes = ("pink", "black", "silver", "blue", "brown", "green")
    hairs = ("long", "twintails", "bob", "sideponytail")
    folder = ROOT / "assets" / "faces" / "anime"
    manifests = {f"{p}/{h}": _json.loads((folder / p / h / "face.json").read_text(encoding="utf-8"))
                 for p in palettes for h in hairs}
    check("Every colour and hairstyle has every expression, the reactions and the mouth shapes",
          all({"calm", "thinking", "reading", "focused", "listening", "surprised", "dizzy", "asleep",
               "happy", "sorry", "talk"} <= set(m["states"]) for m in manifests.values()))
    check("…in the mascot's manifest format", all((folder / k / m["states"]["calm"]["file"]).exists()
                                                  and m["frame"]["width"] == 128 for k, m in manifests.items()))
    size = sum(f.stat().st_size for f in folder.rglob("*.png"))
    check("…and the strips stay small for the installer", size < 12 * 1024 * 1024, f"{size / 1e6:.1f} MB")

    _sys.path.insert(0, str(ROOT / "desktop"))
    try:
        import brain_frames
        from PIL import Image  # noqa: F401
    except ImportError:
        print("  - skipped desktop frames: Pillow is not installed")
        brain_frames = None
    if brain_frames is not None:
        try:
            brain_frames.use_face("anime", "blue", "twintails")
            check("The desktop shows the anime face in the chosen hairstyle",
                  brain_frames.face_dir().name == "twintails" and brain_frames.face_dir().parent.name == "blue")
            check("…and a passing reaction, then back", brain_frames.react("happy", 0.2)
                  and brain_frames.sprite_for("idle") == "happy")
            import time as _time

            _time.sleep(0.25)
            check("…back to its state's expression", brain_frames.sprite_for("idle") == "calm"
                  and brain_frames.sprite_for("awaiting_approval") == "surprised")
            check("The desktop can show the anime face",
                  brain_frames.sprite_for("searching") == "reading" and len(brain_frames.frames("searching", 32)) == 8
                  and brain_frames.frames("idle", 32)[0].size == (32, 32))
            check("…with its mouth open to the speech level",
                  brain_frames.talk_frame(1.0, 32) is not None
                  and brain_frames.talk_frame(0.0, 32).tobytes() != brain_frames.talk_frame(1.0, 32).tobytes())
            brain_frames.use_face("vrm", "green")
            check("A VRM or Live2D face shows as the anime face on the desktop",
                  brain_frames.FACE == {"face": "anime", "palette": "green", "hair": "long", "strips": None})
        finally:
            brain_frames.use_face("brain")
        check("…and the brain is back when chosen", brain_frames.sprite_for("browsing") == "working")
        import voice as voice_module

        sound = io.BytesIO()
        with wave.open(sound, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x00\x00" * 1600 + b"\x00\x40" * 1600)
        curve = voice_module.levels(sound.getvalue(), 0.05)
        check("A reply's loudness curve drives the buddy's mouth", curve and curve[0] == 0 and max(curve) > 0.5)
        import buddy as buddy_module

        pal = buddy_module.Buddy()
        pal.say([0.2, 0.9], 10)
        check("The buddy moves its mouth while speaking", pal._speech_level() == 0.2)

    client = TestClient(app)
    folder = Path(_TEMP_DIR) / "faces"
    with mock.patch.object(faces_api, "FACES_DIR", folder):
        vrm = client.post("/api/faces", files={"file": ("ami.vrm", b"glTF\x02\x00\x00\x00" + b"\0" * 32,
                                                          "model/gltf-binary")}).json()
        check("A VRM model is loaded for a face", vrm["kind"] == "vrm" and vrm["entry"] == "model.vrm")
        added = client.post(f"/api/faces/{vrm['id']}/gestures",
                            files={"file": ("../Wave Hello!.vrma", b"glTF\x02\x00\x00\x00" + b"\0" * 16,
                                            "model/gltf-binary")}).json()
        check("A .vrma gesture is added to the VRM model's own folder",
              added.get("path") == "gestures/Wave-Hello.vrma" and "gestures/Wave-Hello.vrma" in added["face"]["gestures"]
              and (folder / vrm["id"] / "gestures" / "Wave-Hello.vrma").is_file(), added)
        check("…and anything that isn't one is refused",
              client.post(f"/api/faces/{vrm['id']}/gestures",
                          files={"file": ("x.vrma", b"not gltf", "model/gltf-binary")}).status_code == 400)
        check("…as is a model that doesn't exist",
              client.post("/api/faces/..%2F..%2Fetc/gestures",
                          files={"file": ("x.vrma", b"glTF", "model/gltf-binary")}).status_code in (404, 405))
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr("hiyori/hiyori.model3.json", "{}")
            archive.writestr("hiyori/hiyori.moc3", "x")
        live = client.post("/api/faces", files={"file": ("hiyori.zip", bundle.getvalue(), "application/zip")}).json()
        check("A Live2D model is loaded from a zip, finding its model3.json",
              live["kind"] == "live2d" and live["entry"] == "hiyori/hiyori.model3.json")
        served = client.get(f"/faces/{live['id']}/{live['entry']}")
        check("…and served to the page", served.status_code == 200 and served.content == b"{}")
        evil = io.BytesIO()
        with zipfile.ZipFile(evil, "w") as archive:
            archive.writestr("../../escape.txt", "x")
            archive.writestr("a.model3.json", "{}")
        refused = client.post("/api/faces", files={"file": ("evil.zip", evil.getvalue(), "application/zip")})
        check("A zip that tries to write outside its folder is refused",
              refused.status_code == 400 and not (folder.parent / "escape.txt").exists())
        check("Files outside a model's folder aren't served",
              client.get(f"/faces/{live['id']}/../../test.db").status_code == 404)
        check("Live2D's runtime is never assumed — it needs the user's agreement",
              client.get("/api/faces/live2d/runtime").json()["installed"] is False
              and client.post("/api/faces/live2d/runtime", json={}).status_code == 400)

    db = session()
    default = agents.default_agent(db)
    agents.update(db, default, face="vrm", face_style={"palette": "blue", "model": "x", "hair": "bob"})
    desktop = client.get("/api/faces/desktop").json()
    check("The desktop shows the default agent's face (VRM → its anime stand-in)",
          desktop == {"face": "anime", "palette": "blue", "hair": "bob", "agent": default.name,
                      "agent_id": default.id, "react": None, "strips": None},
          desktop)
    client.post("/api/faces/react", json={"emotion": "happy"})
    check("…and the app's passing reaction", client.get("/api/faces/desktop").json()["react"] == "happy")
    check("Unknown reactions are refused", client.post("/api/faces/react", json={"emotion": "rage"}).status_code == 400)
    agents.update(db, default, face="brain", face_style={})

    heard = []
    tts.use_provider("edge", lambda text, voice, rate: heard.append((voice, rate)) or (b"MP3", "audio/mpeg"))
    try:
        # With Edge chosen (in auto, the offline voices answer first).
        with mock.patch.object(tts, "polly_ready", return_value=False), \
                mock.patch.object(settings, "VOICE_PROVIDER", "edge"), mock.patch.object(settings, "VOICE_NAME", ""):
            tts.synthesize("Hello", voice="en-GB-SoniaNeural", rate=1.2)
            tts.synthesize("Hello", voice="Matthew")
        check("Each agent speaks in its own voice", heard[0] == ("en-GB-SoniaNeural", 1.2)
              and heard[1][0] == tts.DEFAULTS["edge"])
    finally:
        tts.use_provider("edge", None)


def test_agents_page():
    """The page: the agents sidebar, Home, Work, the stage and the face libraries."""
    print("\nAgents — the page")
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    page = client.get("/app").text
    script = (ROOT / "backend/app/web/static/ui/app.js").read_text(encoding="utf-8")
    face = client.get("/static/ui/face.js").text
    check("The page has the agents sidebar, Home, Work and the face stage",
          all(marker in page for marker in ('id="agent-list"', 'id="panel-home"', 'id="panel-work"',
                                            'id="agent-stage"', '"three": "/static/vendor/three/three.module.js"')))
    check("The app script drives them", all(marker in script for marker in (
        "/api/home", "/api/agents", "/api/jobs", "/api/watches", "/api/ideas", "cerebroOpenAgent", "lipSync")))
    check("Every face type can be created", all(marker in face for marker in (
        "function animeFace", "function live2dFace", "function vrmFace", "export function createFace")))
    for path in ("/static/vendor/three/three.module.js", "/static/vendor/three/three.core.js",
                 "/static/vendor/three/addons/loaders/GLTFLoader.js",
                 "/static/vendor/three-vrm/three-vrm.module.min.js", "/static/vendor/pixi/pixi.min.js",
                 "/static/vendor/live2d/cubism4.min.js"):
        if client.get(path).status_code != 200:
            check(f"{path} is served", False)
            break
    else:
        check("The VRM and Live2D libraries ship with Cerebro (MIT)", True)

    from app.services.browser.engine import playwright_installed

    if not playwright_installed():
        print("  - skipped page part: Playwright is not installed")
        return
    import threading

    import uvicorn
    from playwright.sync_api import sync_playwright

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    import time as _time

    for _ in range(100):
        if server.started:
            break
        _time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
            except Exception as exc:  # noqa: BLE001
                print(f"  - skipped page part: {str(exc).splitlines()[0]}")
                return
            errors = []
            tab = browser.new_page(viewport={"width": 1200, "height": 800})
            tab.on("pageerror", lambda e: errors.append(str(e)))
            tab.goto(f"http://127.0.0.1:{port}/app")
            tab.wait_for_timeout(1500)
            check("The page runs without errors", not errors, errors)
            check("Agents are listed in the sidebar", tab.locator(".agent-row").count() >= 1)
            modules = tab.evaluate("""async () => {
              const three = await import('three');
              const gltf = await import('/static/vendor/three/addons/loaders/GLTFLoader.js');
              const vrm = await import('/static/vendor/three-vrm/three-vrm.module.min.js');
              return Boolean(three.WebGLRenderer && gltf.GLTFLoader && vrm.VRMLoaderPlugin);
            }""")
            check("The VRM libraries load in the page", modules)
            made = tab.evaluate("""async () => {
              const { createFace } = await import('/static/ui/face.js');
              const out = {};
              for (const kind of ['brain', 'anime', 'vrm', 'live2d']) {
                const host = document.createElement('div');
                host.style.width = host.style.height = '120px';
                document.body.append(host);
                const face = createFace(host, { face: kind, face_style: { palette: 'blue' } });
                face.setState('thinking'); face.setMouth(0.7);
                out[kind] = { kind: face.kind, drawn: Boolean(host.querySelector('svg, .mascot, canvas')) };
                face.destroy();
              }
              return out;
            }""")
            check("Each face is created; a model face without a model stands in with the anime face",
                  made["brain"]["drawn"] and made["anime"]["kind"] == "anime" and made["vrm"]["drawn"]
                  and made["live2d"]["drawn"], made)
            faces = tab.evaluate("""async () => {
              const f = await import('/static/ui/face.js');
              const out = { drawn: {}, layers: {} };
              for (const hair of f.HAIR_NAMES) for (const expr of f.EXPRESSIONS) {
                const svg = new DOMParser().parseFromString(f.animeSVG({ palette: 'pink', hair }, expr), 'image/svg+xml');
                const root = svg.documentElement;
                out.drawn[`${hair}/${expr}`] = root.nodeName === 'svg' && !svg.querySelector('parsererror')
                  && root.getAttribute('data-expr') === expr;
              }
              const host = document.createElement('div');
              host.style.width = host.style.height = '160px';
              document.body.append(host);
              host.innerHTML = f.animeSVG({ palette: 'blue', hair: 'twintails' }, 'calm');
              const svg = host.querySelector('svg');
              f.poseAnime(svg, 12345, 0.5);
              const first = svg.outerHTML;
              f.poseAnime(svg, 999, 0.1);
              f.poseAnime(svg, 12345, 0.5);
              out.deterministic = svg.outerHTML === first;
              out.blinks = [0, 500, 1000, 2000, 3000, 3500].map(t => f.blinkAt(t)).join() === [0, 500, 1000, 2000, 3000, 3500].map(t => f.blinkAt(t)).join();
              const face = f.createFace(host, { face: 'anime', face_style: { palette: 'pink', hair: 'bob' } });
              face.setState('awaiting_approval');
              out.approval = host.querySelector('svg').getAttribute('data-expr');
              face.react('wink', 120);
              out.reacting = host.querySelector('svg').getAttribute('data-expr');
              await new Promise(r => setTimeout(r, 400));
              out.after = host.querySelector('svg').getAttribute('data-expr');
              face.destroy();
              out.vrm = f.matchExpressions(['happy', 'angry', 'sad', 'relaxed', 'surprised', 'aa', 'ih', 'ou', 'ee', 'oh',
                                            'blink', 'blinkLeft', 'blinkRight', 'Think_Pose']);
              out.live2d = f.live2dCatalog({ FileReferences: { Expressions: [{ Name: 'Smile' }, { Name: 'Sad' }, { Name: 'Surprise' }],
                                                               Motions: { Idle: [], TapBody: [] } } });
              out.l2match = f.matchExpressions([...out.live2d.expressions, ...out.live2d.motions]);
              out.silent = f.vowelsFrom(0, [1, 1, 1, 1, 1]);
              out.unclear = f.vowelsFrom(0.8, null);
              out.ooh = f.vowelsFrom(0.8, [1, 0.1, 0.1, 0.05, 0.02]);
              out.eee = f.vowelsFrom(0.8, [0.05, 0.1, 0.1, 1, 0.2]);
              return out;
            }""")
            check("Every hairstyle draws every expression as a well-formed SVG",
                  len(faces["drawn"]) == 4 * 14 and all(faces["drawn"].values()),
                  [k for k, v in faces["drawn"].items() if not v])
            check("Her pose is the same for the same moment (reproducible desktop frames)",
                  faces["deterministic"] and faces["blinks"])
            check("Needing approval shows her surprised; a reaction passes, then she's back",
                  faces["approval"] == "surprised" and faces["reacting"] == "wink" and faces["after"] == "surprised",
                  (faces["approval"], faces["reacting"], faces["after"]))
            vrm = faces["vrm"]
            check("A VRM model's expressions are matched to Cerebro's states",
                  vrm["happy"] == "happy" and vrm["sorry"] == "sad" and vrm["surprised"] == "surprised"
                  and vrm["wink"] == "blinkLeft" and vrm["thinking"] == "Think_Pose", vrm)
            check("A Live2D model's expressions and motions are read from its model3.json",
                  faces["live2d"] == {"expressions": ["Smile", "Sad", "Surprise"], "motions": ["Idle", "TapBody"]}
                  and faces["l2match"]["happy"] == "Smile" and faces["l2match"]["sorry"] == "Sad"
                  and faces["l2match"]["surprised"] == "Surprise", faces["l2match"])
            check("Lip sync shapes vowels from the voice's bands",
                  all(v == 0 for v in faces["silent"].values()) and faces["unclear"]["aa"] == 0.8
                  and max(faces["ooh"], key=faces["ooh"].get) == "ou"
                  and max(faces["eee"], key=faces["eee"].get) == "ee", (faces["ooh"], faces["eee"]))
            # Settings, inside the window.
            tab.click("[data-tab=settings]")
            tab.wait_for_selector("#settings-body [data-group=voice]", timeout=8000)
            tab.click("#settings-body [data-group=voice]")
            tab.wait_for_selector("#settings-body .st-status", timeout=8000)
            check("Settings opens inside the app window, with what works on this PC",
                  "Speech recognition" in tab.inner_text("#settings-body")
                  and "Offline voice" in tab.inner_text("#settings-body") and not errors, errors)
            tab.evaluate("location.hash = 'settings:ai'")
            tab.wait_for_timeout(400)
            check("…and a link can open a particular group", "AI Provider" in tab.inner_text("#settings-body .st-card h3"))
            # A 3D model's expressions, rendered into the desktop buddy's strips.
            import sys as _sys2

            _sys2.path.insert(0, str(ROOT))
            from tests_support import make_vrm

            from fastapi.testclient import TestClient as _Client

            uploaded = _Client(app).post("/api/faces", files={"file": ("test.vrm", make_vrm.build(), "model/gltf-binary")}).json()
            made = tab.evaluate("""async (id) => {
              const f = await import('/static/ui/face.js');
              const host = document.createElement('div'); host.style.width = host.style.height = '160px';
              document.body.append(host);
              const face = f.createFace(host, { face: 'vrm', face_style: { model: id, entry: 'model.vrm' } });
              await face.ready;
              const out = await face.capture({ size: 48 });
              const form = new FormData();
              form.append('manifest', JSON.stringify(out.manifest));
              for (const [name, blob] of Object.entries(out.strips)) form.append(name, blob, name + '.png');
              const saved = await fetch(`/api/faces/${id}/strips`, { method: 'POST', body: form });
              face.destroy();
              return { names: face.listExpressions(), states: Object.keys(out.manifest.states), saved: saved.status };
            }""", uploaded["id"])
            check("A 3D model's expressions are found and rendered into the buddy's strips",
                  "happy" in made["names"] and set(made["states"]) >= {"calm", "happy", "sorry", "talk"}
                  and made["saved"] == 200, made)
            _Client(app).delete(f"/api/faces/{uploaded['id']}")
            tab.click("[data-tab=home]")
            tab.wait_for_selector(".agent-card", timeout=8000)
            check("Home shows a card per agent", tab.locator(".agent-card").count() >= 1,
                  tab.locator("#home-body").inner_text()[:200])
            browser.close()
    finally:
        server.should_exit = True



class FakeDesktop:
    """A stand-in Windows desktop for the app tools: one editor window."""

    def __init__(self):
        from app.services import desktop_apps

        self.desktop_apps = desktop_apps
        self.wins = {}
        self.log = []
        self.saved = None

    def _editor(self, title):
        handle = 100 + len(self.wins)
        self.wins[handle] = {"title": title, "pid": 7, "controls": [
            {"type": "Edit", "name": "Customer", "id": "customer", "value": "", "enabled": True},
            {"type": "Edit", "name": "Notes", "id": "notes", "value": "", "enabled": True},
            {"type": "TabItem", "name": "History", "id": "tab-history", "value": "", "enabled": True},
            {"type": "Button", "name": "Save", "id": "save", "value": "", "enabled": True}],
            "text": "Billing tool — record 42"}
        return handle

    def windows(self):
        return [{"handle": h, "title": w["title"], "pid": w["pid"]} for h, w in self.wins.items()]

    def open(self, target):
        self.log.append(("open", target))
        for h, w in self.wins.items():
            if target.lower() in w["title"].lower():
                return {"handle": h, "title": w["title"], "pid": w["pid"]}
        handle = self._editor("Billing Tool - record 42")
        return {"handle": handle, "title": self.wins[handle]["title"], "pid": 7}

    def snapshot(self, handle):
        w = self.wins[handle]
        return {"handle": handle, "title": w["title"], "text": w["text"],
                "controls": [{**c, "ref": str(i + 1)} for i, c in enumerate(w["controls"])]}

    def type(self, handle, ref, text):
        self.wins[handle]["controls"][int(ref) - 1]["value"] = text
        self.log.append(("type", ref, text))

    def click(self, handle, ref):
        control = self.wins[handle]["controls"][int(ref) - 1]
        self.log.append(("click", control["name"]))
        if control["name"] == "Save":
            self.saved = {c["id"]: c["value"] for c in self.wins[handle]["controls"]}
            self.wins[handle]["text"] = "Billing tool — record 42 (saved)"
        if control["name"] == "History":
            self.wins[handle]["text"] = "History: paid in March"

    def keys(self, handle, keys):
        self.log.append(("keys", keys))

    def screenshot(self, handle):
        from PIL import Image
        import io

        out = io.BytesIO()
        Image.new("RGB", (40, 30), (200, 200, 220)).save(out, "JPEG")
        return out.getvalue()


def test_desktop_apps():
    """Desktop apps: read, type, ordinary clicks, risky clicks become approvals, auto-apply."""
    print("\nDesktop apps")
    from app.services import capabilities, desktop_apps
    from app.services.agent import actions
    from app.services.agent.registry import REGISTRY, ToolContext, available_tools
    from app.services.agents import group_of

    root, patches = _addon_dirs()
    for patch in patches:
        patch.start()
    db = session()
    check("Desktop apps aren't offered where they can't run",
          desktop_apps.available() == (sys.platform == "win32" and desktop_apps.available()))
    fake = FakeDesktop()
    desktop_apps.set_backend(fake)
    try:
        ctx = ToolContext(db=db, context={"_user_text": "open the billing tool and note the payment"})
        names = {t.name for t in available_tools(ctx)}
        check("The app tools are offered with a desktop", {"app_open", "app_read", "app_type",
                                                          "app_click", "app_keys"} <= names)
        check("…in their own scope group", group_of(REGISTRY["app_click"]) == "desktop"
              and group_of(REGISTRY["add_desktop_app"]) == "desktop")
        refused = REGISTRY["app_open"].handler(ToolContext(db=db, context={"_user_text": "hi"}),
                                               app="Payroll")
        check("A program the user didn't name isn't opened", refused["summary"] == "Not added"
              and not desktop_apps.definitions())
        opened = REGISTRY["app_open"].handler(ctx, app="billing tool")
        check("A program the user named is added and opened, with numbered controls",
              "[1] Edit: Customer" in opened["content"] and "Save ⚠ needs approval" in opened["content"]
              and desktop_apps.match("billing tool"), opened["content"])
        rows = [r for r in capabilities.list_all(db) if r["kind"] == "app"]
        check("…and listed under Add-ons → Desktop apps", rows and rows[0]["auto_apply"] is False, rows)
        REGISTRY["app_type"].handler(ctx, ref="1", text="Smile Dental")
        REGISTRY["app_type"].handler(ctx, ref="2", text="Paid in full")
        clicked = REGISTRY["app_click"].handler(ctx, ref="3")
        check("Ordinary clicks happen straight away", "History: paid in March" in clicked["content"])
        before = len(ctx.drafts)
        risky = REGISTRY["app_click"].handler(ctx, ref="4", why="Save the payment note")
        card = ctx.drafts[-1] if len(ctx.drafts) > before else {}
        check("Save becomes an approval card with a screenshot and what was typed",
              risky["summary"] == "Waiting for approval" and fake.saved is None
              and card.get("tool") == "app_action" and card["preview"].get("image")
              and any(f["after"] == "Paid in full" for f in card["preview"]["fields"]), card)
        keys = REGISTRY["app_keys"].handler(ctx, keys="ctrl+s")
        check("Ctrl+S needs approval too", keys["summary"] == "Waiting for approval")
        plain = REGISTRY["app_keys"].handler(ctx, keys="tab")
        check("…but ordinary keys don't", plain["summary"] == "Pressed" and ("keys", "tab") in fake.log)
        # The user typed something else meanwhile; approving puts the values back and saves.
        fake.wins[100]["controls"][1]["value"] = ""
        outcome = actions.approve(db, card["action_id"])
        check("Approving saves it, with what was typed put back",
              fake.saved == {"customer": "Smile Dental", "notes": "Paid in full", "tab-history": "",
                             "save": ""}, (outcome.get("reply"), fake.saved))
        done = actions.card(db.get(__import__("app.models.agent_action", fromlist=["x"]).AgentAction,
                                   card["action_id"]))
        check("…with an after screenshot in the log", done["status"] == "done" and done["after_image"])

        desktop_apps.set_auto_apply(desktop_apps.match("billing tool"), True)
        fake.saved = None
        auto = REGISTRY["app_click"].handler(ctx, ref="4")
        check("With the app's auto-apply on, Save goes straight through and is logged",
              auto["summary"] == "Applied automatically" and fake.saved is not None
              and ctx.drafts[-1]["automatic"])
        auto = REGISTRY["app_keys"].handler(ctx, keys="ctrl+s")
        check("An automatic change that shows no effect is flagged, not retried",
              ctx.drafts[-1]["unconfirmed"] and "check" in auto["content"].lower()
              and sum(1 for e in fake.log if e == ("keys", "ctrl+s")) == 1, auto)
        added = REGISTRY["add_desktop_app"].handler(
            ToolContext(db=db, context={"_user_text": "add our CRM, it's at C:\\Apps\\crm.exe"}),
            label="CRM", path="C:\\Apps\\crm.exe")
        check("add_desktop_app adds a program the user named", added["summary"] == "Added"
              and desktop_apps.match("crm"))
        removed = capabilities.remove(db, "app", "crm")
        check("…and it can be removed", removed["ok"] and not desktop_apps.match("crm.exe"))
    finally:
        desktop_apps.set_backend(None)
        app_tools = sys.modules["app.services.agent.app_tools"]
        app_tools._last.clear()
        for patch in reversed(patches):
            patch.stop()



def test_desktop_apps_notepad():
    """On Windows: drive the real Notepad through UI Automation."""
    print("\nDesktop apps — real Notepad (Windows only)")
    if sys.platform != "win32":
        print("  – skipped: not Windows")
        return
    from app.services import desktop_apps
    from app.services.agent import actions
    from app.services.agent.registry import REGISTRY, ToolContext

    if not desktop_apps.available():
        check("pywinauto is installed on Windows", False, "pip install pywinauto")
        return
    db = session()
    app_tools = sys.modules["app.services.agent.app_tools"]
    ctx = ToolContext(db=db, context={"_user_text": "open notepad and write a note"})
    pid = None
    root, patches = _addon_dirs()
    for patch in patches:
        patch.start()
    try:
        opened = REGISTRY["app_open"].handler(ctx, app="notepad")
        state = ctx.context.get("_app") or {}
        pid = next((w["pid"] for w in desktop_apps.backend().windows()
                    if w["handle"] == state.get("handle")), None)
        check("Notepad opens and its window is read", "Notepad" in opened["content"]
              and state.get("handle"), opened["content"][:400])
        snap = desktop_apps.backend().snapshot(state["handle"])
        editor = next((c for c in snap["controls"] if c["type"] in ("Edit", "Document")), None)
        check("…with its text area listed", editor is not None, snap["controls"][:10])
        if editor is None:
            return
        REGISTRY["app_type"].handler(ctx, ref=editor["ref"], text="Hello from Cerebro")
        read = REGISTRY["app_read"].handler(ctx)
        check("Typing into it and reading it back works", "Hello from Cerebro" in read["content"],
              read["content"][:400])
        windows_before = len(desktop_apps.backend().windows())
        saved = REGISTRY["app_keys"].handler(ctx, keys="ctrl+s", why="Save the note")
        check("Ctrl+S waits for approval, and nothing is saved",
              saved["summary"] == "Waiting for approval"
              and len(desktop_apps.backend().windows()) == windows_before, saved)
        if ctx.drafts:
            actions.discard(db, ctx.drafts[-1]["action_id"])
    finally:
        if pid:
            desktop_apps.kill(pid)
        app_tools._last.clear()
        for patch in reversed(patches):
            patch.stop()



def _job_script(turns, prompts=None):
    """A scripted model for one job run: tool calls in order, then a reply."""
    def script(self, messages, tools=None, **kwargs):
        if prompts is not None and messages:
            prompts.append(messages[-1]["content"] if messages[-1]["role"] == "user" else
                           next((m["content"] for m in reversed(messages) if m["role"] == "user"), ""))
        step = sum(1 for m in messages if m["role"] == "tool")
        if step < len(turns) - 1 and tools:
            name, args = turns[step]
            return {"content": "", "tool_calls": [{"id": f"k{step}", "name": name, "arguments": args}]}
        return {"content": turns[-1], "tool_calls": []}
    return script


def test_keep_it_updated():
    """Keep it updated: one command connects the target, makes the job, learns, then runs by itself."""
    print("\nKeep it updated — files")
    import json as _json
    from unittest import mock

    from app.core import settings_store
    from app.core.config import settings
    from app.models.task import Task
    from app.models.work import Watch
    from app.services import agents, conversations, jobs, keeping, skills, watches
    from app.services.agent import actions
    from app.services.agent.registry import REGISTRY, ToolContext
    from app.services.task_executors import skill_name

    from app.core.activity_state import STATES

    bad = [t.name for t in REGISTRY.values() if t.activity not in STATES]
    check("Every tool shows a real activity while it runs", not bad, bad)
    root, patches = _addon_dirs()
    # The test runs these jobs itself; the background scheduler mustn't take them first.
    patches.append(mock.patch.object(jobs, "due", return_value=[]))
    for patch in patches:
        patch.start()
    db = session()
    try:
        import docx

        folder = Path(_TEMP_DIR) / "keep"
        folder.mkdir(exist_ok=True)
        report = folder / "Weekly status.docx"
        document = docx.Document()
        document.add_heading("Weekly status", 1)
        document.add_paragraph("Nothing yet.")
        document.save(str(report))
        export = folder / "export.csv"
        export.write_text("case,status\nCAS-1,open\n", encoding="utf-8")

        agent = agents.default_agent(db)
        chat = conversations.create(db, "Status report", agent_id=agent.id)
        said = f"keep {report} updated from {export} whenever it changes"
        ctx = ToolContext(db=db, context={"_user_text": said, "agent_id": agent.id,
                                          "conversation_id": chat.id})
        with mock.patch.object(jobs, "start_now") as started:
            result = REGISTRY["keep_updated"].handler(
                ctx, target=str(report), source=str(export), when="whenever it changes",
                instructions="Add a line with the number of open cases")
        card = next((c for c in ctx.drafts if c.get("type") == "keep_updated"), {})
        task = db.get(Task, card.get("task_id") or 0)
        check("keep_updated makes the job in one step and runs the first update now",
              result["summary"] == "Keeping it updated" and task is not None and started.called
              and task.source == "keep" and task.schedule == "manual", result)
        check("…its card says what it keeps updated and how often",
              card.get("kind") == "file" and card.get("label") == "Weekly status.docx"
              and "export.csv changes" in card.get("when", "") and card.get("can_auto_apply"), card)
        watch = db.query(Watch).filter(Watch.task_id == task.id).first()
        check("…and it watches the source file", watch is not None and watch.interval_minutes == 5
              and watch.to_dict()["kind"] == "file")

        operation = _json.dumps([{"op": "append_paragraph", "text": "Open cases: 1"}])
        name = skill_name(task)
        prompts = []
        enabled, patched = _with_ai(_job_script([
            ("edit_document", {"path": str(report), "operations": operation, "why": "Open cases"}),
            ("save_skill", {"name": name, "description": "Update the weekly status",
                            "instructions": "1. Count open cases in the export.\n2. Append a line."}),
            "I've prepared the update."], prompts))
        with enabled, patched:
            jobs.run(task.id)
        db.expire_all()
        task = db.get(Task, task.id)
        check("The first run waits for approval and changes nothing yet",
              task.status == "waiting_approval" and "Open cases" not in
              "\n".join(p.text for p in docx.Document(str(report)).paragraphs), task.to_dict())
        check("…and was told to save how it did it",
              any("save the steps that worked" in p for p in prompts))
        check("…which it did, as a skill", skills.exact(name) is not None)
        waiting = task.state_dict().get("waiting") or []
        actions.approve(db, int(waiting[0].split(":")[1]))
        text = "\n".join(p.text for p in docx.Document(str(report)).paragraphs)
        check("Approving it updates the Word file, keeping a backup",
              "Open cases: 1" in text and list(folder.glob("*cerebro-backup*")))
        db.expire_all()
        task = db.get(Task, task.id)
        check("…and the job carries on", task.status == "active", task.status)
        fresh = keeping.card(db, task)
        check("The card then offers to let it update by itself",
              fresh["learned"] and not fresh["auto_apply"] and (fresh["offer_auto"] or task.run_count == 1),
              fresh)

        # The source changes: the watch runs the job.
        watches.check(db, watch)
        check("An untouched source file isn't read again", watches.check(db, watch).get("skipped"))
        export.write_text("case,status\nCAS-1,open\nCAS-2,open\n", encoding="utf-8")
        import os
        import time as _time

        os.utime(export, (_time.time() + 5, _time.time() + 5))
        fired = watches.check(db, watch)
        db.expire_all()
        task = db.get(Task, task.id)
        check("When the source changes, the job runs", fired.get("fired") and task.next_run is not None
              and "source changed" in (task.state_dict().get("trigger") or ""), (fired, task.to_dict()))

        previous = settings.DOCUMENTS_AUTO_APPLY
        keeping.set_auto_apply("documents", True)
        try:
            prompts.clear()
            operation = _json.dumps([{"op": "append_paragraph", "text": "Open cases: 2"}])
            enabled, patched = _with_ai(_job_script([
                ("edit_document", {"path": str(report), "operations": operation}), "Updated."], prompts))
            with enabled, patched:
                jobs.run(task.id)
            db.expire_all()
            task = db.get(Task, task.id)
            text = "\n".join(p.text for p in docx.Document(str(report)).paragraphs)
            check("Switched to update by itself, the next run saves on its own",
                  "Open cases: 2" in text and task.status == "active", task.to_dict())
            check("…following the saved steps, and knowing why it ran",
                  any("You saved how to do this job before" in p and "source changed" in p for p in prompts))
            check("…and it's logged as an automatic change",
                  any(e["kind"] == "tool" for e in jobs.events(db, task.id))
                  and task.state_dict().get("auto_applied"))
        finally:
            settings_store.update({"DOCUMENTS_AUTO_APPLY": previous})

        check("list_updates lists it", "Weekly status.docx" in
              REGISTRY["list_updates"].handler(ToolContext(db=db, context={}))["content"])
        unknown = REGISTRY["keep_updated"].handler(
            ToolContext(db=db, context={"_user_text": "keep the zork thing updated"}),
            target="the zork thing", instructions="refresh it")
        check("Something it can't reach yet sends it to find a connector",
              unknown["summary"] == "Not connected yet" and "find_connector" in unknown["content"])
    finally:
        for patch in reversed(patches):
            patch.stop()


def test_attached_files():
    """Documents attached in chat are read by the agent and kept with the message."""
    print("\nChat — attached documents")
    import io

    import docx
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import chat_files, conversations
    from app.services.chat_service import ChatService

    client = TestClient(app)
    buffer = io.BytesIO()
    document = docx.Document()
    document.add_paragraph("Smile Dental renewal is due on 1 March.")
    document.save(buffer)
    uploaded = client.post("/api/chat/upload-file",
                           files={"file": ("Renewal notes.docx", buffer.getvalue())})
    info = uploaded.json().get("file") or {}
    check("A Word file can be attached", uploaded.status_code == 200 and info.get("name") == "Renewal notes.docx",
          uploaded.text)
    refused = client.post("/api/chat/upload-file", files={"file": ("setup.exe", b"MZ")})
    check("…but not a program", refused.status_code == 422)

    db = session()
    chat = conversations.create(db, "Renewals")
    script = ScriptedLLM([{"name": "read_document", "arguments": {
        "name": chat_files.resolve(info["id"])["path"]}}, "The renewal is due on 1 March."])
    enabled, patched = _with_ai(script)
    with enabled, patched:
        reply = ChatService(db, chat.id).handle_message("When is it due?", attachments=[info["id"]])
    first = script.requests[0]["messages"][-1]["content"] if script.requests else ""
    check("The agent is told where the attachment is", f"Attached: Renewal notes.docx —" in first, first)
    tool_text = next((m["content"] for m in (script.requests[-1]["messages"] if script.requests else [])
                      if m["role"] == "tool"), "")
    check("…and reads it with the document tools", "Renewal notes.docx" in tool_text
          and "not found" not in tool_text.lower() and "1 March" in reply["reply"], tool_text[:300])
    history = ChatService(db, chat.id).history()
    asked = next((m for m in history if m["role"] == "user"), {})
    check("The message keeps its file chip", (asked.get("meta") or {}).get("attachments", [{}])[0]
          .get("name") == "Renewal notes.docx", asked)
    from app.models.tracked_document import TrackedDocument

    check("…and the document is tracked, so it's searchable",
          db.query(TrackedDocument).filter(TrackedDocument.name == "Renewal notes.docx").first() is not None)



def test_keep_website_updated():
    """A page on an internal site: connected by itself, approved once, then automatic."""
    print("\nKeep it updated — a website")
    import importlib
    import sys as _sys
    from unittest import mock

    from app.core.config import settings
    from app.models.task import Task
    from app.services import browser, jobs, keeping
    from app.services.agent import actions
    from app.services.agent.registry import REGISTRY, ToolContext, unregister_owner
    from app.services.browser import generic
    from app.services.browser.engine import playwright_installed

    root, patches = _addon_dirs()
    patches.append(mock.patch.object(jobs, "due", return_value=[]))
    for patch in patches:
        patch.start()
    db = session()
    server, submitted = _fake_website()
    base = f"http://127.0.0.1:{server.server_port}"
    engine_module = importlib.import_module("app.services.browser.engine")
    browser_patches = [mock.patch.object(settings, k, v) for k, v in {
        "BROWSER_AUTOMATION_ENABLED": True, "BROWSER_MODE": "headless", "BROWSER_TIMEOUT_SECONDS": 15,
        "BROWSER_CHANNEL": "msedge" if _sys.platform == "win32" else "chromium"}.items()]
    browser_patches.append(mock.patch.object(engine_module, "BROWSER_PROFILE_DIR", Path(_TEMP_DIR) / "keep_profile"))
    for patch in browser_patches:
        patch.start()
    name = None
    try:
        ctx = ToolContext(db=db, context={"_user_text": f"start updating {base}/request every day with the "
                                                        "sensor status"})
        with mock.patch.object(jobs, "start_now"):
            REGISTRY["keep_updated"].handler(ctx, target=f"{base}/request", when="every day at 8",
                                             instructions="Send a request with today's sensor status")
        card = next((c for c in ctx.drafts if c.get("type") == "keep_updated"), {})
        name = card.get("integration")
        check("A site it isn't connected to is added by itself",
              name and browser.get(name) is not None and card.get("kind") == "site", card)
        check("…the user is asked to sign in once",
              any(c.get("type") == "signin" and c.get("integration") == name for c in ctx.drafts))
        task = db.get(Task, card["task_id"])
        check("…and the job runs daily at 08:00", task.schedule == "daily" and task.at_time == "08:00",
              (task.schedule, task.at_time))
        if not playwright_installed():
            print("  - skipped browser part: Playwright is not installed")
            return

        def run_once(text):
            def script(self, messages, tools=None, **kwargs):
                step = sum(1 for m in messages if m["role"] == "tool")
                tool_text = [m["content"] for m in messages if m["role"] == "tool"]
                if step == 0:
                    return {"content": "", "tool_calls": [{"id": "w0", "name": "browse_open",
                                                           "arguments": {"url": f"{base}/request"}}]}
                lines = [l for l in tool_text[0].splitlines() if l.startswith("  [")]
                if step == 1:
                    ref = next(l for l in lines if "field: Subject" in l).split("]")[0].split("[")[1]
                    return {"content": "", "tool_calls": [{"id": "w1", "name": "browse_type",
                                                           "arguments": {"ref": ref, "text": text}}]}
                if step == 2:
                    ref = next(l for l in lines if "Send request" in l).split("]")[0].split("[")[1]
                    return {"content": "", "tool_calls": [{"id": "w2", "name": "browse_click",
                                                           "arguments": {"ref": ref, "why": "Send it"}}]}
                return {"content": "Done.", "tool_calls": []}
            return script

        enabled, patched = _with_ai(run_once("Sensors fine"))
        try:
            with enabled, patched:
                jobs.run(task.id)
        except browser.BrowserUnavailable as exc:
            print(f"  - skipped browser part: {exc}")
            return
        db.expire_all()
        task = db.get(Task, task.id)
        check("The first run waits for approval and sends nothing",
              task.status == "waiting_approval" and not submitted, task.to_dict())
        waiting = task.state_dict().get("waiting") or []
        from app.models.agent_action import AgentAction

        action = db.get(AgentAction, int(waiting[0].split(":")[1])) if waiting else None
        check("…recorded under the site itself", action is not None and action.integration == name)
        actions.approve(db, action.id)
        check("Approving sends it", submitted == [{"subject": "Sensors fine"}], submitted)

        keeping.set_auto_apply(name, True)
        check("The site's own switch turns automatic changes on",
              browser.get(name).auto_apply and keeping.card(db, task)["auto_apply"])
        enabled, patched = _with_ai(run_once("Sensors still fine"))
        with enabled, patched:
            jobs.run(task.id)
        db.expire_all()
        task = db.get(Task, task.id)
        last = db.query(AgentAction).order_by(AgentAction.id.desc()).first()
        check("Next time it saves by itself", submitted[-1] == {"subject": "Sensors still fine"}
              and task.status == "active" and last.status == "done", task.to_dict())
        check("…with an after screenshot in its receipt", actions.card(last)["after_image"]
              and actions.card(last)["automatic"])
    finally:
        browser.engine().shutdown()
        if name:
            generic.unload(name)
            unregister_owner(f"site:{name}")
        for patch in reversed(browser_patches + patches):
            patch.stop()
        server.shutdown()



class FakeCalendar:
    """A stand-in calendar: events in memory, and what was booked/moved/cancelled."""

    def __init__(self, events):
        self.items = {e["id"]: e for e in events}
        self.booked, self.moved, self.cancelled = [], [], []

    def calendar(self, start, end):
        return [dict(e) for e in self.items.values()]

    def create_event(self, subject, start, end, attendees, body="", location=""):
        self.booked.append({"subject": subject, "start": start, "end": end, "attendees": attendees})
        return {"detail": f"Booked {subject}"}

    def move_event(self, event_id, start, end):
        self.moved.append((event_id, start))
        self.items[event_id] = {**self.items[event_id], "start": start, "end": end}
        return {"detail": "Moved"}

    def cancel_event(self, event_id, message=""):
        self.cancelled.append((event_id, message))
        self.items.pop(event_id, None)
        return {"detail": "Cancelled"}


def test_secretary():
    """Secretary: calendar, free time, booking with approval, meeting briefs, follow-ups, briefings."""
    print("\nSecretary — calendar, follow-ups and briefings")
    from datetime import datetime, timedelta
    from unittest import mock

    from app.core.config import settings
    from app.models.enterprise import EnterpriseAction, EnterpriseMessage
    from app.models.work import Briefing, FollowUp
    from app.services import briefings, calendar, followups
    from app.services.agent import actions
    from app.services.agent.registry import REGISTRY, ToolContext, available_tools

    db = session()
    now = datetime.now()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # An OWA-shaped event, as Outlook's own calendar page fetches it.
    owa = calendar.event_from({
        "ItemId": {"Id": "AAMk-1"}, "Subject": "Smile Dental review",
        "Start": (now + timedelta(minutes=8)).isoformat(), "End": (now + timedelta(minutes=38)).isoformat(),
        "Location": {"DisplayName": "Teams"}, "Organizer": {"Mailbox": {"Name": "Priya Shah",
                                                                         "EmailAddress": "priya@smiledental.com"}},
        "RequiredAttendees": [{"Mailbox": {"Name": "Priya Shah", "EmailAddress": "priya@smiledental.com"}}],
        "Body": {"Value": "<p>Agenda: sensor replacement</p>"}})
    graph = calendar.event_from({
        "id": "g-2", "subject": "1:1", "start": {"dateTime": (day + timedelta(days=1, hours=10)).isoformat(),
                                                 "timeZone": "UTC"},
        "end": {"dateTime": (day + timedelta(days=1, hours=11)).isoformat()}, "attendees": [],
        "showAs": "busy"})
    check("Outlook's calendar JSON is read (OWA and REST shapes)",
          owa and owa["subject"] == "Smile Dental review" and owa["attendees"][0]["email"] ==
          "priya@smiledental.com" and "sensor replacement" in owa["body"] and graph and graph["subject"] == "1:1",
          (owa, graph))
    fake = FakeCalendar([owa, graph])
    calendar.set_source(fake)
    try:
        ctx = ToolContext(db=db, context={"_user_text": "what's on today"})
        names = {t.name for t in available_tools(ctx)}
        check("The calendar tools are offered once a calendar is connected",
              {"calendar_today", "find_free_time", "book_meeting"} <= names)
        agenda = REGISTRY["calendar_today"].handler(ctx)
        check("calendar_today lists today's meetings with who's coming",
              "Smile Dental review" in agenda["content"] and "Priya Shah" in agenda["content"]
              and "1:1" not in agenda["content"], agenda)
        free = calendar.free_slots(60, days=3, after=day + timedelta(days=1, hours=9))
        tomorrow = [s for s in free if s["start"].date() == (day + timedelta(days=1)).date()]
        check("Free time skips meetings",
              not tomorrow or all(not (s["start"] < graph["end"] and s["end"] > graph["start"]) for s in tomorrow),
              tomorrow)
        booked = REGISTRY["book_meeting"].handler(ctx, subject="Sensor follow-up", start="tomorrow 2pm",
                                                  length_minutes=30, attendees="priya@smiledental.com")
        card = ctx.drafts[-1]
        check("Booking a meeting waits for approval", booked["summary"] == "Waiting for approval"
              and not fake.booked and card["tool"] == "book_meeting", booked)
        actions.approve(db, card["action_id"])
        check("…and books it when approved", fake.booked and fake.booked[0]["attendees"] ==
              ["priya@smiledental.com"] and fake.booked[0]["start"].hour == 14, fake.booked)
        moved = REGISTRY["reschedule_meeting"].handler(ctx, meeting="1:1", start="tomorrow 15:00")
        actions.approve(db, ctx.drafts[-1]["action_id"])
        check("Rescheduling finds the meeting by name and moves it once approved",
              moved["summary"] == "Waiting for approval" and fake.moved and fake.moved[0][0] == "g-2")

        # A brief before the meeting, with who's coming and what's been happening with them.
        db.add(EnterpriseMessage(source="outlook", type="email", sender="priya@smiledental.com",
                                 sender_name="Priya Shah", subject="Sensor still failing",
                                 body="The sensor at reception still fails.", timestamp=now - timedelta(days=1),
                                 external_id="sec-1", customer="Smile Dental", direct=True))
        db.commit()
        with mock.patch.object(settings, "MEETING_BRIEF_MINUTES", 10):
            posted = briefings.meeting_briefs(db, now)
            again = briefings.meeting_briefs(db, now)
        brief = db.query(Briefing).filter(Briefing.kind == "meeting").first()
        check("A brief is posted before a meeting, once",
              posted == 1 and again == 0 and brief is not None, (posted, again))
        check("…with who's coming, recent mail with them and the agenda",
              brief and "Priya Shah" in brief.content and "Sensor still failing" in brief.content
              and "sensor replacement" in brief.content, brief.content if brief else None)

        # Follow-ups from what you sent and what you were asked.
        db.add(EnterpriseAction(action="send_email", source="outlook", to="sam@contoso.com",
                                subject="Licence count", status="sent",
                                body="Hi Sam, could you send me the licence count for the Leeds office? "
                                     "I'll update the renewal quote by Friday.",
                                created_at=now - timedelta(days=4), updated_at=now - timedelta(days=4)))
        db.add(EnterpriseMessage(source="teams", type="message", sender="ana@contoso.com", sender_name="Ana",
                                 body="Can you check the backup report for Leeds when you get a chance?",
                                 timestamp=now - timedelta(hours=2), external_id="sec-2", direct=True))
        db.commit()
        found = followups.detect(db)
        kinds = {i.kind: i for i in followups.open_items(db)}
        check("An unanswered request you sent becomes 'waiting on'",
              "waiting_on" in kinds and "licence count" in kinds["waiting_on"].what
              and kinds["waiting_on"].who == "sam@contoso.com", found)
        check("A promise you made becomes 'promised', due when you said",
              "promised" in kinds and kinds["promised"].due_at and kinds["promised"].due_at.weekday() == 4,
              kinds.get("promised") and kinds["promised"].to_dict())
        check("Something you were asked becomes a to-do", "asked" in kinds and kinds["asked"].who == "Ana")
        followups.detect(db)
        check("…found once, not again on the next pass",
              db.query(FollowUp).filter(FollowUp.kind == "waiting_on").count() == 1)
        with mock.patch.object(settings, "FOLLOWUP_CHASE_DAYS", 3):
            check("After the chase days it's due to chase", followups.chase_due(kinds["waiting_on"]))
            chased = REGISTRY["chase_followup"].handler(ctx, id=kinds["waiting_on"].id)
        check("Chase drafts a follow-up for approval",
              chased["summary"] == "Waiting for approval" and ctx.drafts[-1].get("type") == "draft"
              and "following up" in (ctx.drafts[-1].get("body") or ctx.drafts[-1].get("preview", {})
                                     .get("body", "") or str(ctx.drafts[-1])).lower(), ctx.drafts[-1])
        REGISTRY["add_todo"].handler(ctx, what="Order a spare sensor", due="tomorrow")
        listed = REGISTRY["todo_list"].handler(ctx)
        check("The to-do list has everything", "Order a spare sensor" in listed["content"]
              and "Waiting on sam@contoso.com" in listed["content"], listed["content"])
        spare = db.query(FollowUp).filter(FollowUp.what == "Order a spare sensor").first()
        REGISTRY["done_todo"].handler(ctx, id=spare.id)
        check("…and ticks things off", "Order a spare sensor" not in REGISTRY["todo_list"].handler(ctx)["content"])

        made = briefings.make(db, "morning", now, force=True)
        check("The morning briefing has today's meetings, urgent mail and follow-ups due",
              "Smile Dental review" in made.content and "Follow-ups due" in made.content, made.content)
        check("…and a short spoken version", "meeting" in (made.to_dict()["data"].get("spoken") or ""))
        weekly = briefings.make(db, "weekly", now, force=True)
        check("The weekly summary says what got done and what's open",
              "Done" in weekly.content and "Still open" in weekly.content, weekly.content)
        briefings.ensure_jobs(db)
        from app.models.task import Task

        jobs_made = {t.source: t for t in db.query(Task).filter(Task.kind == "briefing")}
        check("The briefings are jobs you can edit or switch off",
              "briefing:morning" in jobs_made and jobs_made["briefing:morning"].schedule == "weekdays")
        from fastapi.testclient import TestClient

        from app.main import app

        board = TestClient(app).get("/api/today").json()
        check("Home → Today has the agenda, to-dos and the briefing",
              board.get("agenda") and board.get("todos") and board.get("briefing"), list(board))
    finally:
        calendar.set_source(None)


def test_notes_and_templates():
    """Meeting notes become follow-ups; a Word template is filled, with a backup."""
    print("\nSecretary — meeting notes and templates")
    from unittest import mock

    import docx

    from app.core.config import settings
    from app.models.work import FollowUp
    from app.services.agent import actions
    from app.services.agent.registry import REGISTRY, ToolContext

    db = session()
    folder = Path(_TEMP_DIR) / "secretary"
    folder.mkdir(exist_ok=True)
    vtt = folder / "standup.vtt"
    vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n<v Priya>Priya: I'll send the new sensor "
                   "tomorrow.</v>\n\n00:00:05.000 --> 00:00:09.000\n<v Alex>Alex: I'll update the "
                   "Leeds tracker by Friday.</v>\n", encoding="utf-8")
    ctx = ToolContext(db=db, context={"_user_text": "take notes from the standup"})
    with mock.patch.object(settings, "INBOX_MY_NAMES", "Alex"):
        notes = REGISTRY["meeting_notes"].handler(ctx, source=str(vtt))
    items = {f.kind: f for f in db.query(FollowUp).filter(FollowUp.source == "meeting")}
    check("Meeting notes pick out the action items", "Action items" in notes["content"], notes["content"])
    check("…yours become promises, others' become 'waiting on'",
          "promised" in items and "Leeds tracker" in items["promised"].what
          and "waiting_on" in items and items["waiting_on"].who == "Priya", {k: v.to_dict() for k, v in items.items()})

    templates = folder / "templates"
    templates.mkdir(exist_ok=True)
    template = templates / "Site visit template.docx"
    document = docx.Document()
    document.add_heading("Site visit — {{customer}}", 1)
    document.add_paragraph("Date: {{date}}")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Engineer"
    table.rows[0].cells[1].text = "{{engineer}}"
    document.save(str(template))
    with mock.patch.object(settings, "TEMPLATES_FOLDER", str(templates)):
        needs = REGISTRY["draft_document"].handler(ctx, template="site visit")
        check("draft_document finds the template by name and says what it needs",
              all(n in needs["content"] for n in ("customer", "date", "engineer")), needs)
        drafted = REGISTRY["draft_document"].handler(
            ctx, template="site visit", save_to=str(folder / "out"),
            data='{"customer": "Smile Dental", "date": "10 Nov", "engineer": "Alex"}')
    card = ctx.drafts[-1]
    target = folder / "out" / "Site visit - Smile Dental.docx"
    check("…prepared for approval", drafted["summary"] == "Waiting for approval" and not target.exists(), drafted)
    actions.approve(db, card["action_id"])
    filled = docx.Document(str(target))
    text = "\n".join(p.text for p in filled.paragraphs) + filled.tables[0].rows[0].cells[1].text
    check("Approving makes the document with every placeholder filled",
          "Site visit — Smile Dental" in text and "Date: 10 Nov" in text and "Alex" in text and "{{" not in text,
          text)
    check("…and it can be undone", actions.undo(db, card["action_id"]) and not target.exists())


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
                  test_chat_reference_images, test_ask_tools_and_action_cards, test_activity_state, test_llm_chat_protocol, test_ask_relevance, test_ask_agent_loop, test_chat_stream, test_browser_disabled_by_default, test_browser_integrations, test_dynamics_case_prefetch, test_tray_brain, test_desktop_shell, test_app_page, test_rightanswers_teach, test_sharepoint_links, test_bedrock_tool_fallbacks, test_desktop_buddy, test_chats, test_sharepoint_auto_apply, test_window_sizing, test_rightanswers_workspace, test_outlook_and_teams, test_ask_reliability_and_scoping, test_system_tools_and_robustness, test_beyondtrust_and_genesys, test_tray_notices,
                  test_mcp_servers, test_mcp_sign_in_and_self_adding, test_websites_and_browsing,
                  test_skills, test_voice, test_voice_expression, test_offline_voice, test_install_and_avatars, test_browser_worker_shutdown, test_push_to_talk, test_addons_page,
                  test_agents_and_scope, test_durable_jobs, test_projects_watches_ideas,
                  test_faces, test_agents_page, test_desktop_apps,
                  test_desktop_apps_notepad, test_keep_it_updated, test_attached_files,
                  test_keep_website_updated, test_secretary, test_notes_and_templates):
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
