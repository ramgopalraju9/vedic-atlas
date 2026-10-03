"""CoordinatePolicy — extract and validate latitude/longitude from web text.

Used only as a fallback when geocoding can't find a place name and a web
search is asked for its coordinates. Search text is untrusted, so a pair is
accepted only if it parses cleanly AND falls inside the valid ranges.
"""

import re

_NUM = r"(-?\d{1,3}(?:\.\d+)?)"
# 17.385 N, 78.4867 E   /   17.385° N 78.4867° E
_HEMISPHERE_RE = re.compile(_NUM + r"\s*°?\s*([NS])\b[\s,;]*" + _NUM + r"\s*°?\s*([EW])\b", re.IGNORECASE)
# latitude: 17.385 ... longitude: 78.4867
_LABELLED_RE = re.compile(r"lat(?:itude)?\W{0,3}" + _NUM + r"\D{1,30}?lon(?:g|gitude)?\W{0,3}" + _NUM, re.IGNORECASE)
# 17.385, 78.4867  (decimals required so stray integers don't match)
_PAIR_RE = re.compile(r"(?<![\d.])(-?\d{1,2}\.\d{2,})\s*,\s*(-?\d{1,3}\.\d{2,})(?![\d.])")


def _valid(lat: float, lon: float) -> bool:
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0 and not (lat == 0.0 and lon == 0.0)


def parse_lat_lon(text: str) -> tuple[float, float] | None:
    """(lat, lon) found in `text`, or None."""
    text = text or ""
    m = _HEMISPHERE_RE.search(text)
    if m:
        lat, lon = float(m.group(1)), float(m.group(3))
        lat = -abs(lat) if m.group(2).upper() == "S" else abs(lat)
        lon = -abs(lon) if m.group(4).upper() == "W" else abs(lon)
        if _valid(lat, lon):
            return lat, lon
    for pattern in (_LABELLED_RE, _PAIR_RE):
        m = pattern.search(text)
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if _valid(lat, lon):
                return lat, lon
    return None
