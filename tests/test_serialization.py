"""Inspect control output without creating any network connection."""
import pytest

from custom_components.proflame_connect_wifi.climate import ProflameClimate
from custom_components.proflame_connect_wifi.coordinator import ProflameDataCoordinator
from custom_components.proflame_connect_wifi.light import ProflameLight
from custom_components.proflame_connect_wifi.number import ProflameFlame
from custom_components.proflame_connect_wifi.select import ProflamePilot
from custom_components.proflame_connect_wifi.const import Preset

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


@pytest.mark.parametrize("brightness,level", [(0, 0), (1, 1), (127, 3), (128, 3), (254, 6), (255, 6)])
async def test_light_rounding_boundaries(hass, client, brightness, level):
    """Nonzero HA brightness selects a level, without jumping at the midpoint."""
    coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
    await ProflameLight(coordinator).async_turn_on(brightness=brightness)
    assert drain(client) == [{"lamp_control": level}]
    await coordinator.async_shutdown()
