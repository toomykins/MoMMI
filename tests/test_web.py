from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from mommi.config import ChangelogConfig, WebConfig
from mommi.web import WebApp, verify_github_signature

SECRET = "webhooksecret"


class FakeBot:

    def __init__(self, web_config: WebConfig) -> None:
        self.dispatched: list[tuple[str, str, object]] = []
        self.config = SimpleNamespace(main=SimpleNamespace(web=web_config))

    async def dispatch_comm(self, msg_type: str, meta: str, content: object) -> None:
        self.dispatched.append((msg_type, meta, content))


@pytest.fixture
async def client_and_bot():
    config = WebConfig(**{"github-key": SECRET, "changelog": ChangelogConfig()})
    bot = FakeBot(config)
    app = WebApp(bot).build()  # type: ignore[arg-type]
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client, bot
    await client.close()


def sign_sha256(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def sign_sha1(body: bytes) -> str:
    return "sha1=" + hmac.new(SECRET.encode(), body, hashlib.sha1).hexdigest()


def test_verifies_sha256():
    body = b'{"zen": "hi"}'
    assert verify_github_signature(SECRET, body, {"X-Hub-Signature-256": sign_sha256(body)})


def test_verifies_legacy_sha1():
    body = b'{"zen": "hi"}'
    assert verify_github_signature(SECRET, body, {"X-Hub-Signature": sign_sha1(body)})


def test_rejects_bad_signature():
    body = b'{"zen": "hi"}'
    assert not verify_github_signature(SECRET, body, {"X-Hub-Signature-256": "sha256=" + "0" * 64})


def test_rejects_missing_signature():
    assert not verify_github_signature(SECRET, b"{}", {})


def test_rejects_unknown_algorithm():
    body = b"{}"
    assert not verify_github_signature(SECRET, body, {"X-Hub-Signature": "sha2=deadbeef"})


async def test_healthcheck(client_and_bot):
    client, _ = client_and_bot
    response = await client.get("/twohundred")
    assert response.status == 200
    assert await response.text() == "hi BYOND!"


async def test_github_ping_pongs(client_and_bot):
    client, _ = client_and_bot
    body = json.dumps({"zen": "Non-blocking is better than blocking."}).encode()
    response = await client.post(
        "/changelog",
        data=body,
        headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": sign_sha256(body)},
    )
    assert response.status == 200
    assert await response.text() == "pong"


@pytest.mark.parametrize(
    "path", ["/changelog", "/mommi/github", "/mommi/github/1", "/dev/git_hooks/webmommi"]
)
async def test_all_legacy_webhook_paths_work(client_and_bot, path):
    client, bot = client_and_bot
    payload = {"repository": {"full_name": "toomykins/vgstation13"}, "commits": []}
    body = json.dumps(payload).encode()
    response = await client.post(
        path,
        data=body,
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sign_sha256(body)},
    )
    assert response.status == 200
    assert bot.dispatched[-1][0] == "github"
    assert bot.dispatched[-1][1] == "toomykins/vgstation13"


async def test_github_org_rewrites_meta(client_and_bot):
    client, bot = client_and_bot
    for full_name, expected in [
        ("space-wizards/space-station-14", "ss14"),
        ("Bluespess/Bluespess", "bluespess"),
    ]:
        body = json.dumps({"repository": {"full_name": full_name}}).encode()
        await client.post(
            "/mommi/github",
            data=body,
            headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sign_sha256(body)},
        )
        assert bot.dispatched[-1][1] == expected


async def test_github_bad_signature_is_forbidden(client_and_bot):
    client, bot = client_and_bot
    response = await client.post(
        "/mommi/github",
        data=b"{}",
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": "sha256=" + "0" * 64},
    )
    assert response.status == 403
    assert bot.dispatched == []


async def test_github_missing_event_header_is_bad_request(client_and_bot):
    client, _ = client_and_bot
    assert (await client.post("/mommi/github", data=b"{}")).status == 400


async def test_legacy_nudge_admin_flag_selects_adminhelp(client_and_bot):
    client, bot = client_and_bot
    response = await client.get("/discord", params={"pass": "p", "content": "help!", "admin": "1"})
    assert response.status == 202
    assert bot.dispatched[-1][0] == "gamenudge"
    assert bot.dispatched[-1][1] == "adminhelp"


async def test_legacy_nudge_defaults_to_server_status(client_and_bot):
    client, bot = client_and_bot
    await client.get("/discord", params={"pass": "p", "content": "round over"})
    assert bot.dispatched[-1][1] == "server_status"


async def test_nudge_with_explicit_meta(client_and_bot):
    client, bot = client_and_bot
    await client.get("/mommi/nudge", params={"meta": "ick", "pass": "p", "content": "x", "ping": "1"})
    msg_type, meta, content = bot.dispatched[-1]
    assert (msg_type, meta) == ("gamenudge", "ick")
    assert content == {"pass": "p", "content": "x", "ping": True}


async def test_post_nudge(client_and_bot):
    client, bot = client_and_bot
    response = await client.post(
        "/mommi/nudge/adminhelp", json={"pass": "p", "content": "ahelp", "ping": False}
    )
    assert response.status == 202
    assert bot.dispatched[-1][1] == "adminhelp"


async def test_nudge_without_content_is_rejected(client_and_bot):
    client, bot = client_and_bot
    assert (await client.get("/mommi/nudge", params={"meta": "ick", "pass": "p"})).status == 400
    assert bot.dispatched == []


async def test_ss14_relay(client_and_bot):
    client, bot = client_and_bot
    payload = {"password": "x", "type": "ooc", "contents": {"sender": "a", "contents": "b"}}
    response = await client.post("/mommi/ss14/main", json=payload)
    assert response.status == 202
    assert bot.dispatched[-1] == ("ss14", "main", payload)


async def test_ss14_rejects_non_json(client_and_bot):
    client, _ = client_and_bot
    assert (await client.post("/mommi/ss14/main", data=b"not json")).status == 400
