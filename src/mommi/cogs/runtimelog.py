from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from datetime import date, timedelta
from pathlib import Path

import aiohttp
import discord

from mommi.bot import MoMMI
from mommi.context import ChannelContext
from mommi.framework import MoMMICog, regex_command

LOGGER = logging.getLogger(__name__)

LINES_RETURNED = 12
DOWNLOAD_TIMEOUT = 60.0
CONDENSER_TIMEOUT = 120.0

MAX_DOWNLOAD = 256 * 1024 * 1024


def human_size(num: float) -> str:
    for unit in ["", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"]:
        if abs(num) < 1024:
            return f"{num:.1f}{unit}B"
        num /= 1024
    return f"{num:.1f}YiB"


def parse_date_args(raw: str | None, today: date) -> date | None:
    if not raw or not raw.strip():
        return today
    args = raw.split()
    if args[0].startswith("yesterday"):
        return today - timedelta(days=1)
    if len(args) == 3:
        try:
            return date(int(args[0]), int(args[1]), int(args[2]))
        except ValueError:


            return None
    return None


class RuntimeLog(MoMMICog):
    @regex_command("runtimelog", r"runtimelog(?:\s+(.*))?", help_topic="runtimelog")
    async def runtimelog(
        self, ctx: ChannelContext, match: re.Match[str], message: discord.Message
    ) -> None:
        condenser = ctx.module_config("runtimelog.runtime-condenser", None)
        base_url = ctx.server_config("modules.runtimelog.base-url", None)
        if not condenser or not base_url:
            await ctx.send("Runtime logs aren't configured for this server.")
            return

        target = parse_date_args(match.group(1), date.today())
        if target is None:
            await ctx.send("Invalid date format. Format is `year month day`.")
            return

        try:
            await message.add_reaction("🕒")
        except discord.HTTPException:
            pass

        url = f"{base_url}{target.isoformat()}-runtime.log"
        try:
            output, size = await self._condense(url, str(condenser))
        except FileNotFoundError:
            LOGGER.error("Runtime condenser %r is not executable.", condenser)
            await ctx.send("My runtime condenser is missing. Poke an admin.")
            return
        except Exception:
            LOGGER.exception("runtimelog failed for %s.", url)
            await ctx.send("Something went wrong. You're not trying to do time travel, are you?")
            return

        lines = "\n".join(output.splitlines()[:LINES_RETURNED])
        await ctx.send(f"{url}\n```\n{lines}```\nTotal runtime log file size: {human_size(size)}")

    async def _condense(self, url: str, condenser: str) -> tuple[str, int]:
        with tempfile.TemporaryDirectory(prefix="mommi-runtime-") as tmpdir:
            path = Path(tmpdir) / "runtime.log"
            timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    written = 0
                    with path.open("wb") as handle:


                        async for chunk in response.content.iter_chunked(64 * 1024):
                            written += len(chunk)
                            if written > MAX_DOWNLOAD:
                                raise OSError("Runtime log exceeded the size limit.")
                            handle.write(chunk)

            process = await asyncio.create_subprocess_exec(
                condenser,
                "--input",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=CONDENSER_TIMEOUT
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise

            if process.returncode != 0:
                LOGGER.error("Condenser exited %s: %s", process.returncode, stderr[:500])
            return stdout.decode("utf-8", errors="replace"), path.stat().st_size

    def help_articles(self) -> dict[str, str]:
        return {
            "runtimelog": (
                "`@MoMMI runtimelog` shows today's condensed runtime log.\n"
                "`@MoMMI runtimelog yesterday` does the obvious thing.\n"
                "`@MoMMI runtimelog 2026 01 30` fetches a specific day."
            )
        }


async def setup(bot: MoMMI) -> None:
    await bot.add_cog(RuntimeLog(bot))
