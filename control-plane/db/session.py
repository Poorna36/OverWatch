"""Database engine and session management with SQLite foreign key enforcement."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Generator
from typing import Any

from control_plane.db.models import Base
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = "sqlite:///./overwatch.db"

# Table immutability and retention trigger DDL statements
SESSIONS_PREVENT_DELETE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS sessions_prevent_delete
BEFORE DELETE ON sessions
BEGIN
    SELECT RAISE(ABORT, 'Session records are permanent: DELETE not allowed');
END;
"""

AUDIT_PREVENT_UPDATE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS audit_entries_prevent_update
BEFORE UPDATE ON audit_entries
BEGIN
    SELECT RAISE(ABORT, 'Audit log entries are immutable: UPDATE not allowed');
END;
"""

AUDIT_PREVENT_DELETE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS audit_entries_prevent_delete
BEFORE DELETE ON audit_entries
BEGIN
    SELECT RAISE(ABORT, 'Audit log entries are immutable: DELETE not allowed');
END;
"""


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
    """Enforce foreign key constraints on every SQLite connection."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_database_url() -> str:
    """Retrieve configured database connection URL."""
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def create_db_engine(url: str | None = None, **kwargs: Any) -> Engine:
    """Create a configured SQLAlchemy engine."""
    database_url = url or get_database_url()
    connect_args: dict[str, Any] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(database_url, connect_args=connect_args, **kwargs)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a sessionmaker factory bound to the given engine."""
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db(engine: Engine) -> None:
    """Create all core database tables and attach immutability triggers."""
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        conn.exec_driver_sql(SESSIONS_PREVENT_DELETE_TRIGGER)
        conn.exec_driver_sql(AUDIT_PREVENT_UPDATE_TRIGGER)
        conn.exec_driver_sql(AUDIT_PREVENT_DELETE_TRIGGER)
        conn.commit()


def get_db(
    session_factory: sessionmaker[Session] | None = None,
) -> Generator[Session, None, None]:
    """FastAPI dependency for yielding transactional database sessions."""
    if session_factory is None:
        engine = create_db_engine()
        session_factory = create_session_factory(engine)

    db = session_factory()
    try:
        yield db
    finally:
        db.close()
