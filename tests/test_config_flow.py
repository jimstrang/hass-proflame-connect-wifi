"""Test manual configuration, filtered discovery, and stable reconfiguration."""
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import probatio
import pytest

from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.data_entry_flow import AbortFlow, FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from custom_components.proflame_connect_wifi import config_flow
from custom_components.proflame_connect_wifi.const import DOMAIN


def make_entry(**data):
    """Create an actual HA entry without loading any integration."""
    return ConfigEntry(
        version=1, minor_version=0, domain=DOMAIN, title="Fireplace",
        data={
            "name": "Fireplace", "host": "127.0.0.1", "ip_address": "127.0.0.1",
            "port": 88, "unique_id": "aa:bb:cc:dd:ee:ff", **data,
        },
        unique_id="aa:bb:cc:dd:ee:ff", source="user", options={},
        discovery_keys=MappingProxyType({}), subentries_data=[],
    )


@pytest.fixture
def flow(hass):
    """Run the flow against HA's actual config entry manager."""
    hass.config_entries = ConfigEntries(hass, {})
    result = config_flow.ConfigFlow()
    result.hass = hass
    result.context = {"source": "user"}
    result.handler = DOMAIN
    return result


async def test_manual_configuration_and_validation(flow):
    """Manual DNS/IP resolution runs in the executor and the port is validated."""
    form = await flow.async_step_user()
    assert isinstance(form["data_schema"], probatio.Schema)
    data = {
        "name": "Fireplace", "host": "localhost", "port": 88,
        "unique_id": "aa:bb:cc:dd:ee:ff",
    }
    with patch.object(config_flow, "resolve_ip", return_value="127.0.0.1") as resolve, patch.object(
        config_flow, "test_connectivity", new=AsyncMock(return_value=True),
    ) as probe, patch.object(
        flow.hass, "async_add_executor_job", wraps=flow.hass.async_add_executor_job,
    ) as executor:
        result = await flow.async_step_user(data)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["ip_address"] == "127.0.0.1"
    assert result["data"]["host"] == "localhost"
    assert result["data"]["unique_id"] == "aa:bb:cc:dd:ee:ff"
    probe.assert_awaited_once_with("localhost", 88)
    executor.assert_called_once_with(resolve, "localhost")
    for invalid_port in [0, 65536, -1]:
        with pytest.raises(probatio.Invalid):
            form["data_schema"]({**data, "port": invalid_port})
    for invalid_id in ["", ":", " "]:
        with pytest.raises(probatio.Invalid):
            form["data_schema"]({**data, "unique_id": invalid_id})


@pytest.mark.parametrize("dns_failure", [True, False])
async def test_manual_errors_are_shown(flow, dns_failure):
    """DNS failures and incomplete handshakes stay on the form with an error."""
    with patch.object(
        config_flow, "resolve_ip",
        side_effect=OSError("DNS failed") if dns_failure else None,
        return_value="127.0.0.1",
    ), patch.object(config_flow, "test_connectivity", new=AsyncMock(return_value=False)):
        result = await flow.async_step_user({
            "name": "Fireplace", "host": "localhost", "port": 88, "unique_id": "test",
        })
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"host": "cannot_connect"}


@pytest.mark.parametrize("host,ip", [
    ("127.0.0.1", "127.0.0.1"), ("localhost", "127.0.0.1"),
])
async def test_duplicate_addresses(flow, host, ip):
    """Manual and DHCP flows cannot duplicate an existing host/IP."""
    with patch.object(
        flow, "_async_current_entries", return_value=[make_entry()],
    ), pytest.raises(AbortFlow, match="already_configured"):
        flow._abort_duplicate_address(host, ip)


@pytest.mark.parametrize("unique_id", ["aabbccddeeff", "AA-BB-CC-DD-EE-FF"])
async def test_duplicate_sanitized_identity(flow, unique_id):
    """Preserve historical IDs while rejecting punctuation-only collisions."""
    with patch.object(
        flow, "_async_current_entries", return_value=[make_entry()],
    ), pytest.raises(AbortFlow, match="already_configured"):
        await flow._async_set_unique_id(unique_id)


@pytest.mark.parametrize("speaks_proflame", [True, False])
async def test_dhcp_protocol_filter(flow, speaks_proflame):
    """An arbitrary Espressif DHCP hostname is not enough for discovery."""
    flow.context["source"] = "dhcp"
    discovery = DhcpServiceInfo(
        ip="127.0.0.1", hostname="espressif", macaddress="aabbccddeeff",
    )
    with patch.object(
        config_flow, "test_connectivity", new=AsyncMock(return_value=speaks_proflame),
    ) as probe:
        result = await flow.async_step_dhcp(discovery)
    probe.assert_awaited_once_with("127.0.0.1", 88)
    if speaks_proflame:
        assert result["step_id"] == "discovery_confirm"
        created = await flow.async_step_discovery_confirm({"name": "Fireplace"})
        assert created["data"]["unique_id"] == "aa:bb:cc:dd:ee:ff"
    else:
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "not_proflame_device"


async def test_dhcp_duplicate_checked_after_address_context(flow):
    """A manual entry with another ID is still found by resolved IP."""
    flow.context["source"] = "dhcp"
    discovery = DhcpServiceInfo(
        ip="127.0.0.1", hostname="espressif", macaddress="112233445566",
    )
    with patch.object(
        flow, "_async_current_entries", return_value=[make_entry()],
    ), pytest.raises(AbortFlow, match="already_configured"):
        await flow.async_step_dhcp(discovery)


async def test_reconfigure_preserves_identity(flow):
    """A VLAN migration changes address/port, not unique ID or other settings."""
    entry = make_entry()
    flow.hass.config_entries._entries[entry.entry_id] = entry
    flow.context = {"source": "reconfigure", "entry_id": entry.entry_id}
    with patch.object(flow, "_get_reconfigure_entry", return_value=entry), patch.object(
        flow, "_async_current_entries", return_value=[entry],
    ), patch.object(
        flow, "_async_validate_address", new=AsyncMock(return_value="127.0.0.2"),
    ), patch.object(
        flow.hass.config_entries, "async_schedule_reload",
    ) as reload_entry:
        form = await flow.async_step_reconfigure()
        assert form["step_id"] == "reconfigure"
        result = await flow.async_step_reconfigure({"host": "new-host", "port": 89})
    assert result["reason"] == "reconfigure_successful"
    assert entry.unique_id == "aa:bb:cc:dd:ee:ff"
    assert entry.data == {
        "name": "Fireplace", "host": "new-host", "port": 89,
        "ip_address": "127.0.0.2", "unique_id": "aa:bb:cc:dd:ee:ff",
    }
    reload_entry.assert_called_once_with(entry.entry_id)
