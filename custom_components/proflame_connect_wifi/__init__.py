"""The Proflame integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import ProflameClient
from .const import CONF_READ_ONLY
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


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry[ProflameDataCoordinator],
) -> bool:
    """Set up Proflame from a config entry."""

    client = ProflameClient(
        device_id=entry.unique_id,
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        read_only=entry.data.get(CONF_READ_ONLY, True),
        session=async_get_clientsession(hass),
    )
    coordinator = None
    try:
        await client.open()
        if not await client.wait_for_state():
            raise ConfigEntryNotReady("No acknowledged initial Proflame state")

        coordinator = ProflameDataCoordinator(hass, client, entry.title, entry)
        entry.runtime_data = coordinator

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        if coordinator is not None:
            await coordinator.async_shutdown()
        else:
            await client.close()
        raise
    async def async_stop(event) -> None:
        """Close the local socket when Home Assistant stops."""
        await coordinator.async_shutdown()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_stop),
    )
    return True

async def async_unload_entry(
    hass: HomeAssistant, entry: ConfigEntry[ProflameDataCoordinator],
) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_shutdown()

    return unload_ok
