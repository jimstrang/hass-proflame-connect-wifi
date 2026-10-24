"""Provides light control for Proflame fireplaces."""
from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import PilotMode
from .coordinator import ProflameDataCoordinator
from .entity import ProflameEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry[ProflameDataCoordinator],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the number for Proflame fireplaces."""
    pilot = ProflamePilot(config_entry.runtime_data)
    async_add_entities([pilot])

class ProflamePilot(ProflameEntity, SelectEntity):
    """Creates a device to control fireplace lights."""

    _attr_options =[
        'Continuous',
        'Intermitent',
    ]

    def __init__(self, coordinator: ProflameDataCoordinator) -> None:
        """Create new instance of the ProflameFlame class."""
        super().__init__(coordinator, SelectEntityDescription(
            icon='mdi:candle',
            key='pilot',
            translation_key='pilot',
            entity_category=EntityCategory.CONFIG,
        ))

    @property
    def current_option(self) -> str | None:
        """Return the current pilot mode."""
        current = self._device.pilot_mode
        if current == PilotMode.INTERMITENT:
            return 'Intermitent'
        if current == PilotMode.CONTINUOUS:
            return 'Continuous'
        return None

    async def async_select_option(self, option: str) -> None:
        """Select the pilot mode for the fireplace."""
        if option == 'Intermitent':
            self._device.set_pilot_mode(PilotMode.INTERMITENT)
        if option == 'Continuous':
            self._device.set_pilot_mode(PilotMode.CONTINUOUS)
