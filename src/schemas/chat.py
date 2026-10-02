"""Chat and streaming request/response schemas.

Donor: veda/schemas/chat.py, read in full. `image_paths` dropped from both
requests — that field only ever fed the vision pipeline's multi-image
attachments (validated against TEMP_DIR/uploads in the donor route), and
vision is out of scope for this build.
"""

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    system_context: str = ""
    from_voice: bool = False


class ChatResponse(BaseModel):
    response: str


class StreamRequest(BaseModel):
    message: str
    system_context: str = ""
    from_voice: bool = False