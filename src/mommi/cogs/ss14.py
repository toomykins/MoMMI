from __future__ import annotations

import logging
from typing import Any

import aiohttp
import discord

from mommi.bot import MoMMI
from mommi.context import ChannelContext
from mommi.framework import MoMMICog, always_command, comm_event

LOGGER = logging.getLogger(__name__)


RELAY_MARKER = "​"
TIMEOUT = 10.0


class SS14(MoMMICog):
    def __init__(self, bot: MoMMI) -> None:
        super().__init__(bot)
        self._session: aiohttp.ClientSession | None = None

    async def cog_unload(self) -> None:
        if self._session is not None:
            await self._session.close()

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT))
        return self._session

    @comm_event("ss14")
    async def on_ss14(self, ctx: ChannelContext, content: Any, meta: str) -> None:
        if not isinstance(content, dict):
            return
        config = ctx.module_config(f"ss14.servers.{meta}", None)
        if not isinstance(config, dict):
            LOGGER.warning("No `[ss14.servers.%s]` config in modules.toml.", meta)
            return
        if config.get("password") != content.get("password"):
            LOGGER.warning("Rejected an ss14 message for %r with the wrong password.", meta)
            return

        if content.get("type") != "ooc":
            return
        body = content.get("contents")
        if not isinstance(body, dict):
            return

        sender = str(body.get("sender", "?"))
        text = str(body.get("contents", ""))
        await ctx.send(
            f"{RELAY_MARKER}**OOC**: `{sender}`: {text}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @always_command("ss14_relay")
    async def relay(self, ctx: ChannelContext, match: Any, message: discord.Message) -> None:
        if ctx.alias is None:
            return
        content = message.content.strip()

        if not content or content.startswith(RELAY_MARKER) or message.webhook_id is not None:
            return
        if self.bot.user is not None and message.author.id == self.bot.user.id:
            return

        server_key = None
        for entry in ctx.server_config("modules.ss14", []) or []:
            if isinstance(entry, dict) and entry.get("discord_channel") == ctx.alias:
                server_key = entry.get("server")
                break
        if server_key is None:
            return

        config = ctx.module_config(f"ss14.servers.{server_key}", None)
        if not isinstance(config, dict) or "api_url" not in config:
            LOGGER.warning("ss14 server %r is not configured in modules.toml.", server_key)
            return

        url = str(config["api_url"]).rstrip("/") + "/ooc"
        payload = {
            "password": config.get("password"),
            "sender": message.author.display_name,
            "contents": content,
        }
        try:
            session = await self.session()
            async with session.post(url, json=payload) as response:
                if response.status >= 400:
                    body = (await response.text())[:200]

                    LOGGER.error("SS14 relay to %s returned %s: %s", server_key, response.status, body)
        except aiohttp.ClientError:
            LOGGER.exception("Failed to relay a message to SS14 server %r.", server_key)


async def setup(bot: MoMMI) -> None:
    await bot.add_cog(SS14(bot))
