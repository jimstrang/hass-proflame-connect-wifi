"""Provides fan control for Proflame fireplaces."""
import math
from typing import Any

from homeassistant.components.fan import (
    FanEntity,
    FanEntityDescription,
    FanEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import MAX_FAN_SPEED
from .coordinator import ProflameDataCoordinator
from .entity import ProflameEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry[ProflameDataCoordinator],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the fans for Proflame fireplaces."""
    fan = ProflameFan(config_entry.runtime_data)
    async_add_entities([fan])

class ProflameFan(ProflameEntity, FanEntity):
    """Creates a device to control fireplace fan."""

    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )
    _attr_speed_count = MAX_FAN_SPEED

    def __init__(self, coordinator: ProflameDataCoordinator) -> None:
        """Create new instance of the ProflameLight class."""
        super().__init__(coordinator, FanEntityDescription(
            key='fan',
            translation_key='fan',
        ))

    @property
    def fan_speed(self) -> int | None:
        """Return the current numeric speed of the fan."""
        return self._device.fan_speed

    @property
    def is_on(self) -> bool | None:
        """Return true if device is on."""
        fan_speed = self.fan_speed
        return None if fan_speed is None else fan_speed > 0

    @property
    def percentage(self) -> int | None:
        """Return the current speed percentage."""
        speed = self.fan_speed
        return None if speed is None else math.floor(speed * (100 / MAX_FAN_SPEED))

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed of the fireplace fan."""
        speed = math.ceil(percentage / (100 / MAX_FAN_SPEED))
        self._device.set_fan_speed(speed)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn the fan on."""
        if percentage is None:
            self._device.turn_on_fan()
        else:
            await self.async_set_percentage(percentage)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        self._device.turn_off_fan()
