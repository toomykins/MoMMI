from __future__ import annotations

import asyncio

import pytest

from mommi.commloop import CommLoopServer

PASSWORD = b"hunter2"


def legacy_send(address, key, type, meta, content):
    import hmac
    import json
    import struct
    from hashlib import sha512
    from socket import AF_INET, SOCK_STREAM, socket

    msg = json.dumps({"type": type, "meta": meta, "cont": content}).encode("UTF-8")
    h = hmac.new(key, msg, sha512)
    packet = b"\x30\x05"
    packet += h.digest()
    packet += struct.pack("!I", len(msg))
    packet += msg

    with socket(AF_INET, SOCK_STREAM) as s:
        s.settimeout(5)
        s.connect(address)
        s.sendall(packet)
        return struct.unpack("!B", s.recv(1))[0]


@pytest.fixture
async def server():
    received = []

    async def dispatch(msg_type, meta, content):
        received.append((msg_type, meta, content))

    loop_server = CommLoopServer("127.0.0.1", 0, PASSWORD.decode(), dispatch)
    await loop_server.start()
    assert loop_server._server is not None
    port = loop_server._server.sockets[0].getsockname()[1]
    yield port, received
    await loop_server.stop()


async def test_v2_sender_is_accepted(server):
    port, received = server
    content = {"pass": "secret", "content": "Round has ended.", "ping": False}

    code = await asyncio.to_thread(
        legacy_send, ("127.0.0.1", port), PASSWORD, "gamenudge", "server_status", content
    )

    assert code == 0, "v2's sender got a non-OK status byte"
    await asyncio.sleep(0.05)
    assert received == [("gamenudge", "server_status", content)]


async def test_v2_sender_with_wrong_key_is_rejected(server):
    port, received = server
    code = await asyncio.to_thread(
        legacy_send, ("127.0.0.1", port), b"wrongkey", "gamenudge", "ick", {}
    )
    assert code == 3, "expected the HMAC failure code v2 documented"
    assert received == []
