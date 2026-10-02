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
    from tpa.persistence.models import agent_memory, conversation, memory_vector, task  # noqa: F401

    Base.metadata.create_all(engine)