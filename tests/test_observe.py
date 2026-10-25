"""Exercise the RF capture tool with loopback-only, non-actuating mock traffic."""
import asyncio
from datetime import datetime
import json
from pathlib import Path
import stat
import subprocess
import sys
from unittest.mock import Mock

import pytest
from aiohttp import ClientError
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from scripts import observe


def records(path):
    """Read completed JSONL records from a test capture."""
    return [json.loads(line) for line in path.read_text().splitlines()]


async def test_capture_records_raw_traffic_and_only_benign_sends(monkeypatch, tmp_path):
    """Preserve exact payloads, private permissions, timing, and the send allowlist."""
    monkeypatch.setattr(observe, "KEEPALIVE_INTERVAL", 0.01)
    sent = []
    raw = '{"main_mode":0,"temperature_unit":1,"burner_status":"0032678","unknown":null}'

    async def device(ws):
        async for message in ws:
            sent.append(message)
            if message == "PROFLAMECONNECTION":
                await ws.send("PROFLAMECONNECTIONOPEN")
                await ws.send(raw)
                await ws.send(b"\x00\xff")
            elif message == "PROFLAMEPING":
                await ws.send("PROFLAMEPONG")
            else:
                pytest.fail(f"Unexpected observer send: {message}")

    connect = Mock(wraps=observe.ClientSession.ws_connect)
    monkeypatch.setattr(
        observe.ClientSession, "ws_connect",
        lambda self, *args, **kwargs: connect(self, *args, **kwargs),
    )
    output = tmp_path / "capture.jsonl"
    async with serve(device, "127.0.0.1", 0) as server:
        await observe.capture(
            f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/", output, 0.1,
        )
    assert sent.count("PROFLAMECONNECTION") == 1
    assert sent.count("PROFLAMEPING") >= 1
    assert set(sent) == {"PROFLAMECONNECTION", "PROFLAMEPING"}
    assert connect.call_count == 1
    assert connect.call_args.kwargs["heartbeat"] is None
    captured = records(output)
    assert [row["message"] for row in captured if row["event"] == "send"] == sent
    assert any(row.get("message") == raw for row in captured)
    assert any(row.get("binary_hex") == "00ff" for row in captured)
    assert captured[-1]["event"] == "end"
    assert captured[-1]["reason"] == "duration"
    assert [row["elapsed_seconds"] for row in captured] == sorted(
        row["elapsed_seconds"] for row in captured
    )
    assert all(datetime.fromisoformat(row["timestamp"]).utcoffset().total_seconds() == 0 for row in captured)
    assert stat.S_IMODE(output.stat().st_mode) & 0o077 == 0


def test_documented_cli_help_needs_no_endpoint():
    """Exercise the actual standalone entry point without opening a connection."""
    result = subprocess.run(
        [sys.executable, str(Path(observe.__file__).resolve()), "--help"],
        check=True, capture_output=True, text=True, timeout=5,
    )
    assert "--url" in result.stdout
    assert "--output" in result.stdout
    assert "--duration" in result.stdout


@pytest.mark.parametrize("uri", [
    "wss://localhost:88/", "http://localhost:88/", "ws://localhost/",
    "ws://user:password@localhost:88/", "ws://localhost:88/settings",
    "ws://localhost:88/?refresh=1", "ws://localhost:0/",
])
async def test_invalid_endpoint_is_rejected_before_connecting(monkeypatch, tmp_path, uri):
    """Do not connect or create a log for unsupported endpoints."""
    connect = Mock()
    monkeypatch.setattr(observe.ClientSession, "ws_connect", connect)
    output = tmp_path / "capture.jsonl"
    with pytest.raises(ValueError):
        await observe.capture(uri, output)
    connect.assert_not_called()
    assert not output.exists()


@pytest.mark.parametrize("duration", [0, -1, 301, float("inf"), float("nan")])
async def test_duration_is_bounded(monkeypatch, tmp_path, duration):
    """Reject invalid or unbounded durations before any socket activity."""
    connect = Mock()
    monkeypatch.setattr(observe.ClientSession, "ws_connect", connect)
    with pytest.raises(ValueError):
        await observe.capture("ws://localhost:88/", tmp_path / "capture.jsonl", duration)
    connect.assert_not_called()


