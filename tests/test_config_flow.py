"""Test manual configuration, filtered discovery, and stable reconfiguration."""
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import probatio
import pytest

from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.components.dhcp.helpers import async_discovered_service_info
from homeassistant.components.dhcp.models import DATA_DHCP, DHCPData, DhcpMatchers
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
        unique_id=data.get("unique_id", "aa:bb:cc:dd:ee:ff"), source="user", options={},
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


@pytest.fixture(autouse=True)
def discovered_devices():
    """Supply HA discovery records without scanning a network."""
    with patch.object(config_flow, "async_discovered_service_info", return_value=[
        DhcpServiceInfo(ip="127.0.0.1", hostname="espressif", macaddress="aabbccddeeff"),
    ]) as discovery:
        yield discovery


async def test_manual_configuration_and_validation(flow):
    """Manual DNS/IP resolution runs in the executor and the port is validated."""
    form = await flow.async_step_user()
    assert isinstance(form["data_schema"], probatio.Schema)
    data = {
        "name": "Fireplace", "host": "localhost", "port": 88,
    }
    with patch.object(config_flow, "resolve_ip", return_value="127.0.0.1") as resolve, patch.object(
        config_flow.ProflameClient, "test_connection", new=AsyncMock(return_value=True),
    ) as probe, patch.object(
        flow.hass, "async_add_executor_job", wraps=flow.hass.async_add_executor_job,
    ) as executor:
        result = await flow.async_step_user(data)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["ip_address"] == "127.0.0.1"
    assert result["data"]["host"] == "localhost"
    assert result["data"]["unique_id"] == "aa:bb:cc:dd:ee:ff"
    assert result["data"]["read_only"] is True
    assert form["data_schema"](data)["read_only"] is True
    probe.assert_awaited_once_with("localhost", 88)
    executor.assert_called_once_with(resolve, "localhost")
    for invalid_port in [0, 65536, -1]:
        with pytest.raises(probatio.Invalid):
            form["data_schema"]({**data, "port": invalid_port})
    with pytest.raises(probatio.Invalid):
        form["data_schema"]({**data, "unique_id": "user-supplied"})


@pytest.mark.parametrize("dns_failure", [True, False])
async def test_manual_errors_are_shown(flow, dns_failure):
    """DNS failures and incomplete handshakes stay on the form with an error."""
    with patch.object(
        config_flow, "resolve_ip",
        side_effect=OSError("DNS failed") if dns_failure else None,
        return_value="127.0.0.1",
    ), patch.object(
        config_flow.ProflameClient, "test_connection", new=AsyncMock(return_value=False),
    ):
        result = await flow.async_step_user({
            "name": "Fireplace", "host": "localhost", "port": 88,
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
        config_flow.ProflameClient, "test_connection",
        new=AsyncMock(return_value=speaks_proflame),
    ) as probe:
        result = await flow.async_step_dhcp(discovery)
    probe.assert_awaited_once_with("127.0.0.1", 88)
    if speaks_proflame:
        assert result["step_id"] == "discovery_confirm"
        created = await flow.async_step_discovery_confirm({"name": "Fireplace"})
        assert created["data"]["unique_id"] == "aa:bb:cc:dd:ee:ff"
        assert created["data"]["read_only"] is True
    else:
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "not_proflame_device"


async def test_dhcp_duplicate_checked_after_address_context(flow):
    """A manual entry with another ID is still found by resolved IP."""
    flow.context["source"] = "dhcp"
    discovery = DhcpServiceInfo(
        ip="127.0.0.1", hostname="espressif", macaddress="102233445566",
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
        "read_only": True,
    }
    reload_entry.assert_called_once_with(entry.entry_id)


@pytest.mark.parametrize("raw,expected", [
    ("aabbccddeeff", "aa:bb:cc:dd:ee:ff"),
    ("AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff"),
    ("AA-BB-CC-DD-EE-FF", "aa:bb:cc:dd:ee:ff"),
    ("", None), ("invalid", None), ("000000000000", None),
    ("ffffffffffff", None), ("112233445566", None),
])
def test_discovery_mac_normalization(raw, expected):
    """Accept valid discovery hardware IDs, not empty/multicast/placeholders."""
    assert config_flow.discovery_mac(raw) == expected


@pytest.mark.parametrize("addresses", [
    [],
    [("127.0.0.2", "aabbccddeeff")],
    [("127.0.0.1", "invalid")],
    [("127.0.0.1", "aabbccddeeff"), ("127.0.0.1", "aabbccddee00")],
    [("127.0.0.1", "aabbccddeeff"), ("127.0.0.1", "")],
])
async def test_missing_or_ambiguous_identity_is_an_error(flow, discovered_devices, addresses):
    """Never invent identity or ask for it manually when the host lacks a unique record."""
    discovered_devices.return_value = [
        DhcpServiceInfo(ip=ip, hostname="espressif", macaddress=mac)
        for ip, mac in addresses
    ]
    data = {"name": "Fireplace", "host": "localhost", "port": 88, "read_only": False}
    with patch.object(
        flow, "_async_validate_address", new=AsyncMock(return_value="127.0.0.1"),
    ):
        result = await flow.async_step_user(data)
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"host": "cannot_identify"}
    assert result["data_schema"](data)["read_only"] is False
    assert flow.unique_id is None


async def test_duplicate_records_for_same_mac_are_not_ambiguous(flow, discovered_devices):
    """Equivalent formatting from different discovery sources still identifies one device."""
    discovered_devices.return_value = [
        DhcpServiceInfo(ip="127.0.0.1", hostname="espressif", macaddress=mac)
        for mac in ("aabbccddeeff", "AA:BB:CC:DD:EE:FF")
    ]
    with patch.object(
        flow, "_async_validate_address", new=AsyncMock(return_value="127.0.0.1"),
    ):
        result = await flow.async_step_user({"name": "Fireplace", "host": "localhost", "port": 88})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["unique_id"] == "aa:bb:cc:dd:ee:ff"


