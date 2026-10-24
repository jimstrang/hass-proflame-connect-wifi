"""The Proflame integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigType
from homeassistant.const import CONF_HOST, CONF_PORT, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .client import ProflameClient
from .const import DOMAIN, PROFLAME_CLIENT, PROFLAME_COORDINATOR
from .coordinator import ProflameDataCoordinator

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.FAN,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Proflame fireplaces."""
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Proflame from a config entry."""

    client = ProflameClient(
        device_id=entry.unique_id,
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
    )
    coordinator = None
    try:
        await client.open()
        if not await client.wait_for_state():
            raise ConfigEntryNotReady("No acknowledged initial Proflame state")

        coordinator = ProflameDataCoordinator(hass, client, entry.title, entry)
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
            PROFLAME_CLIENT: client,
            PROFLAME_COORDINATOR: coordinator,
        }

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        if coordinator is not None:
            await coordinator.async_shutdown()
        else:
            await client.close()
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        raise
    async def async_stop(event) -> None:
        """Close the local socket when Home Assistant stops."""
        await coordinator.async_shutdown()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_stop),
    )
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator = hass.data[DOMAIN][entry.entry_id][PROFLAME_COORDINATOR]
        await coordinator.async_shutdown()
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
