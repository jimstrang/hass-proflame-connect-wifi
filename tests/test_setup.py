"""Test setup retry, unload failures, shutdown, and cancellation."""
import asyncio
from importlib import import_module
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components import proflame_connect_wifi as integration
from custom_components.proflame_connect_wifi.const import DOMAIN
from custom_components.proflame_connect_wifi.coordinator import ProflameDataCoordinator


@pytest.fixture
def entry():
    """Provide an entry with unload callback registration."""
    result = MagicMock()
    result.data = {"host": "127.0.0.1", "port": 88}
    result.unique_id = "test"
    result.entry_id = "entry"
    result.title = "Fireplace"
    result.runtime_data = None
    return result


@pytest.fixture
def setup_hass(hass):
    """Use real HA event handling and mock only platform forwarding."""
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    return hass


async def test_setup_initial_timeout_closes_client(setup_hass, entry):
    """A missing initial state raises HA's retry exception without leaking a task."""
    client = MagicMock()
    client.open = AsyncMock()
    client.close = AsyncMock()
    client.wait_for_state = AsyncMock(return_value=False)
    with patch.object(
        integration, "ProflameClient", return_value=client,
    ), pytest.raises(ConfigEntryNotReady):
        await integration.async_setup_entry(setup_hass, entry)
    client.close.assert_awaited_once()
    assert entry.runtime_data is None
    assert DOMAIN not in setup_hass.data
    setup_hass.config_entries.async_forward_entry_setups.assert_not_awaited()


async def test_setup_passes_ha_shared_session(setup_hass, entry, client):
    """Use HA's session without taking ownership of its lifetime."""
    session = integration.async_get_clientsession(setup_hass)
    with patch.object(integration, "ProflameClient", return_value=client) as factory, patch.object(
        client, "open", new=AsyncMock(),
    ):
        assert await integration.async_setup_entry(setup_hass, entry)
    assert factory.call_args.kwargs["session"] is session
    await entry.runtime_data.async_shutdown()
    assert not session.closed


async def test_cancelled_setup_closes_client(setup_hass, entry):
    """Cancellation during the initial wait still closes the transport."""
    client = MagicMock()
    client.open = AsyncMock()
    client.close = AsyncMock()
    client.wait_for_state = AsyncMock(side_effect=asyncio.CancelledError)
    with patch.object(
        integration, "ProflameClient", return_value=client,
    ), pytest.raises(asyncio.CancelledError):
        await integration.async_setup_entry(setup_hass, entry)
    client.close.assert_awaited_once()


@pytest.mark.parametrize("unload_ok", [False, True])
async def test_unload_only_closes_after_platforms_unload(setup_hass, entry, client, unload_ok):
    """Failed unload leaves still-loaded entities connected; successful unload cleans up."""
    with patch.object(integration, "ProflameClient", return_value=client), patch.object(
        client, "open", new=AsyncMock(),
    ):
        assert await integration.async_setup_entry(setup_hass, entry)
    coordinator = entry.runtime_data
    assert coordinator.client is client
    assert DOMAIN not in setup_hass.data
    setup_hass.config_entries.async_unload_platforms.return_value = unload_ok
    assert await integration.async_unload_entry(setup_hass, entry) is unload_ok
    if unload_ok:
        assert not client.connected
        assert not client._connection_callbacks
    else:
        assert entry.runtime_data is coordinator
        assert client.connected
        await coordinator.async_shutdown()


async def test_forwarding_failure_releases_callbacks(setup_hass, entry, client):
    """Even a platform setup error cannot leave an active orphan connection."""
    setup_hass.config_entries.async_forward_entry_setups.side_effect = RuntimeError("platform")
    with patch.object(integration, "ProflameClient", return_value=client), patch.object(
        client, "open", new=AsyncMock(),
    ), pytest.raises(RuntimeError, match="platform"):
        await integration.async_setup_entry(setup_hass, entry)
    assert not client.connected
    assert not client._connection_callbacks
    assert entry.runtime_data.client is client
    assert DOMAIN not in setup_hass.data


async def test_ha_shutdown_closes_client(setup_hass, entry, client):
    """HA stop closes the socket even without a platform unload."""
    with patch.object(integration, "ProflameClient", return_value=client), patch.object(
        client, "open", new=AsyncMock(),
    ):
        assert await integration.async_setup_entry(setup_hass, entry)
    setup_hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await setup_hass.async_block_till_done()
    assert not client.connected
    assert not client._connection_callbacks


async def test_reload_does_not_accumulate_subscriptions(setup_hass, entry, client):
    """Repeated setup/unload with fresh clients keeps callback counts stable."""
    for _ in range(2):
        client._set_connected(True)
        client._state_received.set()
        with patch.object(integration, "ProflameClient", return_value=client), patch.object(
            client, "open", new=AsyncMock(),
        ):
            assert await integration.async_setup_entry(setup_hass, entry)
        assert len(client._connection_callbacks) == 1
        assert len(client._callbacks) == 2
        assert await integration.async_unload_entry(setup_hass, entry)
        assert not client._connection_callbacks
        assert client._callbacks == [client._track_state]


@pytest.mark.parametrize("platform,count", [
    ("climate", 1), ("fan", 1), ("light", 1), ("number", 1),
    ("select", 1), ("sensor", 3), ("switch", 1),
])
async def test_platforms_use_entry_runtime_data(hass, entry, client, platform, count):
    """All platforms share the entry's coordinator without a parallel hass.data registry."""
    coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
    entry.runtime_data = coordinator
    entities = []
    module = import_module(f"custom_components.proflame_connect_wifi.{platform}")
    await module.async_setup_entry(hass, entry, entities.extend)
    assert len(entities) == count
    assert all(entity.coordinator is coordinator for entity in entities)
    assert all(entity._device is client for entity in entities)
    assert DOMAIN not in hass.data
    await coordinator.async_shutdown()
