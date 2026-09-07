from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import tasks

from mommi import gameserver
from mommi.bot import MoMMI
from mommi.charts import ChartUnavailable, render_hourly_chart, render_player_chart
from mommi.context import ChannelContext
from mommi.framework import MoMMICog, regex_command
from mommi.gameserver import configured_servers, plural
from mommi.timeparse import utcnow

LOGGER = logging.getLogger(__name__)


DEFAULT_POLL_MINUTES = 5
DEFAULT_RETENTION_DAYS = 90
PRUNE_INTERVAL_HOURS = 24

PERIOD_RE = re.compile(r"^(\d+)\s*([hdw])$", re.IGNORECASE)
DEFAULT_PERIOD = timedelta(days=1)
MAX_PERIOD = timedelta(days=365)


WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

MIN_SAMPLES_PER_BUCKET = 3
DEFAULT_HIGHPOP_PERIOD = timedelta(days=30)


MAX_NAMES_SHOWN = 120

COLUMN_THRESHOLD = 8
NAME_COLUMNS = 3

MAX_FIELD = 1024


@dataclass(frozen=True)
class PopStats:

    samples: int
    mean: float
    peak: int
    peak_at: float

    by_hour: dict[int, float]

    by_weekday: dict[int, float]

    @property
    def busiest_hour(self) -> int | None:
        return max(self.by_hour, key=lambda h: self.by_hour[h]) if self.by_hour else None

    @property
    def busiest_weekday(self) -> int | None:
        return max(self.by_weekday, key=lambda d: self.by_weekday[d]) if self.by_weekday else None

    @property
    def quietest_hour(self) -> int | None:
        return min(self.by_hour, key=lambda h: self.by_hour[h]) if self.by_hour else None


def summarise(samples: list[tuple[float, int]]) -> PopStats | None:
    if not samples:
        return None

    hours: dict[int, list[int]] = {}
    weekdays: dict[int, list[int]] = {}
    peak_value, peak_at = -1, samples[0][0]
    total = 0

    for ts, count in samples:
        moment = datetime.fromtimestamp(ts, timezone.utc)
        hours.setdefault(moment.hour, []).append(count)
        weekdays.setdefault(moment.weekday(), []).append(count)
        total += count
        if count > peak_value:
            peak_value, peak_at = count, ts

    def means(buckets: dict[int, list[int]]) -> dict[int, float]:
        return {
            key: sum(values) / len(values)
            for key, values in buckets.items()
            if len(values) >= MIN_SAMPLES_PER_BUCKET
        }

    return PopStats(
        samples=len(samples),
        mean=total / len(samples),
        peak=peak_value,
        peak_at=peak_at,
        by_hour=means(hours),
        by_weekday=means(weekdays),
    )


