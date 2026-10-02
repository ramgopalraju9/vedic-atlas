"""Knowledge base request/response schemas.

Donor: veda/schemas/knowledge.py, copied verbatim.
"""

from pydantic import BaseModel


class FactRequest(BaseModel):
    fact: str


class FactList(BaseModel):
    facts: list[str]