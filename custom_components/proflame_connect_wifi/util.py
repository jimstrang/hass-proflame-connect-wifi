"""General helper objects used by the rest of the module."""

import re
from typing import Any, Self

from homeassistant.helpers.device_registry import format_mac


def discovery_mac(value: str) -> str | None:
    """Normalize a usable MAC without interpreting arbitrary legacy IDs as addresses."""
    mac = format_mac(value)
    if (
        re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", mac) is None
        or mac == "00:00:00:00:00:00"
        or int(mac[:2], 16) & 1
    ):
        return None
    return mac


def coalesce(*args: list[Any]) -> Any | None:
    """Return the first non-null argument."""
    try:
        return next(x for x in args if x is not None)
    except StopIteration:
        return None

def constrain(value: int, min_value: int, max_value: int):
    """Constrain a number to a specified range."""
    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value

class Temperature:
    """Helper class for interacting with temperature."""

    @staticmethod
    def celcius(value) -> Self:
        """Create temperature from Celcius value."""
        return Temperature(value)

    @staticmethod
    def fahrenheit(value) -> Self:
        """Create temperature from Fahrenheit value."""
        return Temperature((value - 32) * 5/9)

    def __init__(self, celcius) -> None:
        """Create new instance of the Temperature class."""
        self._value = celcius

    def to_celcius(self) -> float:
        """Return the temperature value as Celcius."""
        return self._value

    def to_fahrenheit(self) -> float:
        """Return the temperature value as Fahrenheit."""
        return (self._value * 9/5) + 32
