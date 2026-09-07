from __future__ import annotations

import logging
import re

import aiohttp
import discord

from mommi.bot import EXTENSIONS, MoMMI
from mommi.config import RoleType
from mommi.context import ChannelContext
from mommi.framework import MoMMICog, regex_command

LOGGER = logging.getLogger(__name__)

OWNER = [RoleType.OWNER]


class Admin(MoMMICog):
    @regex_command("reload", r"reload(?:\s+(\S+))?", roles=OWNER)
    async def reload(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        target = match.group(1)
        targets = EXTENSIONS if target is None else [f"mommi.cogs.{target}"]

        failed = []
        for extension in targets:
            try:
                await self.bot.reload_extension(extension)
            except Exception:
                LOGGER.exception("Failed to reload %s.", extension)
                failed.append(extension)

        if failed:
            await message.add_reaction("🤒")
            await ctx.send("Failed to reload: " + ", ".join(f"`{f}`" for f in failed))
        else:
            await message.add_reaction("👌")

    @regex_command("modules", r"(?:modules|cogs)\b", roles=OWNER)
    async def modules(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        lines = []
        for name, cog in sorted(self.bot.cogs.items()):
            lines.append(f"{name}:")
            if isinstance(cog, MoMMICog):
                for command in cog.regex_commands:
                    lines.append(f"  * {command.name}")
                for msg_type in cog.comm_events:
                    lines.append(f"  ~ comm:{msg_type}")
        await ctx.send("```\n" + "\n".join(lines) + "\n```")

    @regex_command("shutdown", r"shutdown\b", roles=OWNER)
    async def shutdown(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        await ctx.send("Shutting down!")
        await self.bot.close()

    @regex_command("setname", r"name\s+(.+)", roles=OWNER)
    async def setname(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        assert self.bot.user is not None
        try:
            await self.bot.user.edit(username=match.group(1).strip())
            await message.add_reaction("👌")
        except discord.HTTPException as e:

            await ctx.send(f"Discord said no: {e.text}")

    @regex_command("setnick", r"nick\s+(.+)", roles=OWNER)
    async def setnick(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        guild = ctx.server.guild
        if guild is None or guild.me is None:
            await ctx.send("I can't see myself in this server.")
            return
        try:
            await guild.me.edit(nick=match.group(1).strip())
            await message.add_reaction("👌")
        except discord.Forbidden:
            await ctx.send("I don't have permission to change my own nickname here.")

    @regex_command("setavatar", r"avatar\b", roles=OWNER)
    async def setavatar(
        self, ctx: ChannelContext, match: re.Match[str], message: discord.Message
    ) -> None:
        if not message.attachments:
            await ctx.send("Attach an image, genius.")
            return
        assert self.bot.user is not None
        attachment = message.attachments[0]
        async with aiohttp.ClientSession() as session:
            async with session.get(attachment.url) as response:
                if response.status != 200:
                    await ctx.send("Couldn't download that attachment.")
                    return
                data = await response.read()
        try:
            await self.bot.user.edit(avatar=data)
            await message.add_reaction("👌")
        except discord.HTTPException as e:
            await ctx.send(f"Discord said no: {e.text}")

    @regex_command("sync", r"sync\b", roles=OWNER)
    async def sync(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        synced = await self.bot.tree.sync()
        await ctx.send(f"Synced {len(synced)} slash commands globally. Give Discord an hour.")

    @regex_command("testperm", r"testperm\s+(\S+)")
    async def testperm(
        self, ctx: ChannelContext, match: re.Match[str], message: discord.Message
    ) -> None:
        try:
            role = RoleType[match.group(1).upper()]
        except KeyError:
            await ctx.send(f"Unknown tier. Known: {', '.join(r.value for r in RoleType)}")
            return
        await ctx.send(str(ctx.is_role(message.author, role)))

    @regex_command("ids", r"ids\b", roles=OWNER)
    async def ids(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        guild = ctx.server.guild
        if guild is None:
            await ctx.send("No guild.")
            return
        lines = [f"{role.name}: `{role.id}`" for role in guild.roles if not role.is_default()]
        embed = discord.Embed(description="\n".join(lines) or "No roles.")
        await ctx.send(embed=embed)

    @regex_command("channelids", r"channelids\b", roles=OWNER)
    async def channelids(
        self, ctx: ChannelContext, match: re.Match[str], message: discord.Message
    ) -> None:
        guild = ctx.server.guild
        if guild is None:
            await ctx.send("No guild.")
            return
        lines = []
        for channel in guild.text_channels:
            alias = ctx.server.alias_for(channel.id)
            lines.append(f"#{channel.name}: `{channel.id}`" + (f" (`{alias}`)" if alias else ""))
        embed = discord.Embed(description="\n".join(lines)[:4000] or "No channels.")
        await ctx.send(embed=embed)


async def setup(bot: MoMMI) -> None:
    await bot.add_cog(Admin(bot))
