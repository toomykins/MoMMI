from __future__ import annotations

import asyncio
import hmac
import json
import logging
import struct
from collections.abc import Awaitable, Callable
from hashlib import sha512
from typing import Any

LOGGER = logging.getLogger(__name__)

MAGIC = b"\x30\x05"
DIGEST_SIZE = sha512().digest_size


MAX_BODY = 8 * 1024 * 1024


CLIENT_TIMEOUT = 30.0


class Status:
    OK = struct.pack("!B", 0)
    BAD_ID = struct.pack("!B", 1)
    BAD_PACKET = struct.pack("!B", 2)
    BAD_HMAC = struct.pack("!B", 3)
    UNKNOWN = struct.pack("!B", 4)


Dispatcher = Callable[[str, str, Any], Awaitable[None]]


def pack(password: str, msg_type: str, meta: str, content: Any) -> bytes:
    body = json.dumps({"type": msg_type, "meta": meta, "cont": content}).encode("utf-8")
    digest = hmac.new(password.encode("utf-8"), body, sha512).digest()
    return MAGIC + digest + struct.pack("!I", len(body)) + body


class CommLoopServer:
    def __init__(self, address: str, port: int, password: str, dispatch: Dispatcher) -> None:
        self.address = address
        self.port = port
        self._password = password.encode("utf-8")
        self._dispatch = dispatch
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self.address, self.port)
        LOGGER.info("Commloop listening on %s:%s", self.address, self.port)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        LOGGER.info("Commloop stopped.")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        try:
            status, message = await asyncio.wait_for(self._read_message(reader), CLIENT_TIMEOUT)
        except asyncio.TimeoutError:
            LOGGER.warning("Commloop client %s timed out.", peer)
            status, message = Status.UNKNOWN, None
        except (asyncio.IncompleteReadError, ConnectionError):

            LOGGER.debug("Commloop client %s disconnected early.", peer)
            await _close(writer)
            return
        except Exception:
            LOGGER.exception("Unhandled error reading from commloop client %s.", peer)
            status, message = Status.UNKNOWN, None

        try:
            writer.write(status)
            await writer.drain()
        except (ConnectionError, OSError):
            LOGGER.debug("Commloop client %s vanished before we could reply.", peer)
        finally:
            await _close(writer)


        if message is not None:
            try:
                await self._dispatch(message["type"], message["meta"], message["cont"])
            except Exception:
                LOGGER.exception("Unhandled error dispatching commloop message %r.", message["type"])

    async def _read_message(self, reader: asyncio.StreamReader) -> tuple[bytes, dict[str, Any] | None]:
        if await reader.readexactly(len(MAGIC)) != MAGIC:
            return Status.BAD_ID, None

        digest = await reader.readexactly(DIGEST_SIZE)
        (length,) = struct.unpack("!I", await reader.readexactly(4))

        if length > MAX_BODY:
            LOGGER.warning("Commloop rejected an oversized body of %d bytes.", length)
            return Status.BAD_PACKET, None

        body = await reader.readexactly(length)

        expected = hmac.new(self._password, body, sha512).digest()
        if not hmac.compare_digest(expected, digest):
            LOGGER.warning("Commloop rejected a message with a bad HMAC.")
            return Status.BAD_HMAC, None

        try:
            message = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return Status.BAD_PACKET, None

        if not isinstance(message, dict) or not {"type", "meta", "cont"} <= message.keys():
            return Status.BAD_PACKET, None
        if not isinstance(message["type"], str) or not isinstance(message["meta"], str):
            return Status.BAD_PACKET, None

        return Status.OK, message


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except (ConnectionError, OSError):
        pass


async def send(
    address: str, port: int, password: str, msg_type: str, meta: str, content: Any
) -> int:
    reader, writer = await asyncio.open_connection(address, port)
    try:
        writer.write(pack(password, msg_type, meta, content))
        await writer.drain()
        return int(struct.unpack("!B", await reader.readexactly(1))[0])
    finally:
        await _close(writer)
