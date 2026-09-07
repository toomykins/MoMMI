from __future__ import annotations

import asyncio
import struct

import pytest

from mommi.commloop import DIGEST_SIZE, MAGIC, CommLoopServer, Status, pack, send

PASSWORD = "hunter2"


@pytest.fixture
async def server():
    received: list[tuple[str, str, object]] = []

    async def dispatch(msg_type: str, meta: str, content: object) -> None:
        received.append((msg_type, meta, content))

    loop_server = CommLoopServer("127.0.0.1", 0, PASSWORD, dispatch)
    loop_server.port = 0
    await loop_server.start()

    assert loop_server._server is not None
    port = loop_server._server.sockets[0].getsockname()[1]
    yield loop_server, port, received
    await loop_server.stop()


def test_pack_layout():
    packet = pack(PASSWORD, "github", "a/b", {"x": 1})
    assert packet[:2] == MAGIC
    length = struct.unpack("!I", packet[2 + DIGEST_SIZE : 6 + DIGEST_SIZE])[0]
    assert len(packet) == 6 + DIGEST_SIZE + length


async def test_round_trip(server):
    _, port, received = server
    code = await send("127.0.0.1", port, PASSWORD, "github", "toomykins/vg", {"event": "push"})
    assert code == 0
    await asyncio.sleep(0.05)
    assert received == [("github", "toomykins/vg", {"event": "push"})]


async def test_wrong_password_is_rejected(server):
    _, port, received = server
    code = await send("127.0.0.1", port, "wrong", "github", "x", {})
    assert code == struct.unpack("!B", Status.BAD_HMAC)[0]
    await asyncio.sleep(0.05)
    assert received == []


async def test_bad_magic_is_rejected(server):
    _, port, received = server
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"\xde\xad" + b"\x00" * 100)
    await writer.drain()
    code = struct.unpack("!B", await reader.readexactly(1))[0]
    writer.close()
    assert code == struct.unpack("!B", Status.BAD_ID)[0]
    assert received == []


async def test_oversized_body_is_refused_not_allocated(server):
    _, port, received = server
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(MAGIC + b"\x00" * DIGEST_SIZE + struct.pack("!I", 2**31))
    await writer.drain()
    code = struct.unpack("!B", await reader.readexactly(1))[0]
    writer.close()
    assert code == struct.unpack("!B", Status.BAD_PACKET)[0]
    assert received == []


async def test_body_split_across_packets_is_reassembled(server):
    _, port, received = server
    payload = {"blob": "x" * 60000}
    packet = pack(PASSWORD, "testing", "meta", payload)

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    for offset in range(0, len(packet), 4096):
        writer.write(packet[offset : offset + 4096])
        await writer.drain()
        await asyncio.sleep(0)
    code = struct.unpack("!B", await reader.readexactly(1))[0]
    writer.close()

    assert code == 0
    await asyncio.sleep(0.05)
    assert received == [("testing", "meta", payload)]


async def test_malformed_json_is_rejected(server):
    _, port, received = server
    import hmac
    from hashlib import sha512

    body = b"{not json"
    digest = hmac.new(PASSWORD.encode(), body, sha512).digest()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(MAGIC + digest + struct.pack("!I", len(body)) + body)
    await writer.drain()
    code = struct.unpack("!B", await reader.readexactly(1))[0]
    writer.close()
    assert code == struct.unpack("!B", Status.BAD_PACKET)[0]
    assert received == []


async def test_missing_required_keys_is_rejected(server):
    _, port, received = server
    import hmac
    import json
    from hashlib import sha512

    body = json.dumps({"type": "github"}).encode()
    digest = hmac.new(PASSWORD.encode(), body, sha512).digest()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(MAGIC + digest + struct.pack("!I", len(body)) + body)
    await writer.drain()
    code = struct.unpack("!B", await reader.readexactly(1))[0]
    writer.close()
    assert code == struct.unpack("!B", Status.BAD_PACKET)[0]
    assert received == []
