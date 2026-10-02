"""System info response schema.

Donor: veda/schemas/system.py, copied verbatim.
"""

from pydantic import BaseModel


class SystemInfo(BaseModel):
    window_title: str = ""
    process_name: str = ""
    exe_name: str = ""