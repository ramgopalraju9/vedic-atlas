"""Persona schema — shared by /api/persona REST and the CLI client.

Donor: veda/schemas/persona.py, copied verbatim (no changes — the
persona/proactivity concept isn't vision- or cloud-CLI-specific).
Mirror of the Angular UI's PersonaStore (ui/src/app/core/state/persona.store.ts).
The four personas seed proactivity defaults the same way:

    developer → medium      (code-first, terminal at hand)
    scrum     → chatty      (standups, blockers, more ambient signal)
    architect → conservative (long-form thinking, quiet by default)
    manager   → medium      (governance + recorder one keystroke away)

Browser persists to localStorage. CLI persists to data/cli_persona.json.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PersonaName = Literal["developer", "scrum", "architect", "manager"]


class PersonaState(BaseModel):
    """Current persona + first-run completion."""

    persona: PersonaName | None = None
    completed: bool = False


class PersonaUpdateRequest(BaseModel):
    """POST body for /api/persona."""

    persona: PersonaName | None = None
    completed: bool | None = Field(
        default=None,
        description="Optional — set true to mark onboarding complete.",
    )


class PersonaInfo(BaseModel):
    """One row in the persona catalog."""

    id: PersonaName
    label: str
    tagline: str
    proactivity: str   # "conservative" | "medium" | "chatty"


PERSONA_CATALOG: list[PersonaInfo] = [
    PersonaInfo(
        id="developer",
        label="Developer",
        tagline="Code-first. Terminal at hand. Approval gated until you trust.",
        proactivity="medium",
    ),
    PersonaInfo(
        id="scrum",
        label="Scrum Master",
        tagline="Standups, blockers, Teams nudges. More ambient signal.",
        proactivity="chatty",
    ),
    PersonaInfo(
        id="architect",
        label="Architect",
        tagline="Long-form thinking. Quiet by default. Markdown + diagrams.",
        proactivity="conservative",
    ),
    PersonaInfo(
        id="manager",
        label="Engineering Manager",
        tagline="Governance + recorder + standup rollups one keystroke away.",
        proactivity="medium",
    ),
]


def lookup(name: str) -> PersonaInfo | None:
    """Case-insensitive lookup by id."""
    needle = (name or "").strip().lower()
    for p in PERSONA_CATALOG:
        if p.id == needle:
            return p
    return None