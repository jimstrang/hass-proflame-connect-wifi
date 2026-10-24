"""Verify standard MAC metadata and native cross-integration registry lookup."""
from types import MappingProxyType

import pytest

from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.helpers import device_registry as dr

from custom_components.proflame_connect_wifi.client import ProflameClient
from custom_components.proflame_connect_wifi.coordinator import ProflameDataCoordinator


@pytest.mark.parametrize("device_id,expected", [
    ("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:ff"),
    ("AABBCCDDEEFF", "aa:bb:cc:dd:ee:ff"),
    ("AA-BB-CC-DD-EE-FF", "aa:bb:cc:dd:ee:ff"),
    ("legacy-fireplace", None), ("000000000000", None),
    ("ffffffffffff", None), ("112233445566", None),
])
async def test_device_connection_preserves_existing_identity(hass, device_id, expected):
    """Add normalized connections only for valid MAC identities, never rewrite legacy IDs."""
    client = ProflameClient(device_id, "127.0.0.1")
    coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
    assert coordinator.device_info["identifiers"] == {("proflame_connect_wifi", device_id)}
    if expected is None:
        assert "connections" not in coordinator.device_info
    else:
        assert coordinator.device_info["connections"] == {(dr.CONNECTION_NETWORK_MAC, expected)}
    await coordinator.async_shutdown()


@pytest.mark.parametrize("previously_registered", [False, True])
async def test_registry_associates_mac_without_replacing_devices(hass, client, previously_registered):
    """Expose both integration records through their MAC without forcing a destructive merge."""
    hass.config_entries = ConfigEntries(hass, {})
    entries = []
    for domain in ("unifi", "proflame_connect_wifi"):
        entry = ConfigEntry(
            version=1, minor_version=0, domain=domain, title=domain,
            data={}, unique_id=f"{domain}-test", source="user", options={},
            discovery_keys=MappingProxyType({}), subentries_data=[],
        )
        hass.config_entries._entries[entry.entry_id] = entry
        entries.append(entry)
    dr.async_setup(hass)
    await dr.async_load(hass, load_empty=True)
    registry = dr.async_get(hass)
    mac_connection = (dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff")
    network_device = registry.async_get_or_create(
        config_entry_id=entries[0].entry_id, connections={mac_connection}, name="Network device",
    )
    coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
    old_device = None
    if previously_registered:
        old_device = registry.async_get_or_create(
            config_entry_id=entries[1].entry_id,
            identifiers=coordinator.device_info["identifiers"], name="Fireplace",
        )
    device = registry.async_get_or_create(
        config_entry_id=entries[1].entry_id, **coordinator.device_info,
    )
    assert device.identifiers == coordinator.device_info["identifiers"]
    assert mac_connection in device.connections
    if old_device is not None:
        assert device.id == old_device.id
    matches = registry.async_get_devices(connections={mac_connection})
    assert {match.id for match in matches} == {network_device.id, device.id}
    assert registry.async_get(network_device.id) is not None
    assert registry.async_get_device_by_connection(mac_connection, entries[1].entry_id).id == device.id
    await coordinator.async_shutdown()
