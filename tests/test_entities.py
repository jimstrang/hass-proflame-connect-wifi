"""State and entity contracts, with serialization but no hardware writes."""
import json
import logging
from datetime import timedelta
from unittest.mock import Mock

import pytest

from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.helpers.entity_platform import EntityPlatform

from custom_components.proflame_connect_wifi.client import ProflameClient
from custom_components.proflame_connect_wifi.climate import ProflameClimate
from custom_components.proflame_connect_wifi.coordinator import ProflameDataCoordinator
from custom_components.proflame_connect_wifi.fan import ProflameFan
from custom_components.proflame_connect_wifi.light import ProflameLight
from custom_components.proflame_connect_wifi.number import ProflameFlame
from custom_components.proflame_connect_wifi.select import ProflamePilot
from custom_components.proflame_connect_wifi.sensor import ProflameSensor
from custom_components.proflame_connect_wifi.switch import ProflamePower
from custom_components.proflame_connect_wifi.const import ApiAttrs, OperatingMode, Preset
from custom_components.proflame_connect_wifi.util import Temperature


def drain(client):
    """Inspect and balance the simulated command queue without sending."""
    result = []
    while not client._queue.empty():
        item = client._queue.get_nowait()
        result.append(json.loads(client._format_command(item)))
        client._queue.task_done()
    return result


@pytest.mark.parametrize("payload", [
    [], None, "text", {}, {"status0": "fan_control"},
    {"fan_control": True}, {"fan_control": 7}, {"fan_control": -1},
    {"fan_control": 2.5}, {"fan_control": "3"}, {"main_mode": 4},
    {"temperature_unit": 2}, {"pilot_mode": None}, {"room_temperature": 999999},
])
def test_invalid_fields_do_not_corrupt_state(client, payload):
    """Malformed fields never replace known settings."""
    previous = client.full_state
    client._handle_json_message(payload)
    assert client.full_state == previous
    assert not client._uses_indexed_format


def test_partial_updates_commit_atomically(client):
    """Bad/unknown fields don't discard good fields or introduce order dependence."""
    observed = []
    client.register_callback(lambda key, value: observed.append(client.full_state))
    client._handle_json_message({
        "main_mode": 1, "flame_control": 0, "fan_control": 4,
        "lamp_control": False, "fw_revision": "unknown",
        "burner_status": 1, "unexpected": {"nested": True},
    })
    assert client.fan_speed == 4
    assert client.preset == Preset.OFF
    assert client._stored_mode == OperatingMode.MANUAL
    assert "burner_status" not in client.full_state
    assert "unexpected" not in client.full_state
    assert observed and all(state["flame_control"] == 0 for state in observed)


@pytest.mark.parametrize("fixture_name", ["snapshot", "manual_snapshot"])
async def test_observed_snapshots_without_entity_churn(hass, request, fixture_name):
    """Process 25 identical observed payloads without interpreting unknown telemetry."""
    snapshot = request.getfixturevalue(fixture_name)
    client = ProflameClient("test", "127.0.0.1")
    coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
    updates = Mock()
    unsubscribe = coordinator.async_add_listener(updates)
    client._handle_message("PROFLAMECONNECTIONOPEN")
    assert client._handle_message(json.dumps(snapshot))
    # One atomic known-state update and one availability transition.
    assert updates.call_count == 2
    updates.reset_mock()
    for _ in range(24):
        # Unchanged valid traffic still counts toward connection liveness.
        assert client._handle_message(json.dumps(snapshot))
    updates.assert_not_called()
    assert coordinator.data == {
        key: value for key, value in snapshot.items() if key != "sh_memory_1"
    }
    for key in ("auxiliary_out", "fan_control", "split_flow"):
        assert client.get_state(key) == 0
    assert client.current_temperature == snapshot["room_temperature"] / 10
    assert client.hvac_action is None
    assert "burner_status" not in coordinator.data
    assert not any("gas" in key or "runtime" in key for key in coordinator.data)
    assert "sh_memory_1" not in coordinator.data
    if "sh_memory_1" in snapshot:
        assert client._unknown_state["sh_memory_1"] == 32678
        client._handle_message('{"sh_memory_1":0}')
        assert client._unknown_state["sh_memory_1"] == 0
        updates.assert_not_called()
    client._handle_message('{"fan_control":2,"unknown_field":{"raw":true}}')
    updates.assert_called_once_with()
    assert coordinator.data["fan_control"] == 2
    assert client._unknown_state["unknown_field"] == {"raw": True}
    assert client._queue.empty()
    unsubscribe()
    await coordinator.async_shutdown()


