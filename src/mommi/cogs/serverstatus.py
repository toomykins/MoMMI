from __future__ import annotations

import logging
import re
from typing import Any

import discord
from discord import app_commands

from mommi import gameserver
from mommi.bot import MoMMI
from mommi.context import ChannelContext
from mommi.framework import MoMMICog, regex_command
from mommi.gameserver import Snapshot, configured_servers, plural

LOGGER = logging.getLogger(__name__)


def format_status(snapshot: Snapshot, show_admins: bool) -> str:
    if not snapshot.online:
        return f"Couldn't get a status out of it: {snapshot.error}"

    out = f"{plural(snapshot.players, 'player')} online"
    if snapshot.server_name and snapshot.kind == "ss14":
        out = f"**{snapshot.server_name}**: {out}"
    if snapshot.map_name:
        out += f", map is {snapshot.map_name}"
    if snapshot.station_time:
        out += f", station time: {snapshot.station_time}"

    if show_admins and snapshot.admins is not None:
        out += f", **{snapshot.admins}** active admin{'' if snapshot.admins == 1 else 's'} online"
        if snapshot.afk_admins is not None:
            out += f", **{snapshot.afk_admins}** AFK admin{'' if snapshot.afk_admins == 1 else 's'} online"

    return out + "."


class ServerStatus(MoMMICog):
    async def _status(self, ctx: ChannelContext, requested: str) -> str:
        raw = ctx.server_config("modules.serverstatus", {})
        servers = configured_servers(raw)
        if not servers:
            return "No status configuration for this Discord server!"

        if requested == "list":
            return f"Available server keys are: {', '.join(sorted(servers))}"

        name = requested or str(raw.get("default", "") if isinstance(raw, dict) else "")
        if name == "default" and isinstance(raw, dict):
            name = str(raw.get("default", ""))
        if not name:
            return "No target server provided."
        if name not in servers:
            return f"Unknown key `{name}`. Known: {', '.join(sorted(servers))}"

        snapshot = await gameserver.query(name, servers[name])


        admindata = servers[name].get("admindata")
        show_admins = isinstance(admindata, list) and any(ctx.matches(i) for i in admindata)
        return format_status(snapshot, show_admins)

    @regex_command("status", r"stat(?:us|su)\s*(\S*)", help_topic="status")
    async def status(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        try:
            await message.add_reaction("⌛")
        except discord.HTTPException:
            pass
        await ctx.send(await self._status(ctx, match.group(1).strip()))

    @app_commands.command(name="status", description="How many players are on?")
    @app_commands.describe(server="Which server. Leave blank for the default.")
    async def slash_status(self, interaction: discord.Interaction, server: str = "") -> None:
        context = self.bot.servers.get(interaction.guild_id or 0)
        if context is None:
            await interaction.response.send_message("Not configured for this server.", ephemeral=True)
            return
        ctx = ChannelContext(context, interaction.channel_id or 0)
        await interaction.response.defer()
        await interaction.followup.send(await self._status(ctx, server.strip()))

    @slash_status.autocomplete("server")
    async def _server_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        context = self.bot.servers.get(interaction.guild_id or 0)
        if context is None:
            return []
        keys = configured_servers(context.get("modules.serverstatus", {}))
        matches = [k for k in sorted(keys) if current.lower() in k.lower()]
        return [app_commands.Choice(name=k, value=k) for k in matches[:25]]

    def help_articles(self) -> dict[str, Any]:
        async def status_help(ctx: ChannelContext) -> str:
            out = (
                "REEEEE IS THE SERVER DOWN?\n\n"
                "The answer is quite simple: ~~yes.~~ just run `@MoMMI status <server>`.\n\n"
                "See also `@MoMMI who` for who's actually on, and `@MoMMI graph` for "
                "player numbers over time.\n\n"
                "On *this Discord server*, you can check status for: "
            )
            keys = sorted(configured_servers(ctx.server_config("modules.serverstatus", {})))
            return out + (", ".join(keys) if keys else "nothing, it isn't configured here.")

        return {"status": status_help}


async def setup(bot: MoMMI) -> None:
    await bot.add_cog(ServerStatus(bot))
