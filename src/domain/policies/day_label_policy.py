"""DayLabelPolicy — how a remembered day is written in a prompt: "Thu 8 Oct"."""

from datetime import datetime


def day_label(when: datetime) -> str:
    return f"{when.strftime('%a')} {when.day} {when.strftime('%b')}"