@pytest.mark.parametrize("field", ["sh_memory_1", "burner_status"])
@pytest.mark.parametrize("indexed", [False, True])
@pytest.mark.parametrize("raw_memory", [
    32672, 32678, 32673, 0, 65535, None, "unrecognized", "32672", "0032678",
])
async def test_memory_hypotheses_remain_raw_and_unclassified(
    hass, client, raw_memory, field, indexed,
):
    """Do not turn third-party candidates or unfamiliar values into burner telemetry."""
    client._handle_json_message({"main_mode": 2, "flame_control": 1})
    coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
    updates = Mock()
    unsubscribe = coordinator.async_add_listener(updates)
    payload = {"status0": field, "value0": raw_memory} if indexed else {field: raw_memory}
    client._handle_message(json.dumps(payload))
    assert client._unknown_state[field] == raw_memory
    assert field not in client.full_state
    assert field not in coordinator.data
    assert client.get_state("burner_status") is None
    assert client.hvac_mode == HVACMode.HEAT
    assert client.hvac_action is None
    updates.assert_not_called()
    assert client._queue.empty()
    unsubscribe()
    await coordinator.async_shutdown()


@pytest.mark.parametrize("unit,expected", [
    (0, UnitOfTemperature.CELSIUS), (1, UnitOfTemperature.FAHRENHEIT),
])
def test_temperature_units_and_zero(client, unit, expected):
    """Temperatures are tenths of the reported unit; zero is legitimate."""
    client._handle_json_message({
        "main_mode": 2, "temperature_unit": unit,
        "room_temperature": 0, "temperature_set": 0,
    })
    assert client.temperature_unit == expected
    assert client.current_temperature == 0
    assert client.target_temperature == 0
    client.set_target_temperature(Temperature.fahrenheit(68))
    assert drain(client) == [{"temperature_set": 200 if unit == 0 else 680}]


def test_missing_fields_are_unknown():
    """No missing state is invented as Celsius, off, or heating."""
    client = ProflameClient("test", "127.0.0.1", read_only=False)
    assert client.current_temperature is None
    assert client.target_temperature is None
    assert client.temperature_unit is None
    assert client.hvac_mode is None
    assert client.hvac_action is None
    assert client.fan_speed is None
    assert client.light_brightness is None
    assert client.flame_height is None
    with pytest.raises(ValueError, match="reported device unit"):
        client.set_target_temperature(Temperature.celcius(20))
    with pytest.raises(ValueError, match="known operating preset"):
        client.heat()


def test_unknown_preset_cannot_silently_restore_heat(client):
    """Refuse an unsupported mode rather than reporting a successful no-op or guessing."""
    client._handle_json_message({"main_mode": 5})
    with pytest.raises(ValueError, match="known operating preset"):
        client.heat()
    assert client._queue.empty()


def test_readiness_requires_ack_and_both_required_fields():
    """Neither a bare ACK nor an incomplete snapshot enables commands."""
    client = ProflameClient("test", "127.0.0.1")
    client._handle_message('{"main_mode":0}')
    assert not client.connected
    client._handle_message("PROFLAMECONNECTIONOPEN")
    assert not client.connected
    client._handle_message('{"temperature_unit":0}')
    assert client.connected
    assert client._state_received.is_set()


