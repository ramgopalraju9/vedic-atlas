"""Persona REST surface — backs both the CLI and the Angular UI.

Donor: veda/routes/persona.py, read in full and copied near-verbatim.
Persistence: data/cli_persona.json (single-user, single file). When
persona changes, seeds proactivity via ambient.set_proactivity() the
same way the Angular PersonaStore does, so the brain matches the UI/CLI
in one round-trip.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from controller.dependencies.providers import get_ambient_dispatcher
from core.constants import DATA_DIR
from core.logging_config import logger
from schemas.persona import PERSONA_CATALOG, PersonaState, PersonaUpdateRequest, lookup
from service.sensing.ambient_dispatcher import AmbientDispatcher

router = APIRouter()

_PERSONA_FILE = DATA_DIR / "cli_persona.json"


def _load() -> PersonaState:
    try:
        if _PERSONA_FILE.is_file():
            data: dict[str, Any] = json.loads(_PERSONA_FILE.read_text(encoding="utf-8"))
            return PersonaState(**data)
    except Exception as e:
        logger.warning(f"persona: failed to read {_PERSONA_FILE}: {e}")
    return PersonaState()


def _save(state: PersonaState) -> None:
    try:
        _PERSONA_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PERSONA_FILE.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"persona: failed to write {_PERSONA_FILE}: {e}")


@router.get("/persona")
async def get_persona() -> dict[str, Any]:
    state = _load()
    return {
        "state": state.model_dump(),
        "catalog": [p.model_dump() for p in PERSONA_CATALOG],
    }


@router.post("/persona")
async def set_persona(
    body: PersonaUpdateRequest, ambient: AmbientDispatcher = Depends(get_ambient_dispatcher)
) -> dict[str, Any]:
    state = _load()

    if body.persona is not None:
        info = lookup(body.persona)
        if info is None:
            raise HTTPException(
                status_code=400,
                detail=f"unknown persona '{body.persona}'. Valid: {[p.id for p in PERSONA_CATALOG]}",
            )
        state.persona = info.id
        try:
            ambient.set_proactivity(info.proactivity)
            logger.info(f"persona: set to {info.id!r}; proactivity seeded to {info.proactivity!r}")
        except Exception as e:
            logger.warning(f"persona: proactivity seed failed: {e}")

    if body.completed is not None:
        state.completed = body.completed

    _save(state)
    return {
        "state": state.model_dump(),
        "catalog": [p.model_dump() for p in PERSONA_CATALOG],
    }