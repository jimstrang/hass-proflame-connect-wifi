"""Provides light control for Proflame fireplaces."""
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import ProflameDataCoordinator
from .entity import ProflameEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry[ProflameDataCoordinator],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the number for Proflame fireplaces."""
    power = ProflamePower(config_entry.runtime_data)
    async_add_entities([power])

class ProflamePower(ProflameEntity, SwitchEntity):
    """Creates a device to control fireplace lights."""

    def __init__(self, coordinator: ProflameDataCoordinator) -> None:
        """Create new instance of the ProflameFlame class."""
        super().__init__(coordinator, SwitchEntityDescription(
            icon='mdi:fireplace',
            key='fireplace',
            translation_key='fireplace',
        ))

    @property
    def is_on(self) -> bool | None:
        """Return reported device power, independently of the heating preset."""
        return self._device.is_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fireplace off."""
        self._device.turn_off()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the fireplace on."""
        self._device.turn_on()