@pytest.mark.parametrize("message", [
    "{", '{"fan_control":NaN}', "[" * 1100 + "]" * 1100,
    '{"fan_control":' + "1" * 5000 + "}",
])
def test_malformed_json_cannot_kill_listener(client, message):
    """Syntax, recursion, and integer decoding limits leave valid state intact."""
    previous = client.full_state
    assert client._handle_message(message) is False
    assert client.full_state == previous


@pytest.mark.parametrize("mode,flame,preset,hvac", [
    (0, 2, Preset.OFF, HVACMode.OFF),
    (1, 0, Preset.OFF, HVACMode.OFF),
    (1, 2, Preset.MANUAL, HVACMode.HEAT),
    (2, 0, Preset.THERMOSTAT, HVACMode.HEAT),
    (3, 2, Preset.SMART, HVACMode.HEAT),
    (5, 2, None, None),
])
def test_climate_settings_are_not_burner_telemetry(client, mode, flame, preset, hvac):
    """Modes describe configuration, not confirmed physical operation."""
    client._handle_json_message({"main_mode": mode, "flame_control": flame})
    assert client.preset == preset
    assert client.hvac_mode == hvac
    assert client.hvac_action is None


def test_indexed_receive_and_serialization(client):
    """Retain existing indexed support without assuming it's this dongle's protocol."""
    client._handle_message(json.dumps({
        "status0": "fan_control", "value0": "3",
        "status1": "fw_revision", "value1": "1.2.3",
    }))
    assert client.get_state("fan_control") == 3
    assert client._uses_indexed_format
    client.set_fan_speed(4)
    assert drain(client) == [{
        "control0": "main_mode", "value0": "5",
        "control1": "fan_control", "value1": "4",
    }]
    client._handle_json_message({"status0": "fan_control", "value0": 1.5})
    assert client.get_state("fan_control") == 3


async def test_entities_identity_availability_and_cleanup(hass, client):
    """Every platform preserves historical unique IDs and tracks readiness."""
    coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
    entities = [
        ProflameClimate(coordinator), ProflameFan(coordinator),
        ProflameLight(coordinator), ProflameFlame(coordinator),
        ProflamePilot(coordinator), ProflameSensor(coordinator, ApiAttrs.FREE_HEAP),
        ProflamePower(coordinator),
    ]
    assert [entity.unique_id for entity in entities] == [
        f"{key}_aabbccddeeff" for key in
        ["climate", "fan", "light", "burner", "pilot", "free_heap", "fireplace"]
    ]
    assert all(entity.available for entity in entities)
    assert coordinator.device_info["identifiers"] == {
        ("proflame_connect_wifi", "aa:bb:cc:dd:ee:ff"),
    }
    assert coordinator.device_info["connections"] == {
        ("mac", "aa:bb:cc:dd:ee:ff"),
    }
    client._set_connected(False)
    assert not any(entity.available for entity in entities)
    client._state.clear()
    client._handle_json_message({"main_mode": 0, "temperature_unit": 1})
    assert "fan_control" not in coordinator.data
    client._set_connected(True)
    assert all(entity.available for entity in entities)
    await coordinator.async_shutdown()
    assert not client._connection_callbacks
    assert client._callbacks == [client._track_state]
    await coordinator.async_shutdown()


@pytest.mark.parametrize("field,value", [
    (ApiAttrs.FREE_HEAP, 19060),
    (ApiAttrs.MIN_FREE_HEAP, 18000),
    (ApiAttrs.WIFI_SIGNAL_STR, -55),
])
async def test_optional_sensors_are_disabled_diagnostics(hass, client, field, value):
    """Default optional diagnostics off while preserving identity and reported-value handling."""
    coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
    sensor = ProflameSensor(coordinator, field)
    assert sensor.entity_category == EntityCategory.DIAGNOSTIC
    assert sensor.entity_registry_enabled_default is False
    assert sensor.unique_id == f"{field}_aabbccddeeff"
    assert sensor.native_value is None
    client._handle_json_message({field: value})
    assert sensor.native_value == value
    client._handle_json_message({field: 0})
    assert sensor.native_value == 0
    assert client._queue.empty()
    await coordinator.async_shutdown()


