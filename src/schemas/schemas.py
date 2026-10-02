"""Standard API response envelope.

Donor: veda/schemas/base.py — copied verbatim. The migration contract
names this file explicitly as KEEP: the AppResponse/AppError wrapper is
the house style for every route in the target repo.
"""

from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class AppError(BaseModel):
    """Structured error in API responses."""

    code: str = Field(description="Error code")
    message: str = Field(description="Error message")


class AppResponse(BaseModel, Generic[T]):
    """Standard API response wrapper."""

    response: Optional[T] = None
    error: Optional[AppError] = None