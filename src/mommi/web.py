from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

from mommi.changelog import ChangelogManager

if TYPE_CHECKING:
    from mommi.bot import MoMMI

LOGGER = logging.getLogger(__name__)


MAX_BODY = 8 * 1024 * 1024

WEB_APP_KEY: web.AppKey[WebApp] = web.AppKey("mommi_web")


def verify_github_signature(secret: str, body: bytes, headers: Any) -> bool:
    sha256 = headers.get("X-Hub-Signature-256")
    if sha256:
        prefix, _, digest = sha256.partition("=")
        if prefix != "sha256":
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, digest)

    sha1 = headers.get("X-Hub-Signature")
    if sha1:
        prefix, _, digest = sha1.partition("=")
        if prefix != "sha1":
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
        return hmac.compare_digest(expected, digest)

    return False


class WebApp:
    def __init__(self, bot: MoMMI) -> None:
        self.bot = bot
        self.config = bot.config.main.web
        self.changelog = ChangelogManager(self.config.changelog, self._relay_changelog)

    async def _relay_changelog(self, document: dict[str, Any]) -> None:
        await self.bot.dispatch_comm("changelog", "", document)


    async def twohundred(self, request: web.Request) -> web.Response:
        return web.Response(text="hi BYOND!")

    async def nudge_legacy(self, request: web.Request) -> web.Response:
        query = request.query
        meta = "adminhelp" if query.get("admin") in ("1", "true", "True") else "server_status"
        return await self._nudge(
            meta,
            query.get("pass", ""),
            query.get("content", ""),
            query.get("ping") in ("1", "true", "True"),
        )

    async def nudge_query(self, request: web.Request) -> web.Response:
        query = request.query
        meta = query.get("meta")
        if not meta:

            return await self.nudge_legacy(request)
        return await self._nudge(
            meta,
            query.get("pass", ""),
            query.get("content", ""),
            query.get("ping") in ("1", "true", "True"),
        )

    async def nudge_post(self, request: web.Request) -> web.Response:
        meta = request.match_info["meta"]
        try:
            body = await self._json(request)
        except ValueError as e:
            return web.Response(status=400, text=str(e))
        return await self._nudge(
            meta,
            str(body.get("pass", "")),
            str(body.get("content", "")),
            bool(body.get("ping", False)),
        )

    async def _nudge(self, meta: str, password: str, content: str, ping: bool) -> web.Response:
        if not content:
            return web.Response(status=400, text="No content.")
        await self.bot.dispatch_comm(
            "gamenudge", meta, {"pass": password, "content": content, "ping": ping}
        )
        return web.Response(status=202, text="MoMMI successfully received the message.")

    async def ss14(self, request: web.Request) -> web.Response:
        try:
            body = await self._json(request)
        except ValueError as e:
            return web.Response(status=400, text=str(e))
        await self.bot.dispatch_comm("ss14", request.match_info["id"], body)
        return web.Response(status=202, text="MoMMI successfully received the message.")

    async def github(self, request: web.Request) -> web.Response:
        event = request.headers.get("X-GitHub-Event")
        if not event:
            return web.Response(status=400, text="Missing X-GitHub-Event.")

        if request.content_length and request.content_length > MAX_BODY:
            return web.Response(status=413, text="Body too large.")
        body = await request.read()

        if self.config.verify_github:
            if not self.config.github_key:
                LOGGER.error("verify-github is on but no github-key is set; rejecting webhook.")
                return web.Response(status=500, text="Server misconfigured.")
            if not verify_github_signature(self.config.github_key, body, request.headers):
                LOGGER.warning("Rejected a GitHub webhook with a bad signature.")
                return web.Response(status=403, text="Bad signature.")

        if event == "ping":
            return web.Response(text="pong")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return web.Response(status=400, text="Body is not JSON.")
        if not isinstance(payload, dict):
            return web.Response(status=400, text="Body is not an object.")


        if event == "pull_request":
            await self.changelog.handle_pull_request(payload)
        elif event == "push":
            await self.changelog.handle_push(payload)

        meta = payload.get("repository", {}).get("full_name", "")

        if meta.startswith("space-wizards/"):
            meta = "ss14"
        elif meta.startswith("Bluespess/"):
            meta = "bluespess"

        await self.bot.dispatch_comm("github", meta, {"event": event, "content": payload})
        return web.Response(text="Worked!")

    async def _json(self, request: web.Request) -> dict[str, Any]:
        if request.content_length and request.content_length > MAX_BODY:
            raise ValueError("Body too large.")
        raw = await request.read()
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError("Body is not JSON.") from e
        if not isinstance(body, dict):
            raise ValueError("Body is not an object.")
        return body

    def build(self) -> web.Application:
        app = web.Application(client_max_size=MAX_BODY)
        app.add_routes(
            [
                web.get("/twohundred", self.twohundred),
                web.get("/discord", self.nudge_query),
                web.get("/mommi/nudge", self.nudge_query),
                web.post("/mommi/nudge/{meta}", self.nudge_post),
                web.post("/mommi/ss14/{id}", self.ss14),
                web.post("/changelog", self.github),
                web.post("/mommi/github", self.github),
                web.post("/mommi/github/{id}", self.github),
                web.post("/dev/git_hooks/webmommi", self.github),
            ]
        )
        app[WEB_APP_KEY] = self

        async def close_changelog(_: web.Application) -> None:

            await self.changelog.close()

        app.on_cleanup.append(close_changelog)
        return app


async def start_web(bot: MoMMI) -> web.AppRunner:
    config = bot.config.main.web
    application = WebApp(bot)
    runner = web.AppRunner(application.build(), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, config.address, config.port)
    await site.start()
    LOGGER.info("Web server listening on %s:%s", config.address, config.port)

    if not config.verify_github and config.github_key:
        LOGGER.warning("verify-github is off; webhook signatures are NOT being checked.")
    return runner
