"""Capture a bounded, read-only Proflame session without reconnecting or polling."""
import argparse
import asyncio
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientWSTimeout, WSMsgType

HANDSHAKE = "PROFLAMECONNECTION"
ACK = "PROFLAMECONNECTIONOPEN"
PING = "PROFLAMEPING"
KEEPALIVE_INTERVAL = 5
ACK_TIMEOUT = 10
SILENCE_TIMEOUT = 30
MAX_DURATION = 300

_LOGGER = logging.getLogger(__name__)


async def capture(uri: str, output: Path, duration: float = 120) -> None:
    """Record raw messages and timing while sending only a handshake and keepalives."""
    endpoint = urlsplit(uri)
    if (
        endpoint.scheme != "ws" or not endpoint.hostname or not endpoint.port
        or endpoint.path not in {"", "/"} or endpoint.query or endpoint.fragment
        or endpoint.username is not None
    ):
        raise ValueError("Use ws://host:port/ without credentials, parameters, or other paths")
    if not 0 < duration <= MAX_DURATION:
        raise ValueError(f"Duration must be greater than zero and at most {MAX_DURATION} seconds")

    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + duration
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        def record(event: str, **details) -> None:
            entry = {
                "timestamp": datetime.now(UTC).isoformat(),
                "elapsed_seconds": round(loop.time() - started, 6),
                "event": event,
                **details,
            }
            stream.write(json.dumps(entry, ensure_ascii=True) + "\n")
            stream.flush()

        record("start", duration_seconds=duration)
        reason = "error"
        try:
            async with ClientSession() as session:
                async with asyncio.timeout(min(ACK_TIMEOUT, duration)):
                    websocket = await session.ws_connect(
                        uri, heartbeat=None, timeout=ClientWSTimeout(ws_close=2),
                        max_msg_size=65536,
                    )
                async with websocket:
                    await _record_session(websocket, record, deadline, loop)
                reason = "duration"
        except asyncio.CancelledError:
            reason = "cancelled"
            raise
        except (OSError, ClientError) as err:
            record("error", error_type=type(err).__name__, message=str(err))
            raise
        finally:
            record("end", reason=reason)
    _LOGGER.info("Capture finished at its duration limit. Private log: %s", output)


async def _record_session(websocket, record, deadline, loop) -> None:
    """Observe one socket until its deadline, without reconnecting or writing settings."""
    async def send(message: str) -> None:
        if message not in {HANDSHAKE, PING}:
            raise ValueError("The observer cannot send setting changes")
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError("Capture deadline reached before sending")
        await asyncio.wait_for(websocket.send_str(message), min(ACK_TIMEOUT, remaining))
        record("send", message=message)

    await send(HANDSHAKE)
    acknowledged = False
    last_received = loop.time()
    ack_deadline = last_received + ACK_TIMEOUT
    next_ping = last_received + KEEPALIVE_INTERVAL
    while loop.time() < deadline:
        now = loop.time()
        if not acknowledged and now >= ack_deadline:
            raise TimeoutError("No Proflame handshake acknowledgement")
        if now >= last_received + SILENCE_TIMEOUT:
            raise TimeoutError("No application messages before the silence deadline")
        if now >= next_ping:
            await send(PING)
            next_ping = loop.time() + KEEPALIVE_INTERVAL
        wake_at = min(deadline, next_ping, last_received + SILENCE_TIMEOUT)
        if not acknowledged:
            wake_at = min(wake_at, ack_deadline)
        try:
            async with asyncio.timeout_at(wake_at):
                frame = await websocket.receive()
        except TimeoutError:
            continue
        if frame.type == WSMsgType.ERROR:
            raise ClientError(f"WebSocket receive failed: {frame.data}")
        if frame.type in {WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED}:
            raise ClientError("WebSocket closed before capture deadline")
        if frame.type not in {WSMsgType.TEXT, WSMsgType.BINARY}:
            continue
        message = frame.data
        last_received = loop.time()
        if isinstance(message, bytes):
            record("receive", binary_hex=message.hex())
        else:
            record("receive", message=message)
            if message == ACK and not acknowledged:
                acknowledged = True
                _LOGGER.info("Handshake acknowledged; establish a baseline before any RF adjustment")
    if not acknowledged:
        raise TimeoutError("Capture ended without a Proflame handshake acknowledgement")


def main() -> int:
    """Run the observer only when explicitly invoked with an endpoint and log path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Root endpoint, ws://host:port/")
    parser.add_argument("--output", required=True, type=Path, help="New private JSONL file outside the repository")
    parser.add_argument("--duration", type=float, default=120, help="Capture seconds, greater than 0 and at most 300")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        asyncio.run(capture(args.url, args.output, args.duration))
    except KeyboardInterrupt:
        _LOGGER.info("Capture cancelled; connection closed")
        return 130
    except (OSError, ValueError, ClientError) as err:
        _LOGGER.error("Capture failed: %s", err)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
