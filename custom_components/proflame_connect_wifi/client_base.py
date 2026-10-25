"""Low level functionality for interacting with Proflame fireplaces."""
import asyncio
from collections.abc import Callable
import json
import logging
import time

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from homeassistant.exceptions import HomeAssistantError

from .const import (
    DEFAULT_PORT,
    INITIAL_STATE_TIMEOUT,
    KEEPALIVE_INTERVAL,
    KEEPALIVE_TIMEOUT,
    RECONNECT_DELAY,
    ApiAttrs,
    ApiControl,
    STATE_RANGES,
)

_LOGGER = logging.getLogger(__name__)


class _HostLogger(logging.LoggerAdapter):
    """Prefix log messages with the fireplace host."""

    def process(self, msg, kwargs):
        """Add the host prefix."""
        return f"PF[{self.extra}] {msg}", kwargs


class ProflameClientBase:
    """Client used for interacting with Proflame fireplaces."""

    @staticmethod
    async def test_connection(host: str, port: int | None = None) -> bool:
        """Test the connection to the fireplace."""

        client = ProflameClientBase("probe", host, port, auto_reconnect=False)
        try:
            await client.open()
            return await client.wait_for_state()
        finally:
            await client.close()

    def __init__(
        self, device_id, host, port=None, logger=None, auto_reconnect=True,
        *, read_only: bool = True,
    ) -> None:
        """Create new class instance."""
        if not isinstance(read_only, bool):
            raise TypeError("read_only must be a boolean")
        self._read_only = read_only
        self._auto_reconnect = auto_reconnect
        self._device_id = device_id
        self._host = host
        self._port = port or DEFAULT_PORT
        self._logger = _HostLogger(logger or _LOGGER, host)
        self._callbacks = []
        self._connection_callbacks = []

        self._ws = None
        self._connected = False
        self._acknowledged = False
        self._last_recv = 0.0
        self._queue = asyncio.Queue()
        self._connection = None

        self._state = {}
        self._unknown_state = {}
        self._uses_indexed_format = False
        self._state_received = asyncio.Event()

    async def __aenter__(self):
        """Initiate a connection for a context manager."""
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_value, exc_tb):
        """Clean up on context manager exit."""
        await self.close()

    async def _connect(self):
        """Maintain an open connection to the websocket, reconnecting as needed."""
        while True:
            tasks = []
            try:
                async with connect(
                    self.uri, ping_interval=None, open_timeout=INITIAL_STATE_TIMEOUT,
                    close_timeout=2, max_size=65536,
                ) as websocket:
                    self._logger.debug('Connection opened')
                    self._ws = websocket
                    self._state.clear()
                    self._unknown_state.clear()
                    self._state_received.clear()
                    self._uses_indexed_format = False
                    self._acknowledged = False
                    self._last_recv = time.monotonic()
                    await asyncio.wait_for(
                        websocket.send(ApiControl.CONN_SYN), INITIAL_STATE_TIMEOUT,
                    )
                    tasks = [
                        asyncio.create_task(self._listener(websocket)),
                        asyncio.create_task(self._keepalive(websocket)),
                        asyncio.create_task(self._dispatcher(websocket)),
                    ]
                    ready = asyncio.create_task(self._state_received.wait())
                    tasks.append(ready)
                    try:
                        done, _ = await asyncio.wait(
                            tasks, timeout=INITIAL_STATE_TIMEOUT,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if not done:
                            raise TimeoutError("No acknowledged initial state")
                        if ready in done:
                            tasks.remove(ready)
                            done, _ = await asyncio.wait(
                                tasks, return_when=asyncio.FIRST_COMPLETED,
                            )
                        for task in done:
                            task.result()
                    finally:
                        self._set_connected(False)
                        self._ws = None
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
            except (OSError, TimeoutError, WebSocketException) as err:
                self._logger.warning('Connection lost: %s', err)
            finally:
                self._set_connected(False)
                self._ws = None
            if not self._auto_reconnect:
                return
            self._logger.warning('Reconnecting in %s seconds', RECONNECT_DELAY)
            await asyncio.sleep(RECONNECT_DELAY)

    def _set_connected(self, connected: bool) -> None:
        """Update connection state and notify listeners on change."""
        if not connected:
            self._state_received.clear()
            while not self._queue.empty():
                self._queue.get_nowait()
                self._queue.task_done()
        if self._connected == connected:
            return
        self._connected = connected
        for callback in self._connection_callbacks:
            callback(connected)

    def _format_command(self, item: dict) -> str:
        """Format a command for sending to the fireplace.

        Preserve the existing indexed command envelope, including main_mode:5.
        Its control semantics are not verified on the observed flat-JSON dongle.
        Flat firmware receives {"attr": value}, without an added mode command.
        """
        if self._uses_indexed_format:
            # Convert {attr: value} to include main_mode:5 for WiFi control
            if len(item) == 1:
                attr, value = next(iter(item.items()))
                return json.dumps({
                    "control0": "main_mode",
                    "value0": "5",
                    "control1": attr,
                    "value1": str(value)
                }, separators=(',', ':'))
        # Flat firmware uses direct attribute/value pairs.
        return json.dumps(item, separators=(',', ':'))

    async def _dispatcher(self, websocket) -> None:
        """Handle the sending of queued commands."""
        while True:
            item = await self._queue.get()
            try:
                await asyncio.wait_for(
                    websocket.send(self._format_command(item)), KEEPALIVE_TIMEOUT,
                )
            except asyncio.CancelledError:
                raise
            except (OSError, WebSocketException) as err:
                # Don't retry: the connection is being re-established, and
                # replaying fireplace commands later could be surprising.
                self._logger.warning('Failed to send command (%s): %s', item, err)
                raise
            finally:
                self._queue.task_done()

    def _handle_control_message(self, message) -> bool:
        """Process a system control/info message from the websocket."""
        if message == ApiControl.CONN_ACK:
            self._acknowledged = True
            self._logger.debug('Connection acknowledged')
            return True
        elif message == ApiControl.PONG:
            self._logger.debug('Ping acknowledged')
            return True
        else:
            self._logger.warning("Received unexpected control message (%s)", message)
            return False

    def _parse_indexed_format(self, message: dict) -> dict[str, object] | None:
        """Parse indexed status/value messages while preserving unknown values.

        Indexed firmware sends: {"status0": "attr", "value0": "123", ...}
        Only known numeric fields are converted to integers.
        """
        if "status0" not in message:
            return None

        result = {}
        i = 0
        while f"status{i}" in message:
            attr_name = message.get(f"status{i}")
            value_key = f"value{i}"

            if not isinstance(attr_name, str) or not attr_name or value_key not in message:
                self._logger.warning("Ignoring incomplete or invalid indexed field %s", i)
            elif attr_name not in STATE_RANGES:
                result[attr_name] = message[value_key]
            else:
                value = message[value_key]
                try:
                    if isinstance(value, bool) or not isinstance(value, int | str):
                        raise TypeError("Expected an integer or integer string")
                    result[attr_name] = int(value)
                except (ValueError, TypeError):
                    # Shared field validation reports invalid known values.
                    result[attr_name] = value
            i += 1

        return result

    def _handle_json_message(self, message) -> bool:
        """Process a system state message from the websocket."""
        err_msg = "Received unexpected JSON message (%s) - %s"
        if not isinstance(message, dict):
            self._logger.warning(err_msg, "NOT_AN_OBJECT", json.dumps(message))
            return False

        # Try to parse indexed format (status0/value0, status1/value1, etc.)
        parsed = self._parse_indexed_format(message)
        if parsed is not None:
            message = parsed
        valid = {}
        for key, value in message.items():
            if key not in STATE_RANGES:
                self._unknown_state[key] = value
                self._logger.debug('Keeping unknown state field %s internal', key)
                continue
            low, high = STATE_RANGES[key]
            if (
                isinstance(value, bool) or not isinstance(value, int)
                or not low <= value <= high
                or (key == ApiAttrs.OPERATING_MODE and value == 4)
            ):
                self._logger.warning('Ignoring invalid state field %s=%r', key, value)
                continue
            valid[key] = value

        # Commit the whole partial update before callbacks inspect related fields.
        if valid and parsed is not None:
            self._uses_indexed_format = True
        self._state.update(valid)
        for k, v in valid.items():
            for callback in self._callbacks:
                callback(k, v)
        return bool(valid)

    def _handle_message(self, message) -> bool:
        """Process a message from the websocket."""
        if not isinstance(message, str):
            self._logger.warning("Ignoring non-text WebSocket message")
            return False
        try:
            handled = self._handle_json_message(json.loads(message))
        except (ValueError, RecursionError):
            handled = self._handle_control_message(message)
        if (
            self._acknowledged
            and ApiAttrs.OPERATING_MODE in self._state
            and ApiAttrs.TEMPERATURE_UNIT in self._state
        ):
            self._state_received.set()
            self._set_connected(True)
        return handled

    async def _keepalive(self, websocket) -> None:
        """Ping the device periodically and return if it stops responding.

        Returning (or raising) ends the connection and triggers a reconnect.
        """
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL)
            silence = time.monotonic() - self._last_recv
            if silence > KEEPALIVE_TIMEOUT:
                self._logger.warning('No response from device in %.0f seconds', silence)
                return
            self._logger.debug("SEND: %s", ApiControl.PING)
            await asyncio.wait_for(websocket.send(ApiControl.PING), KEEPALIVE_TIMEOUT)

    async def _listener(self, websocket) -> None:
        """Receive messages until the connection closes.

        Connection errors propagate so the connection can be re-established.
        """
        async for message in websocket:
            self._logger.debug('RECV: %s', message)
            if self._handle_message(message):
                self._last_recv = time.monotonic()
        self._logger.warning('Connection closed by device')

    async def close(self) -> None:
        """Close the websocket connection."""
        self._logger.debug('Connection closing')
        self._set_connected(False)
        if self._connection:
            self._connection.cancel()
            await asyncio.gather(self._connection, return_exceptions=True)
            self._connection = None
        self._set_connected(False)
        self._logger.debug('Connection closed')

    @property
    def connected(self) -> bool:
        """Return true if the websocket connection is currently open."""
        return self._connected

    def get_state(self, field: str) -> int | None:
        """Query the state of the fireplace."""
        return self._state.get(field, None)

    async def open(self) -> None:
        """Connect to the Proflame websocket."""
        self._logger.debug('Connection opening')
        if self._connection is None or self._connection.done():
            self._connection = asyncio.create_task(self._connect())
            self._connection.add_done_callback(self._connection_done)

    def _connection_done(self, task) -> None:
        """Surface unexpected failures even when nobody is waiting on the client."""
        if not task.cancelled() and (error := task.exception()) is not None:
            self._logger.error("Connection task failed: %s", error)

    async def wait_for_state(self, timeout: float = INITIAL_STATE_TIMEOUT) -> bool:
        """Wait for state to be received from the device.

        This ensures the device protocol (indexed vs simple) is detected
        before sending any commands.

        :param timeout: Maximum time to wait for state.
        :return: True if state was received, False if timeout.
        """
        try:
            await asyncio.wait_for(self._state_received.wait(), timeout=timeout)
            return True
        except TimeoutError:
            self._logger.warning('Timeout waiting for initial state from device')
            return False

    def register_callback(self, callback) -> Callable[[], None]:
        """Register a callback that will be triggered on state changes."""
        self._callbacks.append(callback)
        return lambda: self._callbacks.remove(callback)

    def register_connection_callback(self, callback) -> Callable[[], None]:
        """Register a callback that will be triggered on connect/disconnect."""
        self._connection_callbacks.append(callback)
        return lambda: self._connection_callbacks.remove(callback)

    @property
    def read_only(self) -> bool:
        """Return whether setting writes are disabled for this client."""
        return self._read_only

    def _check_writable(self) -> None:
        """Reject control actions without queuing or transmitting any setting."""
        if self.read_only:
            raise HomeAssistantError(
                "Proflame is in read-only mode. Use Reconfigure to enable editing. "
                "RF remote settings may override Wi-Fi commands, including Off."
            )

    def set_state(self, field: str, value: int) -> None:
        """Send a state update to the fireplace."""
        self._check_writable()
        if not self.connected:
            raise ConnectionError("Proflame is not ready; command was not queued")
        if field not in STATE_RANGES:
            raise ValueError(f"Unknown Proflame field: {field}")
        low, high = STATE_RANGES[field]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"Invalid Proflame value for {field}: {value!r}")
        if field == ApiAttrs.OPERATING_MODE and value == 4:
            raise ValueError("Unknown Proflame operating mode: 4")
        self._queue.put_nowait({field: value})

    @property
    def device_id(self) -> str:
        """Retrieve the unique ID of the device."""
        return self._device_id

    @property
    def full_state(self) -> dict[str, int]:
        """Retrieve full copy of all know fireplace state."""
        return {**self._state}

    @property
    def uri(self):
        """The formatted URI for connecting to the fireplace websocket."""
        return f"ws://{self._host}:{self._port}"