def name_columns(names: list[str], columns: int = NAME_COLUMNS) -> list[str]:
    if not names:
        return []
    columns = max(1, min(columns, len(names)))
    height = -(-len(names) // columns)
    blocks = []
    for start in range(0, len(names), height):
        block = "\n".join(names[start : start + height])
        blocks.append(block[:MAX_FIELD])
    return blocks


def parse_period(raw: str | None) -> timedelta | None:
    if not raw or not raw.strip():
        return DEFAULT_PERIOD
    match = PERIOD_RE.match(raw.strip())
    if match is None:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    period = {"h": timedelta(hours=amount), "d": timedelta(days=amount), "w": timedelta(weeks=amount)}[unit]
    if period <= timedelta(0) or period > MAX_PERIOD:
        return None
    return period


class PlayerStats(MoMMICog):
    def __init__(self, bot: MoMMI) -> None:
        super().__init__(bot)
        interval = float(bot.config.module("playerstats.poll-minutes", DEFAULT_POLL_MINUTES))
        self.poll.change_interval(minutes=max(1.0, interval))
        self.poll.start()
        self.prune.start()

    async def cog_unload(self) -> None:
        self.poll.cancel()
        self.prune.cancel()


    @tasks.loop(minutes=DEFAULT_POLL_MINUTES)
    async def poll(self) -> None:
        for server in self.bot.servers.values():
            servers = configured_servers(server.get("modules.serverstatus", {}))
            if not servers:
                continue

            results = await asyncio.gather(
                *(gameserver.query(key, config) for key, config in servers.items()),
                return_exceptions=True,
            )
            now = utcnow().timestamp()
            for snapshot in results:
                if isinstance(snapshot, BaseException):
                    LOGGER.debug("Poll raised: %s", snapshot)
                    continue


                if not snapshot.online:
                    continue
                try:
                    await self.bot.storage.record_players(
                        server.id, snapshot.key, now, snapshot.players
                    )
                except Exception:
                    LOGGER.exception("Failed to record a player-count sample.")

    @poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(hours=PRUNE_INTERVAL_HOURS)
    async def prune(self) -> None:
        days = float(self.bot.config.module("playerstats.retention-days", DEFAULT_RETENTION_DAYS))
        cutoff = (utcnow() - timedelta(days=days)).timestamp()
        try:
            removed = await self.bot.storage.prune_player_history(cutoff)
            if removed:
                LOGGER.info("Pruned %d player-count samples older than %s days.", removed, days)
        except Exception:
            LOGGER.exception("Failed to prune player-count history.")

    @prune.before_loop
    async def _before_prune(self) -> None:
        await self.bot.wait_until_ready()


    def _servers(self, ctx: ChannelContext) -> dict[str, dict[str, Any]]:
        return configured_servers(ctx.server_config("modules.serverstatus", {}))

    def _resolve(self, ctx: ChannelContext, requested: str) -> str | None:
        servers = self._servers(ctx)
        if requested and requested in servers:
            return requested
        if requested:
            return None
        default = ctx.server_config("modules.serverstatus.default", None)
        if default in servers:
            return str(default)
        return next(iter(servers), None)

    async def _who_embed(self, ctx: ChannelContext, requested: str) -> discord.Embed:
        servers = self._servers(ctx)
        if not servers:
            return discord.Embed(description="No status configuration for this Discord server!")

        key = self._resolve(ctx, requested)
        if key is None:
            return discord.Embed(
                description=f"Unknown key `{requested}`. Known: {', '.join(sorted(servers))}"
            )


        if not ctx.server_config(f"modules.serverstatus.{key}.who_enabled", True):
            return discord.Embed(description=f"The player list for `{key}` isn't public.")

        snapshot = await gameserver.query(key, servers[key])
        if not snapshot.online:
            return discord.Embed(
                title=key, description=f"Couldn't reach it: {snapshot.error}", color=0xE66767
            )
        if not snapshot.names_available:
            return discord.Embed(
                title=key,
                description=(
                    f"**{snapshot.players}** players online. "
                    f"{snapshot.kind.upper()} servers don't publish a player list."
                ),
            )

        embed = discord.Embed(
            title=f"{key} — {plural(snapshot.players, 'player')} online", color=0x3987E5
        )

        if not snapshot.player_names:
            embed.description = "Nobody's on."
        else:

            shown = [
                discord.utils.escape_markdown(name)
                for name in sorted(snapshot.player_names, key=str.lower)[:MAX_NAMES_SHOWN]
            ]
            hidden = len(snapshot.player_names) - len(shown)

            if len(shown) < COLUMN_THRESHOLD:
                embed.description = "\n".join(shown)
            else:
                for block in name_columns(shown):


                    embed.add_field(name="\u200b", value=block, inline=True)
            if hidden > 0:
                embed.description = (embed.description or "") + f"\n...and {hidden} more"

        footer = []
        if snapshot.map_name:
            footer.append(snapshot.map_name)
        if snapshot.gamestate:
            footer.append(f"round {snapshot.gamestate}")
        if snapshot.station_time:
            footer.append(f"station time {snapshot.station_time}")
        if footer:
            embed.set_footer(text=" · ".join(footer))
        return embed

    async def _players_summary(self, ctx: ChannelContext) -> str:
        servers = self._servers(ctx)
        if not servers:
            return "No status configuration for this Discord server!"

        snapshots = await asyncio.gather(
            *(gameserver.query(key, config) for key, config in sorted(servers.items()))
        )
        lines = []
        total = 0
        for snapshot in snapshots:
            if snapshot.online:
                total += snapshot.players
                extra = f" ({snapshot.map_name})" if snapshot.map_name else ""
                lines.append(f"**{snapshot.key}**: {plural(snapshot.players, 'player')}{extra}")
            else:
                lines.append(f"**{snapshot.key}**: offline ({snapshot.error})")

        if len(lines) > 1:
            lines.append(f"\n**{total}** {'player' if total == 1 else 'players'} across all servers.")
        return "\n".join(lines)

    async def _render_graph(
        self, ctx: ChannelContext, requested: str, period: timedelta
    ) -> tuple[discord.File | None, str]:
        servers = self._servers(ctx)
        if not servers:
            return None, "No status configuration for this Discord server!"

        if requested and requested.lower() == "all":
            keys = sorted(servers)
        else:
            key = self._resolve(ctx, requested)
            if key is None:
                return None, f"Unknown key `{requested}`. Known: {', '.join(sorted(servers))}"
            keys = [key]

        since = (utcnow() - period).timestamp()
        series: dict[str, list[tuple[float, int]]] = {}
        for key in keys:
            history = await self.bot.storage.player_history(ctx.server.id, key, since)
            if history:
                series[key] = [(sample.ts, sample.players) for sample in history]

        if not series:
            return None, (
                "I don't have any player history for that yet. "
                "I sample every few minutes, so give me a little while."
            )

        label = _describe(period)
        title = f"Players over the last {label}"
        if len(series) == 1:
            title = f"{next(iter(series))} — players over the last {label}"

        try:
            png = await asyncio.to_thread(render_player_chart, series, title)
        except ChartUnavailable as e:
            return None, str(e)
        except ValueError as e:
            return None, str(e)
        except Exception:
            LOGGER.exception("Chart rendering failed.")
            return None, "The chart renderer fell over. Check my logs."

        return discord.File(io.BytesIO(png), filename="players.png"), ""


    @regex_command("who", r"who\s*(\S*)", help_topic="who")
    async def who(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        await ctx.send(embed=await self._who_embed(ctx, match.group(1).strip()))

    @regex_command("players", r"players\b")
    async def players(
        self, ctx: ChannelContext, match: re.Match[str], message: discord.Message
    ) -> None:
        await ctx.send(await self._players_summary(ctx))

    async def _highpop_embed(
        self, ctx: ChannelContext, requested: str, period: timedelta
    ) -> discord.Embed:
        servers = self._servers(ctx)
        if not servers:
            return discord.Embed(description="No status configuration for this Discord server!")

        key = self._resolve(ctx, requested)
        if key is None:
            return discord.Embed(
                description=f"Unknown key `{requested}`. Known: {', '.join(sorted(servers))}"
            )

        since = (utcnow() - period).timestamp()
        history = await self.bot.storage.player_history(ctx.server.id, key, since)
        stats = summarise([(s.ts, s.players) for s in history])
        if stats is None:
            return discord.Embed(
                description=(
                    "I don't have any player history for that yet. I sample every few "
                    "minutes, so give me a little while."
                )
            )

        embed = discord.Embed(
            title=f"{key} — population over the last {_describe(period)}", color=0x3987E5
        )
        embed.add_field(
            name="Peak",
            value=f"**{plural(stats.peak, 'player')}**\n<t:{int(stats.peak_at)}:F>",
            inline=True,
        )
        embed.add_field(name="Average", value=f"**{stats.mean:.1f}** players", inline=True)

        if (hour := stats.busiest_hour) is not None:
            embed.add_field(
                name="Busiest time",
                value=f"**{hour:02d}:00–{(hour + 1) % 24:02d}:00** UTC\n"
                f"averaging {stats.by_hour[hour]:.1f} players",
                inline=True,
            )
        if (day := stats.busiest_weekday) is not None:
            embed.add_field(
                name="Busiest day",
                value=f"**{WEEKDAYS[day]}**\naveraging {stats.by_weekday[day]:.1f} players",
                inline=True,
            )
        if (quiet := stats.quietest_hour) is not None:
            embed.add_field(
                name="Deadest time",
                value=f"**{quiet:02d}:00–{(quiet + 1) % 24:02d}:00** UTC\n"
                f"averaging {stats.by_hour[quiet]:.1f} players",
                inline=True,
            )

        embed.set_footer(text=f"from {stats.samples:,} samples · times UTC")
        return embed

    @regex_command("highpop", r"(?:highpop|peak)\s*(\S*)\s*(\S*)", help_topic="highpop")
    async def highpop(
        self, ctx: ChannelContext, match: re.Match[str], message: discord.Message
    ) -> None:
        requested, period = _split_args(match.group(1), match.group(2), DEFAULT_HIGHPOP_PERIOD)
        if period is None:
            await ctx.send("I don't understand that period. Try `7d`, `30d` or `12w`.")
            return

        embed = await self._highpop_embed(ctx, requested, period)
        chart = await self._hour_chart(ctx, requested, period)
        if chart is not None:
            await ctx.send(embed=embed, file=chart)
        else:
            await ctx.send(embed=embed)

    async def _hour_chart(
        self, ctx: ChannelContext, requested: str, period: timedelta
    ) -> discord.File | None:
        key = self._resolve(ctx, requested)
        if key is None:
            return None
        since = (utcnow() - period).timestamp()
        history = await self.bot.storage.player_history(ctx.server.id, key, since)
        stats = summarise([(s.ts, s.players) for s in history])
        if stats is None or len(stats.by_hour) < 12:
            return None

        try:
            png = await asyncio.to_thread(
                render_hourly_chart, stats.by_hour, f"{key} — average players by hour (UTC)"
            )
        except (ChartUnavailable, ValueError):
            return None
        except Exception:
            LOGGER.exception("Hourly chart rendering failed.")
            return None
        return discord.File(io.BytesIO(png), filename="highpop.png")

    @app_commands.command(name="highpop", description="When is the server busiest?")
    @app_commands.describe(
        server="Which server. Leave blank for the default.",
        period="How far back: 7d, 30d, 12w. Defaults to 30d.",
    )
    async def slash_highpop(
        self, interaction: discord.Interaction, server: str = "", period: str = "30d"
    ) -> None:
        ctx = self._interaction_ctx(interaction)
        if ctx is None:
            await interaction.response.send_message("Not configured for this server.", ephemeral=True)
            return
        window = parse_period(period)
        if window is None:
            await interaction.response.send_message(
                "I don't understand that period. Try `7d`, `30d` or `12w`.", ephemeral=True
            )
            return
        await interaction.response.defer()
        embed = await self._highpop_embed(ctx, server.strip(), window)
        chart = await self._hour_chart(ctx, server.strip(), window)
        if chart is not None:
            await interaction.followup.send(embed=embed, file=chart)
        else:
            await interaction.followup.send(embed=embed)

    @regex_command("graph", r"(?:graph|playergraph)\s*(\S*)\s*(\S*)", help_topic="graph")
    async def graph(self, ctx: ChannelContext, match: re.Match[str], message: discord.Message) -> None:
        requested, period = _split_args(match.group(1), match.group(2), DEFAULT_PERIOD)
        if period is None:
            await ctx.send("I don't understand that period. Try `24h`, `7d` or `2w`.")
            return

        async with _typing(ctx):
            attachment, error = await self._render_graph(ctx, requested, period)
        if attachment is None:
            await ctx.send(error)
        else:
            await ctx.send(file=attachment)


    @app_commands.command(name="who", description="See who's currently playing.")
    @app_commands.describe(server="Which server. Leave blank for the default.")
    async def slash_who(self, interaction: discord.Interaction, server: str = "") -> None:
        ctx = self._interaction_ctx(interaction)
        if ctx is None:
            await interaction.response.send_message("Not configured for this server.", ephemeral=True)
            return
        await interaction.response.defer()
        await interaction.followup.send(embed=await self._who_embed(ctx, server.strip()))

    @app_commands.command(name="players", description="Player counts across every server.")
    async def slash_players(self, interaction: discord.Interaction) -> None:
        ctx = self._interaction_ctx(interaction)
        if ctx is None:
            await interaction.response.send_message("Not configured for this server.", ephemeral=True)
            return
        await interaction.response.defer()
        await interaction.followup.send(await self._players_summary(ctx))

    @app_commands.command(name="graph", description="Graph player counts over time.")
    @app_commands.describe(
        server="Which server, or 'all' to overlay them.",
        period="How far back: 24h, 7d, 2w. Defaults to 24h.",
    )
    async def slash_graph(
        self, interaction: discord.Interaction, server: str = "", period: str = "24h"
    ) -> None:
        ctx = self._interaction_ctx(interaction)
        if ctx is None:
            await interaction.response.send_message("Not configured for this server.", ephemeral=True)
            return
        window = parse_period(period)
        if window is None:
            await interaction.response.send_message(
                "I don't understand that period. Try `24h`, `7d` or `2w`.", ephemeral=True
            )
            return

        await interaction.response.defer()
        attachment, error = await self._render_graph(ctx, server.strip(), window)
        if attachment is None:
            await interaction.followup.send(error, ephemeral=True)
        else:
            await interaction.followup.send(file=attachment)

    @slash_who.autocomplete("server")
    @slash_graph.autocomplete("server")
    async def _server_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        server = self.bot.servers.get(interaction.guild_id or 0)
        if server is None:
            return []
        keys = sorted(configured_servers(server.get("modules.serverstatus", {})))
        matches = [k for k in keys if current.lower() in k.lower()]
        return [app_commands.Choice(name=k, value=k) for k in matches[:25]]

    def _interaction_ctx(self, interaction: discord.Interaction) -> ChannelContext | None:
        server = self.bot.servers.get(interaction.guild_id or 0)
        if server is None:
            return None
        return ChannelContext(server, interaction.channel_id or 0)

    def help_articles(self) -> dict[str, str]:
        return {
            "who": (
                "`@MoMMI who` lists everyone currently connected, straight from the "
                "game server's own status response.\n"
                "`@MoMMI who <server>` picks a specific one, and `@MoMMI players` "
                "gives you just the counts across every server at once."
            ),
            "highpop": (
                "`@MoMMI highpop` tells you when the server is actually busy: the all-time "
                "peak in the window, the overall average, and which hour and which day of "
                "the week draw the biggest crowds.\n\n"
                "`@MoMMI highpop 7d` changes the window (default 30 days). "
                "All times are UTC."
            ),
            "graph": (
                "`@MoMMI graph` draws player numbers over the last 24 hours.\n"
                "`@MoMMI graph 7d` changes the window -- `h`, `d` and `w` all work.\n"
                "`@MoMMI graph all 7d` overlays every server on one chart.\n\n"
                "I sample every few minutes and keep about three months, so the "
                "graph only covers the time since I was last set up."
            ),
        }


def _split_args(
    first: str, second: str, default: timedelta
) -> tuple[str, timedelta | None]:
    first, second = first.strip(), second.strip()
    if first and parse_period(first) is not None:
        server, raw = second, first
    else:
        server, raw = first, second
    if not raw:
        return server, default
    return server, parse_period(raw)


def _describe(period: timedelta) -> str:
    hours = period.total_seconds() / 3600
    if hours < 48:
        return f"{int(hours)} hours"
    days = hours / 24
    if days < 14:
        return f"{int(days)} days"
    return f"{int(days / 7)} weeks"


@contextlib.asynccontextmanager
async def _typing(ctx: ChannelContext) -> AsyncIterator[None]:
    channel = ctx.channel
    if channel is None or not hasattr(channel, "typing"):
        yield
        return
    try:
        async with channel.typing():
            yield
    except discord.HTTPException:
        yield


async def setup(bot: MoMMI) -> None:
    await bot.add_cog(PlayerStats(bot))