async def test_existing_capture_is_never_overwritten(monkeypatch, tmp_path):
    """Fail before connecting if the target log already exists."""
    output = tmp_path / "capture.jsonl"
    output.write_text("existing")
    connect = Mock()
    monkeypatch.setattr(observe.ClientSession, "ws_connect", connect)
    with pytest.raises(FileExistsError):
        await observe.capture("ws://localhost:88/", output)
    assert output.read_text() == "existing"
    connect.assert_not_called()


async def test_early_close_is_an_error_without_reconnect(tmp_path):
    """An interrupted observation window is explicit and never silently restarted."""
    sent = []

    async def device(ws):
        sent.append(await ws.recv())
        await ws.send("PROFLAMECONNECTIONOPEN")
        await ws.close()

    output = tmp_path / "capture.jsonl"
    async with serve(device, "127.0.0.1", 0) as server:
        with pytest.raises(ClientError, match="closed before capture deadline"):
            await observe.capture(
                f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/", output, 1,
            )
    assert sent == ["PROFLAMECONNECTION"]
    assert any(row["event"] == "error" for row in records(output))
    assert records(output)[-1]["reason"] == "error"


@pytest.mark.parametrize("reply", ["PROFLAMEPONG", "PROFLAMECONNECTIONOPEN"])
async def test_missing_ack_or_later_silence_stops_capture(monkeypatch, tmp_path, reply):
    """Distinguish missing handshake from a connection that stops application replies."""
    monkeypatch.setattr(observe, "ACK_TIMEOUT", 0.1)
    monkeypatch.setattr(observe, "SILENCE_TIMEOUT", 0.15)
    monkeypatch.setattr(observe, "KEEPALIVE_INTERVAL", 0.01)

    async def device(ws):
        assert await ws.recv() == "PROFLAMECONNECTION"
        await ws.send(reply)
        async for message in ws:
            assert message == "PROFLAMEPING"
            if reply == "PROFLAMEPONG":
                await ws.send(reply)

    output = tmp_path / "capture.jsonl"
    expected = "handshake acknowledgement" if reply == "PROFLAMEPONG" else "silence deadline"
    async with serve(device, "127.0.0.1", 0) as server:
        with pytest.raises(TimeoutError, match=expected):
            await observe.capture(
                f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/", output, 0.5,
            )
    assert records(output)[-1]["reason"] == "error"


async def test_cancelled_capture_closes_and_records_cancellation(tmp_path):
    """Cancellation stops the observer and closes its one socket."""
    started = asyncio.Event()

    async def device(ws):
        assert await ws.recv() == "PROFLAMECONNECTION"
        started.set()
        await ws.wait_closed()

    output = tmp_path / "capture.jsonl"
    async with serve(device, "127.0.0.1", 0) as server:
        task = asyncio.create_task(observe.capture(
            f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/", output, 1,
        ))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert records(output)[-1]["reason"] == "cancelled"


async def test_control_frames_do_not_extend_application_silence(monkeypatch, tmp_path):
    """Transport ping/pong traffic cannot keep a silent observation session alive."""
    monkeypatch.setattr(observe, "KEEPALIVE_INTERVAL", 0.01)
    monkeypatch.setattr(observe, "SILENCE_TIMEOUT", 0.08)
    control_frames = []

    async def device(ws):
        assert await ws.recv() == "PROFLAMECONNECTION"
        await ws.send("PROFLAMECONNECTIONOPEN")
        try:
            while True:
                await ws.ping(b"control-only")
                await ws.pong(b"control-only")
                control_frames.append(True)
                await asyncio.sleep(0.005)
        except ConnectionClosed:
            pass

    output = tmp_path / "capture.jsonl"
    async with serve(device, "127.0.0.1", 0) as server:
        with pytest.raises(TimeoutError, match="silence deadline"):
            await observe.capture(
                f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/", output, 0.5,
            )
    assert len(control_frames) >= 2
    assert [row["message"] for row in records(output) if row["event"] == "receive"] == [
        "PROFLAMECONNECTIONOPEN",
    ]
    assert records(output)[-1]["reason"] == "error"
