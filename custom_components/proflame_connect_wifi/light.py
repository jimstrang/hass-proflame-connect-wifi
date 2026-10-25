"""Provides light control for Proflame fireplaces."""
from typing import Any

from homeassistant.components.light import (
    ColorMode,
    LightEntity,
    LightEntityDescription,
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
    """Create the lights for Proflame fireplaces."""
    light = ProflameLight(config_entry.runtime_data)
    async_add_entities([light])

class ProflameLight(ProflameEntity, LightEntity):
    """Creates a device to control fireplace lights."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: ProflameDataCoordinator) -> None:
        """Create new instance of the ProflameLight class."""
        super().__init__(coordinator, LightEntityDescription(
            key='light',
            translation_key='light',
        ))

    @property
    def brightness(self) -> int | None:
        """Return the current primary light brightness."""
        brightness = self._device.light_brightness
        if brightness is None:
            return None
        return round(brightness * 255 / MAX_LEVEL)

    @property
    def is_on(self) -> bool | None:
        """Return true if the primary light is on."""
        brightness = self.brightness
        return None if brightness is None else brightness > 0

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the primary light on."""
        brightness = kwargs.get('brightness', None)
        if brightness is None:
            self._device.turn_on_light()
            return
        converted = max(1, round(brightness * MAX_LEVEL / 255)) if brightness else 0
        self._device.set_light_brightness(converted)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the primary light off."""
        self._device.set_light_brightness(0)
