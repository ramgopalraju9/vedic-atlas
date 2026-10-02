"""SQLAlchemy engine + session factory.

Donor: veda/db/engine.py, read in full and ported near-verbatim. The
donor's `check_same_thread=False` comment referenced the background
CAMERA thread specifically — vision is out of scope, reworded to the
general reason (any background thread — e.g. audio capture — sharing
the same SQLite connection).
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from core.constants import DATA_DIR

_DEFAULT_URL = f"sqlite:///{(DATA_DIR / 'veda.db').as_posix()}"


class Base(DeclarativeBase):
    pass


def _make_engine():
    url = os.getenv("DATABASE_URL", "").strip() or _DEFAULT_URL
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        # Required so background threads (audio capture, ambient sensing)
        # can share the connection safely.
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True, expire_on_commit=False)