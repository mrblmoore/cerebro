#!/usr/bin/env python3
"""
Cerebro test suite.

Runs against a throwaway SQLite database and needs no external services, no API
key and no running server:

    python test_mvp.py
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

# Point every component at a temporary database before the app imports settings.
_TEMP_DIR = tempfile.mkdtemp(prefix="cerebro-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_TEMP_DIR).as_posix()}/test.db"
os.environ["LOG_TO_STDOUT"] = "false"
os.environ["CEREBRO_LOG_PATH"] = str(Path(_TEMP_DIR) / "test.log")
os.environ["LLM_PROVIDER"] = "none"
os.environ["VECTOR_BACKEND"] = "local"

from app.core.database import SessionLocal, init_db  # noqa: E402
from app.schemas.event import EventCreate  # noqa: E402
from app.services import embeddings  # noqa: E402
from app.services.context_engine import ContextEngine  # noqa: E402
from app.services.event_detector import EventDetector  # noqa: E402
from app.services.llm_service import LLMService  # noqa: E402
from app.services.rag_service import RAGService  # noqa: E402

PASSED = []
FAILED = []


def check(label, condition):
    (PASSED if condition else FAILED).append(label)
    print(f"  {'✓' if condition else '✗'} {label}")


def session():
    return SessionLocal()


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


# --------------------------------------------------------------------- llm
def test_llm_disabled():
    print("\nAI provider (disabled)")
    llm = LLMService()
    check("Reports itself disabled", llm.enabled is False)
    check("Status is not an error", llm.status()["ok"] is True)

    summary = llm.generate_case_summary({"customer": "Contoso", "title": "Outlook issue"})
    check("Generation returns guidance, not an exception", "Settings" in summary)


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


# -------------------------------------------------------------------- main
def main() -> int:
    print("Running Cerebro tests…")
    init_db()

    for suite in (test_event_detector, test_context_engine, test_event_flow,
                  test_embeddings, test_knowledge_search, test_llm_disabled,
                  test_embedding_signature_matches_vector, test_schema_upgrade,
                  test_customer_from_title, test_database_password_masking,
                  test_settings_store):
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