async def test_pilot_mode_is_enabled_configuration(hass, client):
    """Group the secondary pilot setting as Configuration without changing its identity."""
    coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
    pilot = ProflamePilot(coordinator)
    assert pilot.entity_category == EntityCategory.CONFIG
    assert pilot.entity_registry_enabled_default is True
    assert pilot.unique_id == "pilot_aabbccddeeff"
    assert pilot.options == ["Continuous", "Intermitent"]
    assert pilot.current_option == "Continuous"
    client._handle_json_message({"pilot_mode": 0})
    assert pilot.current_option == "Intermitent"
    assert client._queue.empty()
    await coordinator.async_shutdown()


async def test_climate_ha_state_rendering_during_reconnect(hass, client):
    """HA renders unavailable capabilities without inventing a new native unit."""
    coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
    climate = ProflameClimate(coordinator)
    platform = EntityPlatform(
        hass=hass, logger=logging.getLogger(__name__), domain="climate",
        platform_name="proflame_connect_wifi", platform=None,
        scan_interval=timedelta(seconds=30), entity_namespace=None,
    )
    climate.add_to_platform_start(hass, platform, None)
    climate.entity_id = "climate.fireplace"
    await climate.add_to_platform_finish()
    state = hass.states.get("climate.fireplace")
    assert state.state == "off"
    original_minimum = state.attributes["min_temp"]
    client._set_connected(False)
    client._state.clear()
    assert client.temperature_unit is None
    assert climate.current_temperature is None
    climate.async_write_ha_state()
    state = hass.states.get("climate.fireplace")
    assert state.state == "unavailable"
    assert state.attributes["min_temp"] == original_minimum
    assert climate.temperature_unit == UnitOfTemperature.FAHRENHEIT
    with pytest.raises(ValueError, match="reported device unit"):
        await climate.async_set_temperature(temperature=20)
    with pytest.raises(ValueError, match="required"):
        await climate.async_set_temperature()
    assert not drain(client)
    client._handle_message('{"main_mode":2,"temperature_unit":0,"room_temperature":0}')
    climate.async_write_ha_state()
    state = hass.states.get("climate.fireplace")
    assert state.state == "heat"
    assert state.attributes["current_temperature"] == 0
    assert climate.temperature_unit == UnitOfTemperature.CELSIUS
    assert "hvac_action" not in state.attributes
    await climate.async_remove(force_remove=True)
    await coordinator.async_shutdown()


async def test_preserve_climate_off_and_power_off_contracts(hass, client):
    """Climate Off is flame-zero/manual; full-device Off remains a separate action."""
    coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
    climate = ProflameClimate(coordinator)
    power = ProflamePower(coordinator)
    client._handle_json_message({"main_mode": 2, "flame_control": 3})
    await climate.async_set_hvac_mode(HVACMode.OFF)
    assert drain(client) == [{"flame_control": 0}, {"main_mode": 1}]
    await climate.async_turn_off()
    assert drain(client) == [{"flame_control": 0}, {"main_mode": 1}]
    await power.async_turn_off()
    assert drain(client) == [{"main_mode": 0}]
    assert climate.supported_features & ClimateEntityFeature.TURN_OFF
    assert climate.supported_features & ClimateEntityFeature.TURN_ON
    # Commands are not optimistically applied to reported state.
    assert client.operating_mode == 2
    assert client.flame_height == 3
    await coordinator.async_shutdown()


