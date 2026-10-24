"""Config flow for Proflame."""
import logging
import re
import socket
from typing import Any

import probatio as vol

from homeassistant import config_entries
from homeassistant.const import (
    CONF_HOST,
    CONF_IP_ADDRESS,
    CONF_NAME,
    CONF_PORT,
    CONF_UNIQUE_ID,
)
from homeassistant.data_entry_flow import AbortFlow, FlowResult
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .client import ProflameClient
from .const import DEFAULT_DEVICE, DEFAULT_NAME, DEFAULT_PORT, DOMAIN

DISCOVERY_CONFIRM_SCHEMA = vol.Schema({
    vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
})

_LOGGER = logging.getLogger(__name__)


def build_user_schema(user_input: dict[str, Any] | None = None):
    """Generate user schema while respecting previously input data."""
    state = user_input or {}
    return vol.Schema({
        vol.Required(CONF_NAME, default=state.get(CONF_NAME, DEFAULT_NAME)): str,
        vol.Required(CONF_HOST, default=state.get(CONF_HOST, None)): str,
        vol.Required(CONF_PORT, default=state.get(CONF_PORT, DEFAULT_PORT)): cv.port,
        vol.Required(CONF_UNIQUE_ID, default=state.get(CONF_UNIQUE_ID, None)): vol.Match(r".*[A-Za-z0-9].*"),
    })

def resolve_ip(host) -> str:
    """Do DNS resolution for a host to store the IP and prevent duplicate entries."""
    return [x[4][0] for x in socket.getaddrinfo(
        host=host,
        port=None,
        proto=6,
    )][0]

async def test_connectivity(host: str, port: int = DEFAULT_PORT) -> bool:
    """Validate fireplace is connectable."""
    return await ProflameClient.test_connection(host, port)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Proflame fireplace."""

    VERSION = 1
    MINOR_VERSION = 0

    @property
    def _device(self):
        host = self.context.get(CONF_HOST, None)
        ip = self.context.get(CONF_IP_ADDRESS, None)
        if host is not None and ip is not None and host != ip:
            return f"{host} ({ip})"
        if host is None and ip is not None:
            return ip
        return DEFAULT_DEVICE

    async def _async_set_unique_id(self, unique_id: str) -> None:
        """Set the config entry's unique ID and validate no duplicates."""
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()
        # Entity IDs historically strip punctuation from the config entry ID.
        normalized = re.sub('[^A-Za-z0-9]+', '', unique_id).casefold()
        for entry in self._async_current_entries():
            if entry.unique_id and re.sub('[^A-Za-z0-9]+', '', entry.unique_id).casefold() == normalized:
                raise AbortFlow("already_configured")
        self._abort_duplicate_address(
            self.context.get(CONF_HOST), self.context.get(CONF_IP_ADDRESS),
        )

    def _abort_duplicate_address(self, host, ip, exclude_entry_id=None) -> None:
        """Compare host and resolved IP without changing another entry."""
        addresses = {host, ip} - {None}
        for entry in self._async_current_entries():
            if entry.entry_id == exclude_entry_id:
                continue
            if addresses.intersection({
                entry.data.get(CONF_HOST), entry.data.get(CONF_IP_ADDRESS),
            }):
                raise AbortFlow("already_configured")

    async def _async_validate_address(self, host, port) -> str | None:
        """Resolve outside the event loop and verify the read-only handshake."""
        try:
            ip = await self.hass.async_add_executor_job(resolve_ip, host)
        except OSError as err:
            _LOGGER.warning("Cannot resolve Proflame host %s: %s", host, err)
            return None
        if not await test_connectivity(host, port):
            return None
        return ip

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle configuration via the UI."""
        if user_input is None:
            return self.async_show_form(
                data_schema=build_user_schema(),
                errors={},
                step_id='user'
            )

        ip = await self._async_validate_address(
            user_input[CONF_HOST], user_input[CONF_PORT],
        )
        if ip is None:
            _LOGGER.error(
                'Error while connecting to Proflame fireplace: %s:%s',
                user_input[CONF_HOST],
                user_input[CONF_PORT],
            )
            return self.async_show_form(
                step_id='user',
                data_schema=build_user_schema(user_input),
                errors={CONF_HOST: 'cannot_connect'}
            )

        self.context[CONF_HOST] = user_input[CONF_HOST]
        self.context[CONF_IP_ADDRESS] = ip
        await self._async_set_unique_id(user_input[CONF_UNIQUE_ID])
        return self.async_create_entry(
            data={
                CONF_NAME: user_input[CONF_NAME],
                CONF_HOST: user_input[CONF_HOST],
                CONF_IP_ADDRESS: ip,
                CONF_PORT: user_input[CONF_PORT],
                CONF_UNIQUE_ID: self.unique_id,
            },
            title=user_input[CONF_NAME],
        )

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> FlowResult:
        """Handle configuration via the UI."""
        self.context[CONF_HOST] = discovery_info.ip
        self.context[CONF_IP_ADDRESS] = discovery_info.ip
        await self._async_set_unique_id(format_mac(discovery_info.macaddress))
        in_flight = [x['context'].get(CONF_IP_ADDRESS) for x in self._async_in_progress()]
        if discovery_info.ip in in_flight:
            return self.async_abort(reason="already_in_progress")

        # "espressif" is not specific to Proflame hardware.
        if not await test_connectivity(discovery_info.ip, DEFAULT_PORT):
            return self.async_abort(reason="not_proflame_device")

        return await self.async_step_discovery_confirm()

    async def async_step_reconfigure(self, user_input=None):
        """Change the address, preserving existing identities."""
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            ip = await self._async_validate_address(
                user_input[CONF_HOST], user_input[CONF_PORT],
            )
            if ip is None:
                errors[CONF_HOST] = "cannot_connect"
            else:
                self._abort_duplicate_address(
                    user_input[CONF_HOST], ip, entry.entry_id,
                )
                return self.async_update_reload_and_abort(
                    entry, data_updates={**user_input, CONF_IP_ADDRESS: ip},
                )
        state = user_input or entry.data
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST, default=state[CONF_HOST]): str,
                vol.Required(CONF_PORT, default=state[CONF_PORT]): cv.port,
            }),
            errors=errors,
        )

    async def async_step_discovery_confirm(self, user_input=None):
        """Confirm discovery."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_HOST: self.context[CONF_HOST],
                    CONF_IP_ADDRESS: self.context[CONF_IP_ADDRESS],
                    CONF_PORT: DEFAULT_PORT,
                    CONF_UNIQUE_ID: self.unique_id,
                },
                title=user_input[CONF_NAME],
            )

        self.context["title_placeholders"] = {'device': self._device}
        return self.async_show_form(
            data_schema=DISCOVERY_CONFIRM_SCHEMA,
            description_placeholders=self.context["title_placeholders"],
            errors={},
            step_id='discovery_confirm'
        )
