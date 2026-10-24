"""Inspect control output without creating any network connection."""
import pytest

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.proflame_connect_wifi.client import ProflameClient
from custom_components.proflame_connect_wifi.climate import ProflameClimate
from custom_components.proflame_connect_wifi.coordinator import ProflameDataCoordinator
from custom_components.proflame_connect_wifi.light import ProflameLight
from custom_components.proflame_connect_wifi.number import ProflameFlame
from custom_components.proflame_connect_wifi.select import ProflamePilot
from custom_components.proflame_connect_wifi.const import OperatingMode, Preset

from test_entities import drain


async def test_control_serialization(hass, client):
    """Serialize pilot, flame, temperature, and presets using the existing fields."""
    coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
    pilot = ProflamePilot(coordinator)
    flame = ProflameFlame(coordinator)
    climate = ProflameClimate(coordinator)
    await pilot.async_select_option("Continuous")
    await pilot.async_select_option("Intermitent")
    assert drain(client) == [{"pilot_mode": 1}, {"pilot_mode": 0}]
    client._handle_json_message({"main_mode": 1, "flame_control": 2})
    await flame.async_set_native_value(4)
    await climate.async_set_temperature(temperature=69)
    await climate.async_set_preset_mode(Preset.THERMOSTAT)
    await climate.async_set_preset_mode(Preset.SMART)
    assert drain(client) == [
        {"flame_control": 4}, {"temperature_set": 690},
        {"main_mode": 2}, {"main_mode": 3},
    ]
    await coordinator.async_shutdown()


@pytest.mark.parametrize("height", range(7))
async def test_smart_flame_slider_rejects_all_levels_without_writes(hass, client, height):
    """Never leave Smart implicitly or transmit a flame command, including level zero."""
    client._handle_json_message({"main_mode": 2, "flame_control": 3})
    client._handle_json_message({"main_mode": 3})
    assert client._stored_mode_adjustable == OperatingMode.THERMOSTAT
    coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
    previous = client.full_state
    with pytest.raises(ServiceValidationError, match="Select Manual or Thermostat"):
        await ProflameFlame(coordinator).async_set_native_value(height)
    assert drain(client) == []
    assert client.full_state == previous
    assert client.operating_mode == OperatingMode.SMART
    await coordinator.async_shutdown()


@pytest.mark.parametrize("preset,flame,expected", [
    (Preset.OFF, 3, [{"flame_control": 0}, {"main_mode": 1}]),
    (Preset.MANUAL, 3, [{"main_mode": 1}]),
    (Preset.MANUAL, 0, [{"flame_control": 3}, {"main_mode": 1}]),
    (Preset.THERMOSTAT, 3, [{"main_mode": 2}]),
])
async def test_explicit_presets_still_leave_smart(hass, client, preset, flame, expected):
    """Reject direct adjustments without breaking deliberate Off and preset actions."""
    client._handle_json_message({"main_mode": 2, "flame_control": 3})
    client._handle_json_message({"main_mode": 3, "flame_control": flame})
    coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
    await ProflameClimate(coordinator).async_set_preset_mode(preset)
    assert drain(client) == expected
    assert client.operating_mode == OperatingMode.SMART
    await coordinator.async_shutdown()


async def test_smart_climate_off_and_device_off_keep_existing_commands(hass, client):
    """Climate Off and full-device Off remain available in Smart mode."""
    client._handle_json_message({"main_mode": 3, "flame_control": 3})
    coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
    await ProflameClimate(coordinator).async_turn_off()
    assert drain(client) == [{"flame_control": 0}, {"main_mode": 1}]
    client.turn_off()
    assert drain(client) == [{"main_mode": 0}]
    await coordinator.async_shutdown()


@pytest.mark.parametrize("mode", [1, 2])
def test_flame_adjustments_after_explicit_mode_change(client, mode):
    """Manual and Thermostat keep direct level adjustment without a mode override."""
    client._handle_json_message({"main_mode": 3, "flame_control": 1})
    client.set_operating_mode(mode)
    assert drain(client) == [{"main_mode": mode}]
    client._handle_json_message({"main_mode": mode})
    client.set_flame_height(4)
    assert drain(client) == [{"flame_control": 4}]


def test_direct_smart_adjustment_is_blocked_and_read_only_still_takes_priority(client):
    """Shared setter guards all callers and preserves the observation-only contract."""
    client._handle_json_message({"main_mode": 3})
    with pytest.raises(ServiceValidationError, match="Smart mode"):
        client.set_flame_height(4)
    assert drain(client) == []
    read_only = ProflameClient("test", "127.0.0.1")
    read_only._handle_json_message({"main_mode": 3})
    with pytest.raises(HomeAssistantError, match="read-only"):
        read_only.set_flame_height(4)
    assert drain(read_only) == []


@pytest.mark.parametrize("brightness,level", [(0, 0), (1, 1), (127, 3), (128, 3), (254, 6), (255, 6)])
async def test_light_rounding_boundaries(hass, client, brightness, level):
    """Nonzero HA brightness selects a level, without jumping at the midpoint."""
    coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
    await ProflameLight(coordinator).async_turn_on(brightness=brightness)
    assert drain(client) == [{"lamp_control": level}]
    await coordinator.async_shutdown()
