"""Streaming request schema for POST /api/stream.

`image_paths` was dropped — vision is out of scope for this build.
"""

from pydantic import BaseModel, Field


class StreamRequest(BaseModel):
    message: str
    system_context: str = ""
    from_voice: bool = False
    # Optional caller-supplied session. When omitted the server derives one from the 30-minute activity gap.
    session_id: str | None = Field(default=None, min_length=1, max_length=32)
