"""ReminderFiredRow — SQLAlchemy model for the reminder ledger. Table `reminders_fired`.

One row per reminder that was said, keyed by `domain.policies.reminder_policy.reminder_key`, so a restart (or a moved
event, which gets a new key) never repeats or loses a reminder. Holds no titles: the key carries an id and a time only.
"""

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from tpa.persistence.session import Base


class ReminderFiredRow(Base):
    __tablename__ = "reminders_fired"

    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    fired_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
