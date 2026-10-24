"""Exercise the actual WebSocket transport entirely on loopback."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from homeassistant.exceptions import HomeAssistantError

from custom_components.proflame_connect_wifi import client_base
from custom_components.proflame_connect_wifi.client_base import ProflameClientBase
from custom_components.proflame_connect_wifi.const import ApiControl


async def wait_until(predicate):
    """Wait with a bounded deadline rather than relying on sleeps."""
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


async def test_initial_partial_malformed_and_cleanup(snapshot):
    """Handshake/state gate availability; partial and malformed traffic is safe."""
    received = []
    release_state = asyncio.Event()
    updated = asyncio.Event()

    async def device(ws):
        received.append(await ws.recv())
        await ws.send(ApiControl.CONN_ACK)
        await release_state.wait()
        await ws.send(json.dumps(snapshot))
        await ws.send("not json")
        await ws.send("[]")
        await ws.send(b"\xff")
        await ws.send(json.dumps({"fan_control": 3, "lamp_control": False}))
        updated.set()
        async for message in ws:
            received.append(message)
            if message == ApiControl.PING:
                await ws.send(ApiControl.PONG)

    async with serve(device, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = ProflameClientBase("test", "127.0.0.1", port, read_only=False)
        changes = []
        client.register_connection_callback(changes.append)
        try:
            await client.open()
            connection = client._connection
            await client.open()
            assert client._connection is connection
            await wait_until(lambda: received)
            assert not client.connected
            with pytest.raises(ConnectionError):
                client.set_state("fan_control", 1)
            release_state.set()
            assert await client.wait_for_state(1)
            await updated.wait()
            await wait_until(lambda: client.get_state("fan_control") == 3)
            assert client.get_state("room_temperature") == 740
            assert client.get_state("lamp_control") == 0
            assert changes == [True]
        finally:
            release_state.set()
            await client.close()
        assert not client.connected
        assert changes == [True, False]
        assert client._connection is None
        assert connection.done()
        assert client._queue.empty()
        assert received == [ApiControl.CONN_SYN]


async def test_reconnect_fresh_state_and_no_stale_commands(monkeypatch, snapshot):
    """A dead session cannot carry queued or in-flight commands into a new one."""
    monkeypatch.setattr(client_base, "RECONNECT_DELAY", 0.01)
    disconnect = asyncio.Event()
    second_session = asyncio.Event()
    release_second_state = asyncio.Event()
    received = [[], []]
    sessions = 0

    async def device(ws):
        nonlocal sessions
        index = sessions
        sessions += 1
        received[index].append(await ws.recv())
        await ws.send(ApiControl.CONN_ACK)
        if index == 0:
            await ws.send(json.dumps({
                **snapshot, "free_heap": 123, "sh_memory_1": 32678,
                "burner_status": "32672",
            }))
            await disconnect.wait()
            await ws.close()
        else:
            second_session.set()
            await release_second_state.wait()
            await ws.send(json.dumps(snapshot))
            async for message in ws:
                received[index].append(message)

    async with serve(device, "127.0.0.1", 0) as server:
        client = ProflameClientBase(
            "test", "127.0.0.1", server.sockets[0].getsockname()[1], read_only=False,
        )
        availability = []
        client.register_connection_callback(availability.append)
        try:
            await client.open()
            assert await client.wait_for_state(1)
            old_socket = client._ws
            blocked = asyncio.Event()

            async def blocked_send(message):
                blocked.set()
                await asyncio.Event().wait()

            monkeypatch.setattr(old_socket, "send", blocked_send)
            client.set_state("fan_control", 1)
            await blocked.wait()
            client.set_state("lamp_control", 2)
            disconnect.set()
            await asyncio.wait_for(second_session.wait(), 2)
            assert not client.connected
            assert not client._state_received.is_set()
            assert client._queue.empty()
            with pytest.raises(ConnectionError):
                client.set_state("fan_control", 6)
            release_second_state.set()
            assert await client.wait_for_state(1)
            assert "free_heap" not in client.full_state
            assert "sh_memory_1" not in client._unknown_state
            assert "burner_status" not in client._unknown_state
            assert availability == [True, False, True]
            client.set_state("fan_control", 3)
            await wait_until(lambda: len(received[1]) == 2)
            assert received[1] == [ApiControl.CONN_SYN, '{"fan_control":3}']
        finally:
            disconnect.set()
            release_second_state.set()
            await client.close()
        await asyncio.wait_for(client._queue.join(), 1)
        assert received[0] == [ApiControl.CONN_SYN]


async def test_initial_state_deadline_reconnects(monkeypatch):
    """An ACK without state is not a successful connection."""
    monkeypatch.setattr(client_base, "INITIAL_STATE_TIMEOUT", 0.03)
    monkeypatch.setattr(client_base, "RECONNECT_DELAY", 0.01)
    sessions = []

    async def device(ws):
        sessions.append(await ws.recv())
        await ws.send(ApiControl.CONN_ACK)
        await ws.wait_closed()

    async with serve(device, "127.0.0.1", 0) as server:
        client = ProflameClientBase(
            "test", "127.0.0.1", server.sockets[0].getsockname()[1],
        )
        try:
            await client.open()
            assert not await client.wait_for_state(0.02)
            await wait_until(lambda: len(sessions) >= 2)
            assert not client.connected
        finally:
            await client.close()


@pytest.mark.parametrize("indexed", [False, True])
@pytest.mark.parametrize("ack_first", [False, True])
@pytest.mark.parametrize("fragmented", [False, True])
async def test_initial_state_across_multiple_messages(
    snapshot, indexed_initial_frames, indexed, ack_first, fragmented,
):
    """Merge streamed state without treating one message or fragment as a full dump."""
    frames = indexed_initial_frames if indexed else [
        {key: snapshot[key]} for key in sorted(snapshot)
    ]
    release = [asyncio.Event() for _ in frames]
    release_ack = asyncio.Event()
    received = []

    async def device(ws):
        received.append(await ws.recv())
        if ack_first:
            await ws.send(ApiControl.CONN_ACK)
        for gate, frame in zip(release, frames, strict=True):
            await gate.wait()
            encoded = json.dumps(frame)
            if fragmented:
                midpoint = len(encoded) // 2
                await ws.send([encoded[:midpoint], encoded[midpoint:]])
            else:
                await ws.send(encoded)
        if not ack_first:
            await release_ack.wait()
            await ws.send(ApiControl.CONN_ACK)
        async for message in ws:
            received.append(message)
            if message == ApiControl.PING:
                await ws.send(ApiControl.PONG)

    async with serve(device, "127.0.0.1", 0) as server:
        client = ProflameClientBase(
            "test", "127.0.0.1", server.sockets[0].getsockname()[1],
        )
        try:
            await client.open()
            await wait_until(lambda: received)
            assert not client.connected
            for gate, frame in zip(release, frames, strict=True):
                gate.set()
                field = frame["status0"] if indexed else next(iter(frame))
                await wait_until(
                    lambda: field in client.full_state or field in client._unknown_state,
                )
                ready = ack_first and {"main_mode", "temperature_unit"} <= client.full_state.keys()
                assert client.connected is ready
                if not ready:
                    with pytest.raises(HomeAssistantError, match="read-only"):
                        client.set_state("fan_control", 1)
            release_ack.set()
            assert await client.wait_for_state(1)
            assert client._uses_indexed_format is indexed
            assert client.get_state("fan_control") == 0
            assert client.get_state("temperature_unit") == 1
            if indexed:
                assert client.get_state("room_temperature") == 720
                assert client._unknown_state["burner_status"] == "32672"
                assert client._unknown_state["en_fan"] == "1"
                assert client._unknown_state["label_aux"] == "001"
                assert "unrecognized" in client._unknown_state
                assert client._unknown_state["unrecognized"] is None
                assert "burner_status" not in client.full_state
            else:
                assert client.full_state == snapshot
            assert client._queue.empty()
        finally:
            for gate in release:
                gate.set()
            release_ack.set()
            await client.close()
    assert received[0] == ApiControl.CONN_SYN
    assert set(received) <= {ApiControl.CONN_SYN, ApiControl.PING}


@pytest.mark.parametrize("indexed", [False, True])
async def test_unsolicited_changes_on_an_existing_subscription(snapshot, indexed):
    """Simulate remote-style changes without claiming a captured RF transition."""
    release_change = asyncio.Event()
    received = []
    update = {"main_mode": 1, "flame_control": 1, "lamp_control": 3}
    messages = []
    for state in (snapshot, update):
        if indexed:
            payload = {}
            for index, (key, value) in enumerate(state.items()):
                payload[f"status{index}"] = key
                payload[f"value{index}"] = str(value)
        else:
            payload = state
        messages.append(json.dumps(payload))

    async def device(ws):
        received.append(await ws.recv())
        await ws.send(ApiControl.CONN_ACK)
        await ws.send(messages[0])
        await release_change.wait()
        await ws.send(messages[1])
        async for message in ws:
            received.append(message)
            if message == ApiControl.PING:
                await ws.send(ApiControl.PONG)

    async with serve(device, "127.0.0.1", 0) as server:
        client = ProflameClientBase(
            "test", "127.0.0.1", server.sockets[0].getsockname()[1],
        )
        try:
            await client.open()
            assert await client.wait_for_state(1)
            assert client.get_state("main_mode") == 0
            release_change.set()
            await wait_until(lambda: client.get_state("lamp_control") == 3)
            assert client.connected
            assert all(client.get_state(key) == value for key, value in update.items())
            assert client.get_state("temperature_unit") == 1
            assert client.get_state("fan_control") == 0
            assert received.count(ApiControl.CONN_SYN) == 1
            assert client._queue.empty()
        finally:
            release_change.set()
            await client.close()
    assert set(received) <= {ApiControl.CONN_SYN, ApiControl.PING}


@pytest.mark.parametrize("field", ["main_mode", "temperature_unit"])
async def test_pongs_do_not_extend_incomplete_initial_state(monkeypatch, field):
    """ACK and live keepalives cannot substitute for either required state field."""
    monkeypatch.setattr(client_base, "INITIAL_STATE_TIMEOUT", 0.1)
    monkeypatch.setattr(client_base, "KEEPALIVE_INTERVAL", 0.01)
    monkeypatch.setattr(client_base, "KEEPALIVE_TIMEOUT", 1)
    pings = []

    async def device(ws):
        assert await ws.recv() == ApiControl.CONN_SYN
        await ws.send(ApiControl.CONN_ACK)
        await ws.send(json.dumps({field: 0}))
        try:
            async for message in ws:
                pings.append(message)
                await ws.send(ApiControl.PONG)
        except ConnectionClosed:
            pass

    async with serve(device, "127.0.0.1", 0) as server:
        client = ProflameClientBase(
            "test", "127.0.0.1", server.sockets[0].getsockname()[1],
            auto_reconnect=False,
        )
        try:
            await client.open()
            await asyncio.wait_for(asyncio.shield(client._connection), 1)
            assert pings and set(pings) == {ApiControl.PING}
            assert not client.connected
            assert not client._state_received.is_set()
            assert not await client.wait_for_state(0.01)
        finally:
            await client.close()


@pytest.mark.parametrize("respond_with_state", [False, True])
async def test_websocket_pong_is_not_application_liveness(
    monkeypatch, snapshot, respond_with_state,
):
    """Only recognized application traffic, not transport pongs alone, keeps a session live."""
    monkeypatch.setattr(client_base, "KEEPALIVE_INTERVAL", 0.01)
    monkeypatch.setattr(client_base, "KEEPALIVE_TIMEOUT", 0.1)
    received = []

    async def device(ws):
        received.append(await ws.recv())
        await ws.send(ApiControl.CONN_ACK)
        await ws.send(json.dumps(snapshot))
        # websockets answers control-frame pings independently of application traffic.
        async for message in ws:
            received.append(message)
            if respond_with_state and message == ApiControl.PING:
                await ws.send(json.dumps({"fan_control": snapshot["fan_control"]}))

    async with serve(device, "127.0.0.1", 0) as server:
        client = ProflameClientBase(
            "test", "127.0.0.1", server.sockets[0].getsockname()[1],
            auto_reconnect=False,
        )
        availability = []
        client.register_connection_callback(availability.append)
        try:
            await client.open()
            assert await client.wait_for_state(1)
            assert client._ws.ping_interval is None
            transport_pong = await client._ws.ping(b"offline-test-only")
            await asyncio.wait_for(transport_pong, 1)
            assert client.connected
            if respond_with_state:
                await wait_until(lambda: received.count(ApiControl.PING) >= 12)
                assert availability == [True]
                assert client.connected
                assert not client._connection.done()
            else:
                await asyncio.wait_for(asyncio.shield(client._connection), 1)
                assert availability == [True, False]
                assert not client.connected
            assert ApiControl.PING in received
            assert set(received) <= {ApiControl.CONN_SYN, ApiControl.PING}
        finally:
            await client.close()


async def test_keepalive_timeout_and_recovery(monkeypatch, snapshot):
    """A silent dongle loses availability, then recovers after a fresh handshake."""
    monkeypatch.setattr(client_base, "KEEPALIVE_INTERVAL", 0.01)
    monkeypatch.setattr(client_base, "KEEPALIVE_TIMEOUT", 0.04)
    monkeypatch.setattr(client_base, "RECONNECT_DELAY", 0.01)
    sessions = 0
    pings = []

    async def device(ws):
        nonlocal sessions
        sessions += 1
        respond = sessions > 1
        assert await ws.recv() == ApiControl.CONN_SYN
        await ws.send(ApiControl.CONN_ACK)
        await ws.send(json.dumps(snapshot))
        try:
            async for message in ws:
                pings.append(message)
                if respond:
                    await ws.send(ApiControl.PONG)
                else:
                    await ws.send("malformed traffic is not a pong")
        except ConnectionClosed:
            pass

    async with serve(device, "127.0.0.1", 0) as server:
        client = ProflameClientBase(
            "test", "127.0.0.1", server.sockets[0].getsockname()[1],
        )
        changes = []
        client.register_connection_callback(changes.append)
        try:
            await client.open()
            assert await client.wait_for_state(1)
            await wait_until(lambda: changes == [True, False, True])
            await asyncio.sleep(0.08)
            assert client.connected
            assert sessions == 2
            assert pings and set(pings) == {ApiControl.PING}
        finally:
            await client.close()


async def test_open_failure_and_cancellation(monkeypatch):
    """Connection refusal can be cancelled during backoff without orphan tasks."""
    monkeypatch.setattr(client_base, "RECONNECT_DELAY", 10)
    attempted = asyncio.Event()

    def refused_connection(*args, **kwargs):
        attempted.set()
        raise OSError("connection refused")

    monkeypatch.setattr(client_base, "connect", refused_connection)
    client = ProflameClientBase("test", "127.0.0.1", 1)
    await client.open()
    task = client._connection
    await asyncio.wait_for(attempted.wait(), 1)
    await asyncio.wait_for(client.close(), 1)
    assert task.done()
    assert not client.connected
    assert client._ws is None


async def test_unexpected_task_failure_is_logged(monkeypatch, caplog):
    """An unexpected task failure is explicit, not hidden until close."""
    client = ProflameClientBase("test", "127.0.0.1")

    async def fail():
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(client, "_connect", fail)
    await client.open()
    await wait_until(lambda: "Connection task failed: unexpected failure" in caplog.text)
    assert not client.connected
    await client.close()


async def test_dispatch_failure_is_not_retried(client):
    """A failed send is surfaced, balanced in the queue, and never replayed."""
    socket = AsyncMock()
    socket.send.side_effect = OSError("lost connection")
    client.set_state("fan_control", 1)
    with pytest.raises(OSError):
        await client._dispatcher(socket)
    await asyncio.wait_for(client._queue.join(), 1)
    socket.send.assert_awaited_once_with('{"fan_control":1}')


@pytest.mark.parametrize("task_kind", ["keepalive", "dispatcher"])
async def test_blocked_sends_have_deadlines(monkeypatch, client, task_kind):
    """Backpressure cannot disable the keepalive deadline indefinitely."""
    monkeypatch.setattr(client_base, "KEEPALIVE_INTERVAL", 0)
    monkeypatch.setattr(client_base, "KEEPALIVE_TIMEOUT", 0.02)
    socket = AsyncMock()
    cancelled = asyncio.Event()

    async def blocked_send(message):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    socket.send.side_effect = blocked_send
    client._last_recv = client_base.time.monotonic()
    if task_kind == "dispatcher":
        client.set_state("fan_control", 2)
        work = client._dispatcher(socket)
    else:
        work = client._keepalive(socket)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(work, 1)
    assert cancelled.is_set()
    await asyncio.wait_for(client._queue.join(), 1)


async def test_async_context_reopen_has_no_automatic_writes(snapshot):
    """Closing and reopening a client always subscribes afresh, without restoration."""
    received = []

    async def device(ws):
        received.append(await ws.recv())
        await ws.send(ApiControl.CONN_ACK)
        await ws.send(json.dumps(snapshot))
        await ws.wait_closed()

    async with serve(device, "127.0.0.1", 0) as server:
        client = ProflameClientBase(
            "test", "127.0.0.1", server.sockets[0].getsockname()[1],
        )
        for _ in range(2):
            async with client:
                assert await client.wait_for_state(1)
                assert client.connected
            assert not client.connected
            assert not client._state_received.is_set()
    assert received == [ApiControl.CONN_SYN, ApiControl.CONN_SYN]


async def test_read_only_probe(snapshot, monkeypatch):
    """Configuration validation sends only handshake and keepalive messages."""
    monkeypatch.setattr(client_base, "KEEPALIVE_INTERVAL", 0.005)
    received = []

    async def device(ws):
        received.append(await ws.recv())
        await ws.send(ApiControl.CONN_ACK)
        await asyncio.sleep(0.02)
        await ws.send(json.dumps(snapshot))
        try:
            async for message in ws:
                received.append(message)
                await ws.send(ApiControl.PONG)
        except ConnectionClosed:
            pass

    async with serve(device, "127.0.0.1", 0) as server:
        assert await ProflameClientBase.test_connection(
            "127.0.0.1", server.sockets[0].getsockname()[1],
        )
    assert received[0] == ApiControl.CONN_SYN
    assert set(received) <= {ApiControl.CONN_SYN, ApiControl.PING}
