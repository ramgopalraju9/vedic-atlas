from typing import Protocol, runtime_checkable

@runtime_checkable
class StatusDisplayPort(Protocol):
    """A small character display showing current device state."""

    @property
    def rows(self) -> int:
        """Number of text rows this display supports (4 for the reference 16x4)."""
        ...

    @property
    def cols(self) -> int:
        """Number of characters per row (16 for the reference display)."""
        ...

    def show(self, lines: tuple[str, ...]) -> None:
        """Render up to `rows` lines of up to `cols` characters each."""
        ...
