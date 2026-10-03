"""Place — a resolved location with coordinates and how it was resolved."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float
    region: str = ""
    country: str = ""
    source: str = "geocoder"  # "geocoder" | "search" | "default"

    def label(self) -> str:
        """e.g. "Hyderabad, Telangana, India" — spoken/logged so a wrong match is visible."""
        return ", ".join(p for p in (self.name, self.region, self.country) if p)
