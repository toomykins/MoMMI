from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any

import discord
from discord import app_commands
from discord.ext import tasks

from mommi.bot import MoMMI
from mommi.context import ChannelContext
from mommi.framework import MoMMICog, always_command, regex_command

LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "markov"
SENTENCE_RE = re.compile(r"([.,?\n]|(?<!@)!)")
PAREN_RE = re.compile(r"[\[\]{}()\"']")
MENTION_RE = re.compile(r"<@!?(\d+)>")
ROLE_RE = re.compile(r"<@&(\d+)>")
CHANNEL_RE = re.compile(r"<#(\d+)>")


MIN_SENTENCE_WORDS = 7

MAX_CHAIN_LENGTH = 100

GENERATION_ATTEMPTS = 5
MIN_ACCEPTABLE_LENGTH = 5
FLUSH_INTERVAL = 60.0


Chain = dict[str, dict[str, int]]


def sentences(text: str) -> list[str]:
    out = []
    last = 0
    for match in SENTENCE_RE.finditer(text):
        fragment = text[last : match.start()].strip()
        if fragment:
            out.append(fragment)
        last = match.end()
    tail = text[last:].strip()
    if tail:
        out.append(tail)
    return out


def learn(chain: Chain, text: str) -> None:
    for sentence in sentences(PAREN_RE.sub("", text.lower())):
        words = sentence.split()
        if len(words) < MIN_SENTENCE_WORDS:
            continue
        previous = ""
        for word in words:
            chain.setdefault(previous, {})
            chain[previous][word] = chain[previous].get(word, 0) + 1
            previous = word
        chain.setdefault(previous, {})
        chain[previous][""] = chain[previous].get("", 0) + 1


def generate(chain: Chain, seed: str) -> list[str] | None:
    if seed not in chain:
        return None

    best: list[str] = []
    for _ in range(GENERATION_ATTEMPTS):
        words = [seed]
        current = seed
        for _ in range(MAX_CHAIN_LENGTH):
            options = chain.get(current)
            if not options:
                break
            current = random.choices(list(options), weights=list(options.values()), k=1)[0]
            if current == "":
                break
            words.append(current)

        if len(words) > len(best):
            best = words
        if len(best) > MIN_ACCEPTABLE_LENGTH:
            break
    return best


class Markov(MoMMICog):
    def __init__(self, bot: MoMMI) -> None:
        super().__init__(bot)
        self._chains: dict[int, Chain] = {}
        self._dirty: set[int] = set()
        self._lock = asyncio.Lock()
        self.flush_loop.start()

    async def cog_unload(self) -> None:
        self.flush_loop.cancel()
        await self._flush()

    async def _chain_for(self, guild_id: int) -> Chain:
        if guild_id not in self._chains:
            stored = await self.bot.storage.get(guild_id, STORAGE_KEY, {})
            self._chains[guild_id] = stored if isinstance(stored, dict) else {}
        return self._chains[guild_id]

    async def _flush(self) -> None:
        async with self._lock:
            dirty, self._dirty = self._dirty, set()
            for guild_id in dirty:
                chain = self._chains.get(guild_id)
                if chain is not None:
                    await self.bot.storage.set(guild_id, STORAGE_KEY, chain)
        if dirty:
            LOGGER.debug("Flushed markov chains for %d guild(s).", len(dirty))

    @tasks.loop(seconds=FLUSH_INTERVAL)
    async def flush_loop(self) -> None:
        try:
            await self._flush()
        except Exception:
            LOGGER.exception("Failed to flush markov chains.")

    @flush_loop.before_loop
    async def _before_flush(self) -> None:
        await self.bot.wait_until_ready()

    @always_command("markov_read")
    async def read(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        if not ctx.server_config("modules.markov.enabled", True):
            return

        if self.bot.user is not None and self.bot.user.mentioned_in(message):
            return
        chain = await self._chain_for(ctx.server.id)
        learn(chain, message.content)
        self._dirty.add(ctx.server.id)

    @regex_command("markov", r"markov(?:\s*\(?\s*(\S*?)\s*\)?)?$", help_topic="markov")
    async def markov(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        chain = await self._chain_for(ctx.server.id)
        if not chain:
            await ctx.send("I haven't learned anything here yet. Talk more.")
            return

        seed = (match.group(1) or "").lower().rstrip(")")
        words = generate(chain, seed)
        if words is None:
            await ctx.send("Cannot make markov chain: unknown word.")
            return

        await ctx.send(self._sanitise(ctx, " ".join(words) + "."))

    @app_commands.command(name="markov", description="Generate a sentence from what people have said.")
    @app_commands.describe(seed="Word to start from. Leave blank for a random start.")
    async def slash_markov(self, interaction: discord.Interaction, seed: str = "") -> None:
        server = self.bot.servers.get(interaction.guild_id or 0)
        if server is None:
            await interaction.response.send_message("Not configured for this server.", ephemeral=True)
            return
        ctx = ChannelContext(server, interaction.channel_id or 0)

        chain = await self._chain_for(server.id)
        if not chain:


            await interaction.response.send_message(
                "I haven't learned anything here yet. Talk more.", ephemeral=True
            )
            return

        words = generate(chain, seed.lower())
        if words is None:
            await interaction.response.send_message(
                "Cannot make markov chain: unknown word.", ephemeral=True
            )
            return
        await interaction.response.send_message(self._sanitise(ctx, " ".join(words) + "."))

    def _sanitise(self, ctx: ChannelContext, text: str) -> str:
        guild = ctx.server.guild

        def role(match: re.Match[str]) -> str:
            found = guild.get_role(int(match.group(1))) if guild else None
            return f"@{found.name}" if found else "@role"

        def member(match: re.Match[str]) -> str:
            found = guild.get_member(int(match.group(1))) if guild else None
            return f"@{found.display_name}" if found else "@someone"

        def channel(match: re.Match[str]) -> str:
            found = guild.get_channel(int(match.group(1))) if guild else None
            return f"#{found.name}" if found else "#channel"

        text = ROLE_RE.sub(role, text)
        text = MENTION_RE.sub(member, text)
        return CHANNEL_RE.sub(channel, text)

    def help_articles(self) -> dict[str, Any]:
        return {
            "markov": (
                "I read what everyone says and build a word-association chain out of it.\n\n"
                "`@MoMMI markov` starts from nothing, `@MoMMI markov(word)` starts from a word.\n"
                "Results are not my opinion, they are yours, statistically."
            )
        }


async def setup(bot: MoMMI) -> None:
    await bot.add_cog(Markov(bot))
