from __future__ import annotations

import re

import discord

from mommi.bot import MoMMI
from mommi.context import ChannelContext
from mommi.framework import MoMMICog, regex_command


class UserInfo(MoMMICog):
    @regex_command("userinfo", r"userinfo\s*<@!?(\d+)>")
    async def userinfo(
        self, ctx: ChannelContext, match: re.Match[str], message: discord.Message
    ) -> None:
        user_id = int(match.group(1))
        try:


            user = await self.bot.fetch_user(user_id)
        except discord.NotFound:
            await ctx.send("No such user.")
            return
        except discord.HTTPException:
            await ctx.send("Discord wouldn't tell me about that user.")
            return

        embed = discord.Embed(title=str(user))
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="ID", value=str(user.id), inline=False)
        embed.add_field(
            name="Account created", value=f"<t:{int(user.created_at.timestamp())}:F>", inline=False
        )

        member = ctx.server.guild.get_member(user_id) if ctx.server.guild else None
        if member is not None and member.joined_at is not None:
            embed.add_field(
                name="Joined server",
                value=f"<t:{int(member.joined_at.timestamp())}:F>",
                inline=False,
            )
            roles = [r.mention for r in member.roles if not r.is_default()]
            if roles:
                embed.add_field(name="Roles", value=" ".join(roles)[:1000], inline=False)

        await ctx.send(embed=embed)


async def setup(bot: MoMMI) -> None:
    await bot.add_cog(UserInfo(bot))
