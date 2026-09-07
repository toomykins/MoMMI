from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands

from mommi.bot import MoMMI
from mommi.context import ChannelContext
from mommi.framework import MoMMICog, regex_command

Article = str | Callable[[ChannelContext], Awaitable[str]]

INTRO = (
    "Yes hello I'm your ~~un~~friendly neighbourhood MoMMI.\n"
    "Send `@MoMMI help <topic>` for more info.\n"
    "Available topics are: "
)


MESSAGE_LIMIT = 2000


class Help(MoMMICog):
    def articles(self) -> dict[str, Article]:
        out: dict[str, Article] = {}
        for cog in self.bot.cogs.values():
            if isinstance(cog, MoMMICog):
                out.update(cog.help_articles())
        return out

    async def render(self, topic: str, ctx: ChannelContext) -> str | None:
        article = self.articles().get(topic)
        if article is None:
            return None
        if callable(article):
            result = article(ctx)
            return await result if inspect.isawaitable(result) else str(result)
        return article

    @regex_command("help", r"help(?:\s+(\S+))?", help_topic="help")
    async def help(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        topic = match.group(1)
        if topic is None:
            await ctx.send(INTRO + ", ".join(sorted(self.articles())))
            return

        body = await self.render(topic, ctx)
        if body is None:
            await ctx.send(f"Invalid topic. Try one of: {', '.join(sorted(self.articles()))}")
            return

        for chunk in _chunk(body):
            await ctx.send(chunk)

    @app_commands.command(name="help", description="Explain what MoMMI can do.")
    @app_commands.describe(topic="A specific topic to read about")
    async def slash_help(self, interaction: discord.Interaction, topic: str | None = None) -> None:
        server = self.bot.servers.get(interaction.guild_id or 0)
        if server is None:
            await interaction.response.send_message("Not configured for this server.", ephemeral=True)
            return
        ctx = ChannelContext(server, interaction.channel_id or 0)

        if topic is None:
            await interaction.response.send_message(
                INTRO + ", ".join(sorted(self.articles())), ephemeral=True
            )
            return

        body = await self.render(topic, ctx)
        if body is None:
            await interaction.response.send_message(
                f"No such topic. Try one of: {', '.join(sorted(self.articles()))}", ephemeral=True
            )
            return
        await interaction.response.send_message(next(iter(_chunk(body))), ephemeral=True)

    @slash_help.autocomplete("topic")
    async def _topic_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        matches = [t for t in sorted(self.articles()) if current.lower() in t.lower()]
        return [app_commands.Choice(name=t, value=t) for t in matches[:25]]

    def help_articles(self) -> dict[str, Article]:
        return {"help": "How hopeless are you, exactly?"}


def _chunk(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit:
            if current:
                chunks.append(current)

            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks


async def setup(bot: MoMMI) -> None:
    await bot.add_cog(Help(bot))
