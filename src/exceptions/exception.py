"""Application exception base class.

Donor: veda/exceptions/__init__.py — copied as-is, only the import of
ErrorMessage repointed to the new core location.
"""

from typing import Optional

from core.enums import ErrorMessage


class AppException(Exception):
    """Application-level exception with a structured code and message.

    Renamed from VedaException -> AppException: this class is core-layer
    infrastructure, not branding, and every ring imports it.
    """

    def __init__(
        self,
        class_name: str,
        code: str,
        error_message: Optional[ErrorMessage] = None,
        message: Optional[str] = None,
        **kwargs,
    ):
        self.class_name = class_name
        self.code = code
        if message is not None:
            self.message = message
        else:
            self.message = error_message.value.format(**kwargs)
        super().__init__(self.message)

    def __str__(self):
        return self.message