"""Provides light control for Proflame fireplaces."""
from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import MAX_LEVEL
from .coordinator import ProflameDataCoordinator
from .entity import ProflameEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry[ProflameDataCoordinator],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the number for Proflame fireplaces."""
    flame = ProflameFlame(config_entry.runtime_data)
    async_add_entities([flame])

class ProflameFlame(ProflameEntity, NumberEntity):
    """Creates a device to control fireplace lights."""

    _attr_native_max_value: float = MAX_LEVEL
    _attr_native_min_value: float = 0
    _attr_native_step: float = 1
    _attr_mode: NumberMode = NumberMode.SLIDER

    def __init__(self, coordinator: ProflameDataCoordinator) -> None:
        """Create new instance of the ProflameFlame class."""
        super().__init__(coordinator, NumberEntityDescription(
            icon='mdi:fire',
            key='burner',
            translation_key='burner',
        ))

    @property
    def native_value(self) -> float | None:
        """Returns the current flame height."""
        return self._device.flame_height

    async def async_set_native_value(self, value: float) -> None:
        """Update the desired height of the flame."""
        self._device.set_flame_height(int(value))
