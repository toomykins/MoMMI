from __future__ import annotations

import logging
import re

import discord
from discord import app_commands
from discord.ext import tasks

from mommi.bot import MoMMI
from mommi.config import RoleType
from mommi.context import ChannelContext
from mommi.framework import MoMMICog, regex_command
from mommi.timeparse import TimeParseError, parse_time, utcnow

LOGGER = logging.getLogger(__name__)

TICK_INTERVAL = 5.0
MAX_MESSAGE = 1500


class Reminders(MoMMICog):
    def __init__(self, bot: MoMMI) -> None:
        super().__init__(bot)
        self.tick.start()

    async def cog_unload(self) -> None:
        self.tick.cancel()

    @tasks.loop(seconds=TICK_INTERVAL)
    async def tick(self) -> None:
        try:
            due = await self.bot.storage.pop_due_reminders(utcnow().timestamp())
        except Exception:
            LOGGER.exception("Failed to poll the reminder queue.")
            return

        for reminder in due:
            server = self.bot.servers.get(reminder.guild_id)
            if server is None:
                LOGGER.warning("Dropping reminder %d for unconfigured guild.", reminder.uid)
                continue
            ctx = ChannelContext(server, reminder.channel_id)
            await ctx.send(
                f"*Buzz* <@{reminder.user_id}> {reminder.message}",
                allowed_mentions=discord.AllowedMentions(users=True),
            )

    @tick.before_loop
    async def _before_tick(self) -> None:
        await self.bot.wait_until_ready()

    async def _schedule(
        self, ctx: ChannelContext, spec: str, text: str, guild_id: int, channel_id: int, user_id: int
    ) -> str:
        try:
            when = parse_time(spec)
        except TimeParseError as e:
            return f"Invalid time format lad. ({e})"

        if when <= utcnow():
            return "*Buzz* no time travel, nerd."
        if len(text) > MAX_MESSAGE:
            return f"Keep the reminder under {MAX_MESSAGE} characters."

        uid = await self.bot.storage.add_reminder(
            when.timestamp(), text, guild_id, channel_id, user_id, utcnow().timestamp()
        )
        return f"#{uid} coming in at <t:{int(when.timestamp())}:F>"

    @regex_command("remind", r"remind(?:me|er)?\s+(\S+)\s+([\s\S]+)", help_topic="reminders")
    async def remind(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        reply = await self._schedule(
            ctx,
            match.group(1),
            match.group(2).strip(),
            ctx.server.id,
            ctx.id,
            message.author.id,
        )
        await ctx.send(reply)
        await message.add_reaction("✅" if reply.startswith("#") else "❌")

    @regex_command(
        "sneakremind",
        r"sneakremind\s+(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\s\S]+)",
        roles=[RoleType.OWNER],
    )
    async def sneakremind(
        self, ctx: ChannelContext, match: re.Match[str], message: discord.Message
    ) -> None:
        reply = await self._schedule(
            ctx,
            match.group(1),
            match.group(5).strip(),
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4)),
        )
        await ctx.send(reply)
        await message.add_reaction("✅" if reply.startswith("#") else "❌")

    @regex_command("remindlist", r"remindlist(?:\s+<@!?(\d+)>)?")
    async def remindlist(
        self, ctx: ChannelContext, match: re.Match[str], message: discord.Message
    ) -> None:
        target = message.author.id
        if match.group(1) is not None:
            if not ctx.is_role(message.author, RoleType.ADMIN):
                await ctx.send("No perms lad.")
                return
            target = int(match.group(1))

        reminders = await self.bot.storage.list_reminders(ctx.server.id, target)
        if not reminders:
            await ctx.send("No reminders pending for that user here.")
            return

        lines = ["All reminders for that user ID, contents hidden:"]
        for reminder in reminders:
            stamp = int(reminder.due)
            lines.append(f"`{reminder.uid}`: <t:{stamp}:F> in <#{reminder.channel_id}>")
        await ctx.send(embed=discord.Embed(description="\n".join(lines)[:4000]))

    @regex_command("unremind", r"unremind\s+(\d+)")
    async def unremind(
        self, ctx: ChannelContext, match: re.Match[str], message: discord.Message
    ) -> None:
        uid = int(match.group(1))
        reminder = await self.bot.storage.get_reminder(uid)
        if reminder is None:
            await ctx.send("No reminder by that UID. Too late, maybe?")
            return
        if reminder.guild_id != ctx.server.id:
            await ctx.send("That reminder UID doesn't belong to this server, hands off.")
            return
        if reminder.user_id != message.author.id and not ctx.is_role(message.author, RoleType.ADMIN):
            await ctx.send("You're not touching that without admin perms dude.")
            return

        await self.bot.storage.delete_reminder(uid)
        await ctx.send("And away it goes.")


    @app_commands.command(name="remind", description="Remind you about something later.")
    @app_commands.describe(
        when="When: 5m, 1d12h, 2026/01/01@09:00, or an ISO 8601 timestamp",
        what="What to remind you about",
    )
    async def slash_remind(self, interaction: discord.Interaction, when: str, what: str) -> None:
        server = self.bot.servers.get(interaction.guild_id or 0)
        if server is None:
            await interaction.response.send_message("Not configured for this server.", ephemeral=True)
            return
        ctx = ChannelContext(server, interaction.channel_id or 0)
        reply = await self._schedule(
            ctx, when, what, server.id, interaction.channel_id or 0, interaction.user.id
        )
        await interaction.response.send_message(reply, ephemeral=not reply.startswith("#"))

    @app_commands.command(name="reminders", description="List your pending reminders here.")
    async def slash_reminders(self, interaction: discord.Interaction) -> None:
        reminders = await self.bot.storage.list_reminders(
            interaction.guild_id or 0, interaction.user.id
        )
        if not reminders:
            await interaction.response.send_message("Nothing pending.", ephemeral=True)
            return
        lines = [
            f"`{r.uid}`: <t:{int(r.due)}:F> in <#{r.channel_id}>" for r in reminders
        ]
        await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)

    @app_commands.command(name="unremind", description="Cancel one of your pending reminders.")
    @app_commands.describe(uid="The reminder's number, from /reminders")
    async def slash_unremind(self, interaction: discord.Interaction, uid: int) -> None:
        reminder = await self.bot.storage.get_reminder(uid)
        if reminder is None or reminder.guild_id != (interaction.guild_id or 0):
            await interaction.response.send_message("No such reminder here.", ephemeral=True)
            return
        if reminder.user_id != interaction.user.id:
            await interaction.response.send_message("That one isn't yours.", ephemeral=True)
            return
        await self.bot.storage.delete_reminder(uid)
        await interaction.response.send_message("And away it goes.", ephemeral=True)

    def help_articles(self) -> dict[str, str]:
        return {
            "reminders": (
                "For when you're forgetful like me.\n\n"
                "`@MoMMI remind 5m fix that got damn broken Discord bot.`\n\n"
                "The time specifier can be one of three things:\n"
                "* **Relative**: smash a number and a unit together -- `w d h m s` for week, "
                "day, hour, minute, second. `1d12h` works.\n"
                "* **`YYYY/MM/DD@hh:mm:ss`**: pretty simple as long as you're not American. "
                "Either half can be left out.\n"
                "* **ISO 8601**: also the only way you're entering a non-UTC time zone.\n\n"
                "Everything else is UTC, because it's the only way I'm keeping my sanity: "
                "https://www.youtube.com/watch?v=-5wpm-gesOY\n\n"
                f"I confirm with a `#UID` (within {int(TICK_INTERVAL)} seconds of accuracy). "
                "Cancel with `@MoMMI unremind <UID>`, and list yours with `@MoMMI remindlist`.\n"
                "Admins can pass a mention to `remindlist` to see and cancel someone else's."
            )
        }


async def setup(bot: MoMMI) -> None:
    await bot.add_cog(Reminders(bot))
