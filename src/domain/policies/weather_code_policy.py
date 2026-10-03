"""WeatherCodePolicy — WMO weather-interpretation code -> plain words."""

_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "heavy freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light rain showers", 81: "rain showers", 82: "violent rain showers",
    85: "light snow showers", 86: "heavy snow showers",
    95: "a thunderstorm", 96: "a thunderstorm with hail", 99: "a heavy thunderstorm with hail",
}


def describe_weather_code(code: object) -> str:
    try:
        return _CODES.get(int(code), "unsettled weather")
    except (TypeError, ValueError):
        return "unknown conditions"
