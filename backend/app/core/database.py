"""SQLAlchemy engine/session wiring, tuned for the default SQLite setup."""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings
from app.core.paths import default_database_url, ensure_data_dirs

ensure_data_dirs()

def _engine_for(url: str):
    kwargs = {"echo": settings.SQLALCHEMY_ECHO, "pool_pre_ping": True}
    if str(url).startswith("sqlite"):
        # FastAPI serves requests from a thread pool, and SQLite objects are
        # bound to the creating thread unless we opt out.
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs.pop("pool_pre_ping")
    return create_engine(url, **kwargs)


#: Why the configured database could not be used, if it could not be. Empty
#: when all is well. Surfaced by :func:`check_database` so the setup wizard and
#: the settings page can explain it instead of showing a stack trace.
ENGINE_ERROR = ""

try:
    engine = _engine_for(settings.DATABASE_URL)
except Exception as _exc:  # noqa: BLE001 - reported, never fatal
    # create_engine imports the driver eagerly, so a PostgreSQL URL with no
    # psycopg installed raises here — at import time, before anything can catch
    # it. Left alone that makes a single bad setting stop Cerebro from starting
    # at all, with no way back in to change it. Fall back to the built-in
    # database so the app still boots and the mistake can be corrected in the UI.
    ENGINE_ERROR = str(_exc)
    engine = _engine_for(default_database_url())

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create any missing tables and columns. Safe to call repeatedly."""
    import app.models  # noqa: F401  (registers every model on Base.metadata)

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """
    Add columns that exist on the models but not yet in the database.

    ``create_all`` only creates missing *tables*, so an install that predates a
    new column would fail with "no such column" on every query touching it.
    Cerebro is a single-file-database desktop app, not a migration-managed
    service, so this narrow catch-up keeps upgrades a no-op for the user.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue

        present = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present or column.primary_key:
                continue
            # Only nullable, default-less columns can be added safely in place.
            if not column.nullable:
                continue

            column_type = column.type.compile(engine.dialect)
            statement = text(
                f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}'
            )
            try:
                with engine.begin() as connection:
                    connection.execute(statement)
            except Exception as exc:  # pragma: no cover - dialect specific
                from app.core import logger

                logger.warn("database", "Could not add missing column", {
                    "table": table.name, "column": column.name, "error": str(exc),
                })


def check_database() -> dict:
    """Connectivity probe used by diagnostics, the settings page and setup."""
    from sqlalchemy import text

    if ENGINE_ERROR:
        return {"ok": False, "detail": _explain(ENGINE_ERROR), "fell_back": True}

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"ok": True, "detail": f"Connected ({engine.dialect.name})"}
    except Exception as exc:  # pragma: no cover - depends on local environment
        return {"ok": False, "detail": _explain(str(exc))}


def _explain(message: str) -> str:
    """Turn a driver or connection error into something actionable."""
    lowered = message.lower()
    if "psycopg" in lowered and ("no module named" in lowered or "modulenotfound" in lowered):
        return ("The PostgreSQL driver is not installed, so Cerebro is using its "
                "built-in database for now. Install the driver from the setup "
                "wizard, or run Cerebro's setup again — then choose PostgreSQL.")
    if "could not translate host name" in lowered or "name or service not known" in lowered:
        return f"That database server name could not be found. {message}"
    if "connection refused" in lowered:
        return ("Nothing is listening at that address. Check the database server "
                "is running and the host and port are right.")
    if "password authentication failed" in lowered or "authentication" in lowered:
        return "The database rejected that username or password."
    if "does not exist" in lowered and "database" in lowered:
        return "That database name does not exist on the server yet."
    return message
