from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _engine_kwargs(database_url: str) -> dict:
    if not database_url.startswith("sqlite"):
        return {}

    kwargs: dict = {"connect_args": {"check_same_thread": False}}
    if ":memory:" in database_url or "mode=memory" in database_url:
        # A plain in-memory SQLite database lives and dies with its connection.
        # StaticPool keeps a single connection alive so every request — and every
        # thread FastAPI hands work to — sees the same data for the process's lifetime.
        kwargs["poolclass"] = StaticPool
    return kwargs


settings = get_settings()

engine = create_engine(
    settings.database_url,
    echo=settings.sql_echo,
    **_engine_kwargs(settings.database_url),
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db() -> None:
    """Create tables and run migrations. Called on startup; safe to call repeatedly."""
    from app import models  # noqa: F401  (register models on Base.metadata)

    Base.metadata.create_all(bind=engine)
    _run_migrations()


def _run_migrations() -> None:
    """Run schema migrations for existing databases."""
    from sqlalchemy import inspect, text
    import logging

    logger = logging.getLogger("contacts.database")

    inspector = inspect(engine)
    if "contacts" not in inspector.get_table_names():
        return  # Table doesn't exist yet, create_all handled it

    columns = {col["name"] for col in inspector.get_columns("contacts")}
    if "photo" in columns:
        return

    # Add the missing column. Be defensive: log and ignore errors so startup
    # doesn't bring the whole app down for minor migration failures.
    try:
        with engine.begin() as conn:
            if engine.dialect.name == "sqlite":
                logger.info("adding 'photo' column to contacts (sqlite)")
                conn.execute(text("ALTER TABLE contacts ADD COLUMN photo TEXT"))
            elif engine.dialect.name == "postgresql":
                # Use IF NOT EXISTS where supported to be idempotent
                logger.info("adding 'photo' column to contacts (postgresql)")
                conn.execute(text("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS photo TEXT"))
            else:
                # Fallback generic ALTER TABLE statement
                logger.info("adding 'photo' column to contacts (generic)")
                conn.execute(text("ALTER TABLE contacts ADD COLUMN photo TEXT"))
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("failed to add 'photo' column: %s", exc)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a session that is always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
