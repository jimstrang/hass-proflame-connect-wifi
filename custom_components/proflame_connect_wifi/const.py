"""Constants for the Proflame integration."""
from enum import IntEnum, StrEnum


class ApiAttrs(StrEnum):
    """Available attributes accessible via the API."""

    AUXILIARY = "auxiliary_out"
    CURRENT_TEMPERATURE = "room_temperature"
    FAN_SPEED = "fan_control"
    FLAME_HEIGHT = "flame_control"
    FREE_HEAP = "free_heap"
    LIGHT_BRIGHTNESS = "lamp_control"
    MIN_FREE_HEAP = "min_free_heap"
    OPERATING_MODE = "main_mode"
    PILOT_MODE = "pilot_mode"
    SPLIT_FLOW = "split_flow"
    TARGET_TEMPERATURE = "temperature_set"
    TEMPERATURE_UNIT = "temperature_unit"
    WIFI_SIGNAL_STR = "wifi_signal_str"


class ApiControl(StrEnum):
    """Known control messages used by the API."""

    CONN_ACK = "PROFLAMECONNECTIONOPEN"
    CONN_SYN = "PROFLAMECONNECTION"
    PING = "PROFLAMEPING"
    PONG = "PROFLAMEPONG"


class OperatingMode(IntEnum):
    """Available operating modes for fireplace unit."""

    OFF = 0
    MANUAL = 1
    THERMOSTAT = 2
    SMART = 3
    # Mode 5 appears to be a WiFi/App control mode on newer firmware
    WIFI = 5


class PilotMode(IntEnum):
    """Available pilot modes for the fireplace."""

    INTERMITENT = 0
    CONTINUOUS = 1


class Preset(StrEnum):
    """Available presets for fireplace control."""

    OFF = "Off"
    MANUAL = "Manual"
    THERMOSTAT = "Thermostat"
    SMART = "Smart"


DOMAIN = "proflame_connect_wifi"
CONF_READ_ONLY = "read_only"

DEFAULT_DEVICE = 'Proflame Fireplace'
DEFAULT_NAME = 'Fireplace'
DEFAULT_PORT = 88
INITIAL_STATE_TIMEOUT = 10

# Fan speed, flame height, and light brightness all run from 0 to this level.
MAX_LEVEL = 6

# Validate known numeric fields independently; unknown diagnostics aren't controls.
STATE_RANGES = {
    ApiAttrs.OPERATING_MODE: (0, 5),
    ApiAttrs.TEMPERATURE_UNIT: (0, 1),
    ApiAttrs.PILOT_MODE: (0, 1),
    ApiAttrs.FAN_SPEED: (0, MAX_LEVEL),
    ApiAttrs.FLAME_HEIGHT: (0, MAX_LEVEL),
    ApiAttrs.LIGHT_BRIGHTNESS: (0, MAX_LEVEL),
    ApiAttrs.AUXILIARY: (0, 1),
    ApiAttrs.SPLIT_FLOW: (0, 1),
    ApiAttrs.CURRENT_TEMPERATURE: (-1000, 3000),
    ApiAttrs.TARGET_TEMPERATURE: (0, 1000),
    ApiAttrs.FREE_HEAP: (0, 2**32 - 1),
    ApiAttrs.MIN_FREE_HEAP: (0, 2**32 - 1),
    ApiAttrs.WIFI_SIGNAL_STR: (-127, 0),
}

# Seconds between pings, and seconds of silence before the connection is dropped
KEEPALIVE_INTERVAL = 5
KEEPALIVE_TIMEOUT = 30
# Seconds to wait before reconnecting after a dropped connection
RECONNECT_DELAY = 5

ADJUSTABLE_MODES = [
    OperatingMode.MANUAL,
    OperatingMode.THERMOSTAT,
    OperatingMode.WIFI,
]

# Temperature limits in Celsius.
MAX_TEMPERATURE = 35
MIN_TEMPERATURE = 5
