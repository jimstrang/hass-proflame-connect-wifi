"""Enforce observation-only access at the shared command boundary."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from websockets.asyncio.server import serve

from homeassistant.components.climate import HVACMode
from homeassistant.exceptions import HomeAssistantError

from custom_components.proflame_connect_wifi import client_base
from custom_components.proflame_connect_wifi.client import ProflameClient
from custom_components.proflame_connect_wifi.client_base import ProflameClientBase
from custom_components.proflame_connect_wifi.climate import ProflameClimate
from custom_components.proflame_connect_wifi.coordinator import ProflameDataCoordinator
from custom_components.proflame_connect_wifi.fan import ProflameFan
from custom_components.proflame_connect_wifi.light import ProflameLight
from custom_components.proflame_connect_wifi.number import ProflameFlame
from custom_components.proflame_connect_wifi.select import ProflamePilot
from custom_components.proflame_connect_wifi.switch import ProflamePower
from custom_components import proflame_connect_wifi as integration


@pytest.mark.parametrize("client_type", [ProflameClientBase, ProflameClient])
def test_default_is_read_only(client_type):
    """Refuse writes by default even when the device is connected and reporting."""
    client = client_type("test", "127.0.0.1")
    assert client.read_only
    client._handle_message("PROFLAMECONNECTIONOPEN")
    client._handle_message('{"main_mode":1,"temperature_unit":1,"flame_control":3}')
    assert client.connected
    with pytest.raises(HomeAssistantError, match="read-only"):
        client.set_state("main_mode", 0)
    assert client._queue.empty()
    assert client.get_state("main_mode") == 1


@pytest.mark.parametrize("value", [None, 0, 1, "false"])
def test_access_mode_requires_boolean(value):
    """Never interpret malformed configuration as permission to control."""
    with pytest.raises(TypeError, match="boolean"):
        ProflameClient("test", "127.0.0.1", read_only=value)


@pytest.mark.parametrize("entity_type,method,kwargs", [
    (ProflameClimate, "async_set_hvac_mode", {"hvac_mode": HVACMode.OFF}),
    (ProflameClimate, "async_set_hvac_mode", {"hvac_mode": HVACMode.HEAT}),
    (ProflameClimate, "async_set_preset_mode", {"preset_mode": "Off"}),
    (ProflameClimate, "async_set_preset_mode", {"preset_mode": "Manual"}),
    (ProflameClimate, "async_set_preset_mode", {"preset_mode": "Thermostat"}),
    (ProflameClimate, "async_set_preset_mode", {"preset_mode": "Smart"}),
    (ProflameClimate, "async_set_temperature", {"temperature": 69}),
    (ProflameClimate, "async_turn_on", {}),
    (ProflameClimate, "async_turn_off", {}),
    (ProflameFan, "async_set_percentage", {"percentage": 50}),
    (ProflameFan, "async_turn_on", {}),
    (ProflameFan, "async_turn_on", {"percentage": 50}),
    (ProflameFan, "async_turn_off", {}),
    (ProflameLight, "async_turn_on", {}),
    (ProflameLight, "async_turn_on", {"brightness": 255}),
    (ProflameLight, "async_turn_off", {}),
    (ProflameFlame, "async_set_native_value", {"value": 3}),
    (ProflamePilot, "async_select_option", {"option": "Continuous"}),
    (ProflamePilot, "async_select_option", {"option": "Intermitent"}),
    (ProflamePower, "async_turn_on", {}),
    (ProflamePower, "async_turn_off", {}),
])
async def test_all_entity_controls_are_blocked(hass, snapshot, entity_type, method, kwargs):
    """Keep entity identities and availability while rejecting every setting surface."""
    client = ProflameClient("aa:bb:cc:dd:ee:ff", "127.0.0.1")
    client._handle_message("PROFLAMECONNECTIONOPEN")
    client._handle_message(json.dumps(snapshot))
    coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
    entity = entity_type(coordinator)
    assert entity.available
    assert entity.unique_id.endswith("_aabbccddeeff")
    previous = client.full_state
    with pytest.raises(HomeAssistantError, match="read-only"):
        await getattr(entity, method)(**kwargs)
    assert client._queue.empty()
    assert client.full_state == previous
    await coordinator.async_shutdown()


async def test_read_only_subscription_and_reconnect(monkeypatch, snapshot):
    """Observe state and keepalives on fresh sessions without ever sending a setting."""
    monkeypatch.setattr(client_base, "KEEPALIVE_INTERVAL", 0.01)
    monkeypatch.setattr(client_base, "RECONNECT_DELAY", 0.01)
    sessions = []

    async def device(ws):
        messages = []
        sessions.append(messages)
        messages.append(await ws.recv())
        await ws.send("PROFLAMECONNECTIONOPEN")
        await ws.send(json.dumps(snapshot))
        message = await ws.recv()
        messages.append(message)
        assert message == "PROFLAMEPING"
        await ws.send("PROFLAMEPONG")
        if len(sessions) == 1:
            await ws.close()
        else:
            await ws.send('{"lamp_control":6,"main_mode":1,"flame_control":0}')
            await ws.wait_closed()

    async with serve(device, "127.0.0.1", 0) as server:
        client = ProflameClient("test", "127.0.0.1", server.sockets[0].getsockname()[1])
        try:
            await client.open()
            async with asyncio.timeout(2):
                while len(sessions) < 2 or client.get_state("lamp_control") != 6:
                    await asyncio.sleep(0.001)
            assert client.connected and client.read_only
            with pytest.raises(HomeAssistantError, match="read-only"):
                client.turn_off()
            assert client._queue.empty()
        finally:
            await client.close()
    assert len(sessions) == 2
    assert all(messages == ["PROFLAMECONNECTION", "PROFLAMEPING"] for messages in sessions)


@pytest.mark.parametrize("configured", [None, True, False])
async def test_setup_wires_safe_legacy_default(hass, snapshot, configured):
    """Legacy entries without the key become read-only; explicit editing stays opt-in."""
    entry = MagicMock()
    entry.data = {"host": "127.0.0.1", "port": 88}
    if configured is not None:
        entry.data["read_only"] = configured
    entry.unique_id = "test"
    entry.entry_id = "entry"
    entry.title = "Fireplace"
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    real_client = ProflameClient
    clients = []

    def create(**kwargs):
        client = real_client(**kwargs)
        client._handle_message("PROFLAMECONNECTIONOPEN")
        client._handle_message(json.dumps(snapshot))
        client.open = AsyncMock()
        clients.append(client)
        return client

    with patch.object(integration, "ProflameClient", side_effect=create):
        assert await integration.async_setup_entry(hass, entry)
    client = clients[0]
    assert client.read_only is (configured is not False)
    if client.read_only:
        with pytest.raises(HomeAssistantError, match="read-only"):
            client.turn_off_light()
        assert client._queue.empty()
    else:
        client.turn_off_light()
        assert client._queue.get_nowait() == {"lamp_control": 0}
        client._queue.task_done()
    assert entry.runtime_data.client is client
    assert "proflame_connect_wifi" not in hass.data
    await entry.runtime_data.async_shutdown()


@pytest.mark.parametrize("field,value", [("lamp_control", 0), ("main_mode", 0)])
async def test_editable_echo_can_be_overwritten_without_retry(hass, snapshot, field, value):
    """Replay RF-style contention without claiming an echo is a successful physical action."""
    baseline = {**snapshot, "main_mode": 1, "flame_control": 6, "lamp_control": 2}
    release_overwrite = asyncio.Event()
    overwritten = asyncio.Event()
    received = []

    async def device(ws):
        received.append(await ws.recv())
        await ws.send("PROFLAMECONNECTIONOPEN")
        await ws.send(json.dumps(baseline))
        command = await ws.recv()
        received.append(command)
        assert json.loads(command) == {field: value}
        await ws.send(command)
        await release_overwrite.wait()
        await ws.send(json.dumps(baseline))
        overwritten.set()
        await ws.wait_closed()

    async with serve(device, "127.0.0.1", 0) as server:
        client = ProflameClient(
            "test", "127.0.0.1", server.sockets[0].getsockname()[1], read_only=False,
        )
        coordinator = None
        try:
            await client.open()
            assert await client.wait_for_state(1)
            coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
            client.set_state(field, value)
            async with asyncio.timeout(2):
                while client.get_state(field) != value:
                    await asyncio.sleep(0.001)
            assert coordinator.data[field] == value
            assert client.hvac_action is None
            release_overwrite.set()
            await asyncio.wait_for(overwritten.wait(), 1)
            async with asyncio.timeout(2):
                while coordinator.data[field] != baseline[field]:
                    await asyncio.sleep(0.001)
            assert client.hvac_action is None
            await asyncio.wait_for(client._queue.join(), 1)
            assert client._queue.empty()
            assert len(received) == 2
        finally:
            release_overwrite.set()
            if coordinator is not None:
                await coordinator.async_shutdown()
            else:
                await client.close()
