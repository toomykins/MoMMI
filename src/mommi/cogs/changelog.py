from __future__ import annotations

from typing import Any

from mommi.bot import MoMMI
from mommi.context import ChannelContext
from mommi.framework import MoMMICog, comm_event

CHANGELOG_EMOJIS = {
    "bugfix": "🐞",
    "wip": "🔜",
    "tweak": "🛠",
    "soundadd": "🎧",
    "sounddel": "🔇",
    "rscdel": "❌",
    "rscadd": "🆕",
    "imageadd": "🎨",
    "imagedel": "⬜",
    "spellcheck": "🔡",
    "experiment": "💯",
    "tgs": "💩",
}

MAX_MESSAGE = 1900


class Changelog(MoMMICog):
    def __init__(self, bot: MoMMI) -> None:
        super().__init__(bot)


        self._last_author: dict[int, str] = {}

    @comm_event("changelog")
    async def on_changelog(self, ctx: ChannelContext, content: Any, meta: str) -> None:
        if not isinstance(content, dict):
            return
        author = str(content.get("author", "someone"))
        changes = content.get("changes") or []
        if not changes:
            return

        lines = []
        if self._last_author.get(ctx.id) != author:
            self._last_author[ctx.id] = author
            lines.append(f"**__{author}__** Updated:")

        for change in changes:

            if not isinstance(change, dict):
                continue
            for kind, description in change.items():
                emoji = CHANGELOG_EMOJIS.get(str(kind).lower(), "🆕")
                lines.append(f"{emoji} {description}")

        for chunk in _chunks(lines):
            await ctx.send(chunk)


def _chunks(lines: list[str]) -> list[str]:
    out: list[str] = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > MAX_MESSAGE:
            if current:
                out.append(current)
            current = line[:MAX_MESSAGE]
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        out.append(current)
    return out


async def setup(bot: MoMMI) -> None:
    await bot.add_cog(Changelog(bot))
