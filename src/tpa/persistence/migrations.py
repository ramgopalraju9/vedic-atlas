"""init_tables — create SQLite/Postgres tables if they don't exist.

Donor: veda/db/__init__.py's re-export module + init_db() in engine.py,
read in full. Dropped the Person/FaceEmbedding/Observation imports —
vision is out of scope, those tables are never created.
"""

from tpa.persistence.session import Base, engine
from core.constants import DATA_DIR


def init_tables() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    DATA_DIR.mkdir(exist_ok=True)
    # Import models for side-effect registration before create_all.
    from tpa.persistence.models import (  # noqa: F401
        agent_memory, conversation, memory_vector, session_context, task, turn_trace,
    )

    Base.metadata.create_all(engine)
    _add_missing_columns()


# create_all never alters an existing table, so columns added after a user's
# DB was first created are applied here (SQLite ADD COLUMN, idempotent).
_ADDED_COLUMNS = (("tasks", "completed_at", "DATETIME"),)


def _add_missing_columns() -> None:
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    with engine.begin() as conn:
        for table, column, ddl in _ADDED_COLUMNS:
            if not insp.has_table(table):
                continue
            if column not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))