@pytest.mark.parametrize("mode,flame,expected", [
    (None, None, None),
    (0, 0, False), (0, 6, False),
    (1, 0, True), (1, 3, True), (1, None, True),
    (2, 0, True), (3, 0, True), (5, 0, True),
])
async def test_device_power_reflects_mode_not_heating(hass, mode, flame, expected):
    """Device power is independent of the climate Off preset and retained flame settings."""
    client = ProflameClient("aa:bb:cc:dd:ee:ff", "127.0.0.1")
    state = {}
    if mode is not None:
        state["main_mode"] = mode
    if flame is not None:
        state["flame_control"] = flame
    client._handle_json_message(state)
    coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
    power = ProflamePower(coordinator)
    assert power.is_on is expected
    assert power.unique_id == "fireplace_aabbccddeeff"
    assert power.entity_description.translation_key == "fireplace"
    assert "_attr_name" not in power.__dict__
    assert client.hvac_action is None
    assert client._queue.empty()
    await coordinator.async_shutdown()


async def test_device_on_after_climate_off_does_not_restore_flame(hass, client):
    """A mode-only On remains visibly on after its echo without secretly restoring flame."""
    client._handle_json_message({"main_mode": 1, "flame_control": 3})
    coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
    climate = ProflameClimate(coordinator)
    power = ProflamePower(coordinator)
    await climate.async_turn_off()
    assert drain(client) == [{"flame_control": 0}, {"main_mode": 1}]
    client._handle_json_message({"main_mode": 1, "flame_control": 0})
    assert climate.hvac_mode == HVACMode.OFF
    assert power.is_on is True
    await power.async_turn_off()
    assert drain(client) == [{"main_mode": 0}]
    client._handle_json_message({"main_mode": 0})
    assert power.is_on is False
    await power.async_turn_on()
    assert drain(client) == [{"main_mode": 1}]
    assert power.is_on is False
    client._handle_json_message({"main_mode": 1})
    assert power.is_on is True
    assert climate.hvac_mode == HVACMode.OFF
    assert client.get_state("flame_control") == 0
    assert client._stored_flame == 3
    assert client.hvac_action is None
    await coordinator.async_shutdown()


async def test_fan_light_ranges_and_restore(hass, client):
    """Round-trip all six levels, honor explicit speed, and restore observed settings."""
    coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
    fan = ProflameFan(coordinator)
    light = ProflameLight(coordinator)
    client._handle_json_message({"main_mode": 1})
    for level in range(7):
        client._handle_json_message({"fan_control": level, "lamp_control": level})
        assert fan.is_on is (level > 0)
        assert light.is_on is (level > 0)
        assert 0 <= fan.percentage <= 100
        assert 0 <= light.brightness <= 255
        await fan.async_set_percentage(fan.percentage)
        await light.async_turn_on(brightness=light.brightness)
        assert drain(client) == [{"fan_control": level}, {"lamp_control": level}]
    assert light.brightness == 255
    client._handle_json_message({"fan_control": 3, "lamp_control": 2})
    client._handle_json_message({"fan_control": 0, "lamp_control": 0})
    await fan.async_turn_on()
    await light.async_turn_on()
    assert drain(client) == [{"fan_control": 3}, {"lamp_control": 2}]
    await fan.async_turn_on(percentage=50)
    await fan.async_turn_on(percentage=0)
    assert drain(client) == [{"fan_control": 3}, {"fan_control": 0}]
    client.set_fan_speed(100)
    client.set_light_brightness(-1)
    assert drain(client) == [{"fan_control": 6}, {"lamp_control": 0}]
    # Preserve existing mode-Off masking; retained levels are not physical telemetry.
    client._handle_json_message({"main_mode": 0, "fan_control": 4, "lamp_control": 5})
    assert fan.percentage == 0
    assert light.brightness == 0
    assert client.get_state("lamp_control") == 5
    assert not drain(client)
    await coordinator.async_shutdown()


@pytest.mark.parametrize("field,value", [
    ("fan_control", True), ("fan_control", 1.1), ("fan_control", "1"),
    ("fan_control", 7), ("missing", 1), ("main_mode", 4),
])
def test_invalid_commands_are_not_queued(client, field, value):
    """Invalid control data is surfaced before serialization."""
    with pytest.raises(ValueError):
        client.set_state(field, value)
    assert not drain(client)