async def test_manual_and_dhcp_identity_agree(flow):
    """Automatic manual setup uses the same MAC format as DHCP discovery."""
    await flow._async_set_unique_id("aa:bb:cc:dd:ee:ff")
    with patch.object(
        flow, "_async_validate_address", new=AsyncMock(return_value="127.0.0.1"),
    ):
        result = await flow.async_step_user({"name": "Fireplace", "host": "localhost", "port": 88})
    assert result["data"]["unique_id"] == "aa:bb:cc:dd:ee:ff"


async def test_manual_setup_preserves_existing_legacy_identity(flow, discovered_devices):
    """A new automatic lookup cannot overwrite a legacy entry's ID or duplicate its address."""
    entry = make_entry(unique_id="existing-legacy-id")
    discovered_devices.return_value = []
    with patch.object(
        flow, "_async_validate_address", new=AsyncMock(return_value="127.0.0.1"),
    ), patch.object(
        flow, "_async_current_entries", return_value=[entry],
    ), pytest.raises(AbortFlow, match="already_configured"):
        await flow.async_step_user({"name": "Fireplace", "host": "localhost", "port": 88})
    assert entry.unique_id == "existing-legacy-id"
    discovered_devices.assert_not_called()


async def test_manual_setup_checks_discovered_mac_duplicates(flow):
    """A changed address must not duplicate an already configured discovery identity."""
    entry = make_entry(host="old-host", ip_address="127.0.0.2")
    with patch.object(
        flow, "_async_validate_address", new=AsyncMock(return_value="127.0.0.1"),
    ), patch.object(
        flow, "_async_current_entries", return_value=[entry],
    ), pytest.raises(AbortFlow, match="already_configured"):
        await flow.async_step_user({"name": "Fireplace", "host": "localhost", "port": 88})


async def test_invalid_dhcp_identity_never_opens_probe(flow):
    """Invalid discovery MACs cannot create a device or trigger a connectivity probe."""
    with patch.object(config_flow.ProflameClient, "test_connection", new=AsyncMock()) as probe:
        result = await flow.async_step_dhcp(DhcpServiceInfo(
            ip="127.0.0.1", hostname="espressif", macaddress="invalid",
        ))
    assert result["reason"] == "cannot_identify"
    probe.assert_not_awaited()


async def test_real_ha_dhcp_lookup_works_without_starting_watchers(flow, discovered_devices):
    """Use HA's public lookup over cached discovery records, not private integration lookups."""
    flow.hass.data[DATA_DHCP] = DHCPData(
        integration_matchers=DhcpMatchers(set(), {}, {}),
        address_data={"aabbccddeeff": {"ip": "127.0.0.1", "hostname": "espressif"}},
    )
    discovered_devices.side_effect = lambda hass: async_discovered_service_info(hass)
    with patch.object(
        flow, "_async_validate_address", new=AsyncMock(return_value="127.0.0.1"),
    ):
        result = await flow.async_step_user({"name": "Fireplace", "host": "localhost", "port": 88})
    assert result["data"]["unique_id"] == "aa:bb:cc:dd:ee:ff"


@pytest.mark.parametrize("read_only", [True, False])
async def test_manual_access_mode_is_persisted(flow, read_only):
    """Persist explicit access choices rather than inferring control ownership."""
    data = {
        "name": "Fireplace", "host": "localhost", "port": 88,
        "read_only": read_only,
    }
    with patch.object(
        flow, "_async_validate_address", new=AsyncMock(return_value="127.0.0.1"),
    ):
        result = await flow.async_step_user(data)
    assert result["data"]["read_only"] is read_only


@pytest.mark.parametrize("read_only", [True, False])
async def test_discovery_access_mode_is_persisted(flow, read_only):
    """Discovery confirmation offers the same explicit write opt-in as manual setup."""
    flow.context.update({"host": "127.0.0.1", "ip_address": "127.0.0.1"})
    form = await flow.async_step_discovery_confirm()
    assert form["data_schema"]({"name": "Fireplace"})["read_only"] is True
    result = await flow.async_step_discovery_confirm({
        "name": "Fireplace", "read_only": read_only,
    })
    assert result["data"]["read_only"] is read_only


@pytest.mark.parametrize("previous,new", [(None, False), (False, True), (True, False)])
async def test_reconfigure_access_mode_preserves_identity(flow, previous, new):
    """Enable/disable editing through entry reload without new entity identities."""
    entry = make_entry(**({} if previous is None else {"read_only": previous}))
    flow.hass.config_entries._entries[entry.entry_id] = entry
    flow.context = {"source": "reconfigure", "entry_id": entry.entry_id}
    with patch.object(flow, "_get_reconfigure_entry", return_value=entry), patch.object(
        flow, "_async_current_entries", return_value=[entry],
    ), patch.object(
        flow, "_async_validate_address", new=AsyncMock(return_value="127.0.0.1"),
    ), patch.object(flow.hass.config_entries, "async_schedule_reload") as reload_entry:
        form = await flow.async_step_reconfigure()
        assert form["data_schema"]({"host": "localhost", "port": 88})["read_only"] is (
            True if previous is None else previous
        )
        result = await flow.async_step_reconfigure({
            "host": entry.data["host"], "port": 88, "read_only": new,
        })
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["read_only"] is new
    assert entry.unique_id == "aa:bb:cc:dd:ee:ff"
    reload_entry.assert_called_once_with(entry.entry_id)
