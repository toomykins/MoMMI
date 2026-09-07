from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import Any

import aiohttp
import discord

from mommi.bot import MoMMI
from mommi.context import ChannelContext, member_roles
from mommi.framework import MoMMICog, regex_command

LOGGER = logging.getLogger(__name__)

TIMEOUT = 10.0


class Restart(MoMMICog):
    def _allowed(self, ctx: ChannelContext, user: discord.abc.User) -> bool:
        if user.id == self.bot.config.main.bot.owner:
            return True
        allowed = ctx.server_config("modules.restart.roles", []) or []
        held = member_roles(user)
        if held is None:
            return False
        return any(role.id in allowed for role in held)

    @regex_command("restart", r"restart\s*(\S*)", help_topic="restart")
    async def restart(
        self, ctx: ChannelContext, match: re.Match[str], message: discord.Message
    ) -> None:
        config: dict[str, Any] = ctx.server_config("modules.restart.srv", {})
        if not config:
            await ctx.send("No restart configuration for this Discord server!")
            return

        if not self._allowed(ctx, message.author):
            await ctx.send("You are not allowed to do that")
            return

        name = match.group(1).strip()
        if not name:
            await ctx.send("Available servers are: " + " ".join(sorted(config)))
            return
        if name not in config:
            await ctx.send(f"Unknown key `{name}`")
            return

        server_config = config[name]
        try:
            base_url = server_config["url"].rstrip("/")
            key = server_config["key"]
            token = server_config["token"]
        except KeyError as e:
            await ctx.send(f"`{name}` is missing config key {e}.")
            return

        try:
            await message.add_reaction("⌛")
        except discord.HTTPException:
            pass

        auth = base64.b64encode(f"{key}:{token}".encode()).decode("ascii")
        url = f"{base_url}/instances/{key}/restart"
        try:
            timeout = aiohttp.ClientTimeout(total=TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers={"Authorization": f"Basic {auth}"}) as response:
                    if response.status != 200:
                        body = (await response.text())[:200]
                        LOGGER.error("Restart of %s returned %s: %s", name, response.status, body)
                        await ctx.send(f"Server said no ({response.status}).")
                        return
        except asyncio.TimeoutError:
            await ctx.send("Server timed out.")
            return
        except aiohttp.ClientError:
            LOGGER.exception("Restart request for %s failed.", name)
            await ctx.send("Couldn't reach the server.")
            return

        LOGGER.info("%s restarted server %r.", message.author, name)
        await ctx.send("Server restarted")

    def help_articles(self) -> dict[str, str]:
        return {
            "restart": (
                "`@MoMMI restart` lists the servers you can restart; "
                "`@MoMMI restart <name>` does it. You need a role listed under "
                "`modules.restart.roles` for this server."
            )
        }


async def setup(bot: MoMMI) -> None:
    await bot.add_cog(Restart(bot))
