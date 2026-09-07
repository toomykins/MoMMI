from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp
import discord
from discord.ext import tasks

from mommi.bot import MoMMI
from mommi.context import ChannelContext
from mommi.framework import MoMMICog, always_command
from mommi.timeparse import utcnow

LOGGER = logging.getLogger(__name__)

TICK_INTERVAL = 5.0
MUTE_EMOJI = "🔇"

MAX_CONTENT = 2000


class Mirror(MoMMICog):
    def __init__(self, bot: MoMMI) -> None:
        super().__init__(bot)
        self._session: aiohttp.ClientSession | None = None
        self.tick.start()

    async def cog_unload(self) -> None:
        self.tick.cancel()
        if self._session is not None:
            await self._session.close()

    async def session(self) -> aiohttp.ClientSession:


        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._session

    def _entry_for(self, ctx: ChannelContext) -> dict[str, Any] | None:
        entries = ctx.server_config("modules.mirror", [])
        if not isinstance(entries, list):
            return None
        for entry in entries:
            if not isinstance(entry, dict) or "from" not in entry or "to" not in entry:
                continue
            if ctx.matches(entry["from"]):
                return entry
        return None

    @always_command("mirror_capture", allow_self=True)
    async def capture(
        self, ctx: ChannelContext, match: Any, message: discord.Message
    ) -> None:
        entry = self._entry_for(ctx)
        if entry is None:
            return

        if message.webhook_id is not None:
            return

        content = message.content
        for attachment in message.attachments:
            content += " " + attachment.url
        content = content.strip()
        if not content:
            return

        avatar = message.author.display_avatar.url
        due = utcnow() + timedelta(minutes=float(entry.get("delay", 0)))
        await self.bot.storage.add_mirror(
            due.timestamp(),
            content[:MAX_CONTENT],
            message.author.display_name,
            str(avatar),
            str(entry["to"]),
            message.id,
        )

    async def on_mommi_delete(
        self, ctx: ChannelContext, payload: discord.RawMessageDeleteEvent
    ) -> None:
        if self._entry_for(ctx) is None:
            return
        if await self.bot.storage.cancel_mirror(payload.message_id):
            LOGGER.debug("Retracted pending mirror for deleted message %s.", payload.message_id)

    async def on_mommi_reaction(
        self, ctx: ChannelContext, payload: discord.RawReactionActionEvent
    ) -> None:
        if str(payload.emoji) != MUTE_EMOJI:
            return
        if self._entry_for(ctx) is None:
            return
        if not await self.bot.storage.cancel_mirror(payload.message_id):
            return

        channel = self.bot.get_channel(payload.channel_id)
        if isinstance(channel, discord.TextChannel):
            try:
                message = await channel.fetch_message(payload.message_id)
                await message.add_reaction("✅")
            except discord.HTTPException:
                LOGGER.debug("Retracted %s but couldn't confirm it.", payload.message_id)

    @tasks.loop(seconds=TICK_INTERVAL)
    async def tick(self) -> None:
        try:
            due = await self.bot.storage.pop_due_mirrors(utcnow().timestamp())
        except Exception:
            LOGGER.exception("Failed to poll the mirror queue.")
            return

        session = await self.session()
        for entry in due:
            try:
                async with session.post(
                    entry.target,
                    json={
                        "content": entry.content,
                        "username": entry.sender,
                        "avatar_url": entry.avatar,

                        "allowed_mentions": {"parse": []},
                    },
                ) as response:
                    if response.status >= 400:
                        body = (await response.text())[:200]
                        LOGGER.error(
                            "Mirror webhook returned %s: %s", response.status, body
                        )
            except aiohttp.ClientError:
                LOGGER.exception("Failed to deliver a mirrored message.")

    @tick.before_loop
    async def _before_tick(self) -> None:
        await self.bot.wait_until_ready()

    def help_articles(self) -> dict[str, str]:
        return {
            "mirror": (
                f"Some channels are mirrored elsewhere after a delay. Before the delay is up "
                f"you can pull a message back by deleting it, or by reacting with {MUTE_EMOJI}. "
                f"I'll add a ✅ to confirm."
            )
        }


async def setup(bot: MoMMI) -> None:
    await bot.add_cog(Mirror(bot))
