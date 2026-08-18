"""SQLAlchemy engine/session wiring, tuned for the default SQLite setup."""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings
from app.core.paths import ensure_data_dirs

ensure_data_dirs()

_engine_kwargs = {"echo": settings.SQLALCHEMY_ECHO, "pool_pre_ping": True}

if settings.using_sqlite:
    # FastAPI serves requests from a thread pool, and SQLite objects are bound
    # to the creating thread unless we opt out.
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
    _engine_kwargs.pop("pool_pre_ping")

engine = create_engine(settings.DATABASE_URL, **_engine_kwargs)

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
    """Lightweight connectivity probe used by the diagnostics endpoint."""
    from sqlalchemy import text

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"ok": True, "detail": f"Connected ({engine.dialect.name})"}
    except Exception as exc:  # pragma: no cover - depends on local environment
        return {"ok": False, "detail": str(exc)}
