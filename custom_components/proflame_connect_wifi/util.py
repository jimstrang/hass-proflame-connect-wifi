"""General helper objects used by the rest of the module."""

import re

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


def constrain(value: int, min_value: int, max_value: int):
    """Constrain a number to a specified range."""
    return min(max(value, min_value), max_value)
