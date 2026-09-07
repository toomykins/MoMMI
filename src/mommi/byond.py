from __future__ import annotations

import asyncio
import struct
from urllib.parse import parse_qs

MAGIC = b"\x00\x83"

TopicResponse = float | dict[str, list[str]]


class ByondError(Exception):
    pass


def build_packet(message: bytes) -> bytes:
    if not message.startswith(b"?"):
        message = b"?" + message

    packet = bytearray(MAGIC)

    packet += struct.pack(">H", len(message) + 6)
    packet += b"\x00" * 5
    packet += message
    packet += b"\x00"
    return bytes(packet)


def decode_payload(payload: bytes) -> float | str:
    if not payload:
        raise ByondError("Empty response payload.")

    tag = payload[0]
    if tag == 0x2A:
        if len(payload) < 5:
            raise ByondError("Float response was truncated.")
        return float(struct.unpack(">f", payload[1:5])[0])
    if tag == 0x06:


        return payload[1:].rstrip(b"\x00").decode("utf-8", errors="replace")

    raise ByondError(f"Unknown BYOND data code: 0x{tag:02x}")


def parse_response(payload: bytes) -> TopicResponse:
    decoded = decode_payload(payload)
    if isinstance(decoded, str):
        return parse_qs(decoded)
    return decoded


async def topic(address: str, port: int, message: bytes, timeout: float = 5.0) -> TopicResponse:
    async def _run() -> TopicResponse:
        reader, writer = await asyncio.open_connection(address, port)
        try:
            writer.write(build_packet(message))
            await writer.drain()

            header = await reader.readexactly(2)
            if header != MAGIC:
                raise ByondError(f"Bad response magic: {header!r}")

            (size,) = struct.unpack(">H", await reader.readexactly(2))
            payload = await reader.readexactly(size)
            return parse_response(payload)
        finally:
            writer.close()


            try:
                await writer.wait_closed()
            except (OSError, asyncio.IncompleteReadError):
                pass

    return await asyncio.wait_for(_run(), timeout=timeout)
