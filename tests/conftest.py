"""Offline fixtures using real HA classes and loopback-only WebSockets."""
import asyncio
import json
from urllib.parse import urlsplit

import pytest

from homeassistant.core import HomeAssistant

from custom_components.proflame_connect_wifi import client_base
from custom_components.proflame_connect_wifi.client import ProflameClient


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch):
    """Never let an integration test connect to a real fireplace."""
    connect = client_base.connect

    def guarded_connect(uri, **kwargs):
        assert urlsplit(uri).hostname in {"127.0.0.1", "localhost", "::1"}
        return connect(uri, **kwargs)

    monkeypatch.setattr(client_base, "connect", guarded_connect)


@pytest.fixture
def snapshot():
    """Historical observation, not a claim about current hardware state."""
    return {
        "main_mode": 0, "temperature_unit": 1, "lamp_control": 0,
        "pilot_mode": 1, "flame_control": 2, "auxiliary_out": 0,
        "fan_control": 0, "split_flow": 0, "temperature_set": 690,
        "room_temperature": 740,
    }


@pytest.fixture
def manual_snapshot():
    """Preserve the separate user-reported observation after RF-remote operation."""
    return {
        "auxiliary_out": 0, "fan_control": 0, "flame_control": 1,
        "lamp_control": 3, "main_mode": 1, "pilot_mode": 1,
        "room_temperature": 670, "sh_memory_1": 32678, "split_flow": 0,
        "temperature_set": 690, "temperature_unit": 1,
    }


@pytest.fixture
def indexed_initial_frames():
    """Represent a five-message firmware dump without replaying a device capture."""
    return [
        {
            "status0": "fw_revision", "value0": "FW: 625.04.673",
            "status1": "en_fan", "value1": "1",
        },
        {"status0": "main_mode", "value0": "0"},
        {
            "status0": "temperature_unit", "value0": "1",
            "status1": "burner_status", "value1": "32672",
        },
        {
            "status0": "fan_control", "value0": "0",
            "status1": "flame_control", "value1": "6",
            "status2": "lamp_control", "value2": "0",
        },
        {
            "status0": "room_temperature", "value0": "720",
            "status1": "temperature_set", "value1": "770",
            "status2": "label_aux", "value2": "001",
            "status3": "unrecognized", "value3": None,
        },
    ]


@pytest.fixture
def client(snapshot):
    """Create a ready simulated client without a socket or dispatcher."""
    result = ProflameClient("aa:bb:cc:dd:ee:ff", "127.0.0.1")
    result._handle_message("PROFLAMECONNECTIONOPEN")
    result._handle_message(json.dumps(snapshot))
    return result


@pytest.fixture
async def hass(tmp_path):
    """Create HA without starting integrations or contacting external services."""
    result = HomeAssistant(str(tmp_path))
    yield result
    await result.async_stop(force=True)


@pytest.fixture(autouse=True)
async def no_client_task_leaks():
    """Check every test releases its connection and child tasks."""
    yield
    remaining = [
        task for task in asyncio.all_tasks()
        if not task.done()
        and task.get_coro().__qualname__.startswith("ProflameClientBase.")
    ]
    assert not remaining
