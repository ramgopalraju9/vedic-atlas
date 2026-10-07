"""Streaming request schema for POST /api/stream.

`image_paths` was dropped — vision is out of scope for this build.
"""

from pydantic import BaseModel


class StreamRequest(BaseModel):
    message: str
    system_context: str = ""
    from_voice: bool = False
