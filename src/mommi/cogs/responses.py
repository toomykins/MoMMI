from __future__ import annotations

import re

import discord

from mommi.bot import MoMMI
from mommi.context import ChannelContext, member_roles
from mommi.framework import MoMMICog, always_command, regex_command

STORAGE_KEY = "resp"

MAX_NAME = 64
MAX_VALUE = 1800


class Responses(MoMMICog):
    async def _can_modify(self, ctx: ChannelContext, user: discord.abc.User) -> bool:
        role_id = ctx.server_config("modules.responses.role", None)
        if role_id is None:
            await ctx.send("No response-editor role is configured for this server.")
            return False
        if user.id == self.bot.config.main.bot.owner:
            return True
        held = member_roles(user)
        if held is not None and any(r.id == role_id for r in held):
            return True
        await ctx.send("You are not allowed to do that")
        return False

    @always_command("resp_read")
    async def read(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        if not message.content.startswith("$"):
            return
        name = message.content[1:].strip()
        if not name:
            return
        data: dict[str, str] = await ctx.get_storage(STORAGE_KEY, {})
        response = data.get(name)
        if response:
            await ctx.send(response)

    @regex_command("resp_add", r"resp\s+add\s+(\S+)\s+([\s\S]+)")
    async def add(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        if not await self._can_modify(ctx, message.author):
            return
        name, value = match.group(1), match.group(2).strip()
        if len(name) > MAX_NAME:
            await ctx.send(f"Keep the name under {MAX_NAME} characters.")
            return
        if len(value) > MAX_VALUE:
            await ctx.send(f"Keep the response under {MAX_VALUE} characters.")
            return

        data: dict[str, str] = await ctx.get_storage(STORAGE_KEY, {})
        data[name] = value
        await ctx.set_storage(STORAGE_KEY, data)
        await message.add_reaction("✅")

    @regex_command("resp_remove", r"resp\s+remove\s+(\S+)")
    async def remove(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        if not await self._can_modify(ctx, message.author):
            return
        data: dict[str, str] = await ctx.get_storage(STORAGE_KEY, {})
        if match.group(1) not in data:
            await message.add_reaction("❓")
            return
        del data[match.group(1)]
        await ctx.set_storage(STORAGE_KEY, data)
        await message.add_reaction("✅")

    @regex_command("resp_list", r"resp\s+list\b")
    async def list(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        data: dict[str, str] = await ctx.get_storage(STORAGE_KEY, {})
        if not data:
            await ctx.send("There are no responses set up here yet.")
            return
        await ctx.send(f"Your options are: {', '.join(sorted(data))}\nChoose wisely.")

    def help_articles(self) -> dict[str, str]:
        return {
            "resp": (
                "Canned responses. Say `$name` in a channel and I'll say the thing back.\n\n"
                "Editors can manage them with:\n"
                "`@MoMMI resp add <name> <text>`\n"
                "`@MoMMI resp remove <name>`\n"
                "`@MoMMI resp list`"
            )
        }


async def setup(bot: MoMMI) -> None:
    await bot.add_cog(Responses(bot))
