from __future__ import annotations

import logging
import random
import re
from pathlib import Path

import discord

from mommi.bot import MoMMI
from mommi.context import ChannelContext
from mommi.framework import MoMMICog, always_command, regex_command

LOGGER = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent.parent / "assets"


SUBVERT_CHANCE = 0.005

BASED_RE = re.compile(
    r"^\s*(based|gebaseerd|bas[ée]|basato|basado|basiert|bunaithe|ベース)[\s*?.!)]*$",
    re.IGNORECASE,
)
WYCI_RE = re.compile(r"\S\s+(?:when|whence)[\s*?.!)]*$", re.IGNORECASE)
TETRIS_RE = re.compile(r"tetris", re.IGNORECASE)


BASED_REPLIES = {
    "based": ("Based on what?", "Not Based."),
    "gebaseerd": ("Gebaseerd op wat?", "Niet Gebaseerd."),
    "basiert": ("Worüber?", "Nich basiert."),
    "basé": ("Sur quoi?", "Pas basé."),
    "base": ("Sur quoi?", "Pas basé."),
    "basado": ("¿Basado en qué?", "No basado."),
    "basato": ("Basato su cosa?", "Non basato."),
    "bunaithe": ("Cad é ina bunaithe?", "Ní bunaithe."),
    "ベース": ("何に基づいてですか", "ベースではない"),
}


def _enabled(ctx: ChannelContext, module: str, default: bool = True) -> bool:
    for key in (f"modules.{module}.enabled", f"{module}.enabled"):
        value = ctx.server_config(key, None)
        if value is not None:
            return bool(value)
    return default


def based_reply(word: str) -> str:
    affirmative, negative = BASED_REPLIES.get(word.lower(), BASED_REPLIES["based"])
    return negative if random.random() <= SUBVERT_CHANCE else affirmative


class Fun(MoMMICog):


    @always_command("based")
    async def based(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        if not _enabled(ctx, "based"):
            return
        found = BASED_RE.search(message.content)
        if found is None:
            return
        await ctx.send(based_reply(found.group(1)))

    @always_command("wyci")
    async def wyci(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        if not _enabled(ctx, "wyci"):
            return
        if WYCI_RE.search(message.content) is None:
            return
        await ctx.send("Never." if random.random() <= SUBVERT_CHANCE else "When You Code It.")

    @always_command("tetris")
    async def tetris(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        if not _enabled(ctx, "nanotrasenblockgame", default=False):
            return
        if TETRIS_RE.search(message.content) is None:
            return
        await ctx.send("*Nanotrasen Block Game:tm:")


    @regex_command("gettingstarted", r"gettingstarted\b")
    async def gettingstarted(
        self, ctx: ChannelContext, match: re.Match[str], message: discord.Message
    ) -> None:
        await ctx.send(ctx.module_config("links.gettingstarted", "https://hackmd.io/@ss14/getting-set-up"))

    @regex_command("howdoicode", r"howdoicode\b")
    async def howdoicode(
        self, ctx: ChannelContext, match: re.Match[str], message: discord.Message
    ) -> None:
        await ctx.send(ctx.module_config("links.howdoicode", "i dunno lol"))

    @regex_command("testmerge", r"testmerge\b")
    async def testmerge(
        self, ctx: ChannelContext, match: re.Match[str], message: discord.Message
    ) -> None:
        await ctx.send("Sorry dude, we can't do that (yet?).")

    @regex_command("dance", r"(?:dance|wiggle)\b")
    async def dance(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        await self._send_asset(ctx, "wiggle.gif")

    @regex_command("away", r"away\b")
    async def away(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        await self._send_asset(ctx, "away.gif")

    async def _send_asset(self, ctx: ChannelContext, filename: str) -> None:
        base = Path(ctx.main_config("bot.files-dir", str(ASSETS)))
        path = base / filename
        if not path.is_file():
            LOGGER.warning("Asset %s is missing (looked in %s).", filename, base)
            await ctx.send("*buzz* I seem to have misplaced that.")
            return
        await ctx.send(file=discord.File(path))


async def setup(bot: MoMMI) -> None:
    await bot.add_cog(Fun(bot))
