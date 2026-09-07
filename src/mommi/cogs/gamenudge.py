from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import discord
from discord.ext import tasks

from mommi.bot import MoMMI
from mommi.context import ChannelContext
from mommi.framework import MoMMICog, comm_event
from mommi.timeparse import utcnow

LOGGER = logging.getLogger(__name__)

TICK_INTERVAL = 5.0

DEFAULT_KILL_MINUTES = 5


class GameNudge(MoMMICog):
    def __init__(self, bot: MoMMI) -> None:
        super().__init__(bot)
        self.tick.start()

    async def cog_unload(self) -> None:
        self.tick.cancel()

    @comm_event("gamenudge")
    async def on_nudge(self, ctx: ChannelContext, content: Any, meta: str) -> None:
        if not isinstance(content, dict):
            LOGGER.warning("gamenudge payload was not an object.")
            return

        try:
            password = content["pass"]
            text = str(content["content"])
            ping = bool(content.get("ping", False))
        except KeyError:
            LOGGER.warning("gamenudge payload missing pass/content.")
            return

        expected = ctx.module_config("nudge.password", None)
        if expected is None:
            LOGGER.error("No `[nudge] password` set in modules.toml; refusing all nudges.")
            return
        if password != expected:
            LOGGER.warning("Rejected a gamenudge with the wrong password.")
            return

        original = text


        text = text.replace("@", "@​")

        kill_phrase = ctx.server_config("modules.gamenudge.kill_phrase", None)
        if kill_phrase and original.strip() == kill_phrase:
            await self._close_channel(ctx)
            return

        if ping:
            role_id = ctx.server_config(f"modules.gamenudge.ping.{meta}", None)
            if role_id is None:
                LOGGER.warning("Got a ping nudge for %r but no role ID is configured.", meta)
            else:
                text += f" <@&{role_id}>"

        mirror = ctx.server_config(f"modules.gamenudge.{meta}.mirror", {})
        if isinstance(mirror, dict) and "channel" in mirror:
            target = ctx.server.resolve_channel_id(mirror["channel"])
            if target is None:
                LOGGER.error("Nudge mirror for %r names unknown channel %r.", meta, mirror["channel"])
            else:
                due = utcnow() + timedelta(minutes=float(mirror.get("delay", 0)))
                await self.bot.storage.add_nudge(due.timestamp(), original, ctx.server.id, target)

        await ctx.send(
            text,
            allowed_mentions=discord.AllowedMentions(roles=bool(ping), everyone=False, users=False),
        )

    async def _close_channel(self, ctx: ChannelContext) -> None:
        target_name = ctx.server_config("modules.gamenudge.kill_channel", None)
        if target_name is None:
            LOGGER.warning("Kill phrase fired but no kill_channel is configured.")
            return
        target = ctx.server.channel(target_name)
        if target is None:
            LOGGER.error("kill_channel %r does not resolve to a channel.", target_name)
            return

        minutes = int(ctx.server_config("modules.gamenudge.kill_minutes", DEFAULT_KILL_MINUTES))
        board = ctx.server_config(
            "modules.gamenudge.kill_link", "https://boards.4chan.org/vm/catalog#s=ss13g"
        )
        await target.send(
            f"**A round has ended.** You can discuss it at {board}. "
            f"This channel will be closed for {minutes} minutes."
        )
        if not await _set_locked(target, True):
            return


        asyncio.create_task(self._reopen_later(target, minutes))

    async def _reopen_later(self, target: ChannelContext, minutes: int) -> None:
        try:
            await asyncio.sleep(minutes * 60)
            if await _set_locked(target, False):
                await target.send("**This channel is open for discussion again.**")
        except asyncio.CancelledError:

            await _set_locked(target, False)
            raise
        except Exception:
            LOGGER.exception("Failed to reopen %s; unlocking manually may be needed.", target)

    @tasks.loop(seconds=TICK_INTERVAL)
    async def tick(self) -> None:
        try:
            due = await self.bot.storage.pop_due_nudges(utcnow().timestamp())
        except Exception:
            LOGGER.exception("Failed to poll the nudge mirror queue.")
            return

        for entry in due:
            server = self.bot.servers.get(entry.guild_id)
            if server is None:
                continue
            await ChannelContext(server, entry.channel_id).send(
                entry.content.replace("@", "@​")
            )

    @tick.before_loop
    async def _before_tick(self) -> None:
        await self.bot.wait_until_ready()


async def _set_locked(ctx: ChannelContext, locked: bool) -> bool:
    guild = ctx.server.guild
    channel = ctx.bot.get_channel(ctx.id)
    if guild is None or not isinstance(channel, discord.TextChannel):
        LOGGER.error("Cannot lock %s: not a text channel we can see.", ctx.id)
        return False

    overwrite = channel.overwrites_for(guild.default_role)
    overwrite.send_messages = False if locked else None  # type: ignore[misc]
    try:
        await channel.set_permissions(
            guild.default_role,
            overwrite=overwrite,
            reason="MoMMI round-end channel lock",
        )
        return True
    except discord.Forbidden:
        LOGGER.error("Missing Manage Permissions on #%s; cannot lock it.", channel.name)
    except discord.HTTPException:
        LOGGER.exception("Failed to change permissions on #%s.", channel.name)
    return False


async def setup(bot: MoMMI) -> None:
    await bot.add_cog(GameNudge(bot))
