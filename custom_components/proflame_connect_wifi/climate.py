"""Provides light control for Proflame fireplaces."""
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityDescription,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
    UnitOfTemperature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import (
    MAX_TEMPERATURE,
    MIN_TEMPERATURE,
    Preset,
)
from .coordinator import ProflameDataCoordinator
from .entity import ProflameEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry[ProflameDataCoordinator],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the number for Proflame fireplaces."""
    climate = ProflameClimate(config_entry.runtime_data)
    async_add_entities([climate])

class ProflameClimate(ProflameEntity, ClimateEntity):
    """Creates a device to control fireplace lights."""

    _attr_hvac_modes = [
        HVACMode.HEAT,
        HVACMode.OFF,
    ]
    _attr_preset_modes = list(Preset)
    _attr_supported_features = (
        ClimateEntityFeature.PRESET_MODE |
        ClimateEntityFeature.TARGET_TEMPERATURE |
        ClimateEntityFeature.TURN_ON |
        ClimateEntityFeature.TURN_OFF
    )
    _attr_target_temperature_step = 1.0

    def __init__(self, coordinator: ProflameDataCoordinator) -> None:
        """Create new instance of the ProflameFlame class."""
        super().__init__(coordinator, ClimateEntityDescription(
            key='climate',
            translation_key='climate',
        ))
        unit = self._device.temperature_unit
        if unit is None:
            raise ValueError("Cannot create a climate entity before the device reports its unit")
        self._attr_temperature_unit = unit

    @property
    def current_temperature(self) -> float | None:
        """Return the current fireplace mode."""
        return self._device.current_temperature

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current fireplace mode."""
        return self._device.hvac_action

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the current fireplace mode."""
        return self._device.hvac_mode

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        return TemperatureConverter.convert(
            MAX_TEMPERATURE, UnitOfTemperature.CELSIUS, self.temperature_unit,
        )

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        return TemperatureConverter.convert(
            MIN_TEMPERATURE, UnitOfTemperature.CELSIUS, self.temperature_unit,
        )

    @property
    def preset_mode(self) -> str | None:
        """Return the current fireplace mode."""
        return self._device.preset

    @property
    def target_temperature(self) -> float | None:
        """Return the current fireplace mode."""
        return self._device.target_temperature

    @property
    def temperature_unit(self) -> str:
        """Return the unit of measurement used by the platform."""
        if (unit := self._device.temperature_unit) is not None:
            self._attr_temperature_unit = unit
        # HA renders min/max capabilities even while the connection is unavailable.
        return self._attr_temperature_unit

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        temperature = kwargs.get('temperature', None)
        if temperature is None:
            raise ValueError("A target temperature is required")
        self._device.set_target_temperature(temperature)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        if hvac_mode == HVACMode.OFF:
            self._device.set_preset(Preset.OFF)
        if hvac_mode == HVACMode.HEAT:
            self._device.heat()

    async def async_turn_on(self) -> None:
        """Restore the existing heat configuration."""
        self._device.heat()

    async def async_turn_off(self) -> None:
        """Use the climate Off preset, not the full-device power switch."""
        self._device.set_preset(Preset.OFF)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        self._device.set_preset(Preset(preset_mode))
