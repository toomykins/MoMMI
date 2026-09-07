from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import discord

from mommi.bot import MoMMI
from mommi.context import ChannelContext
from mommi.framework import MoMMICog, always_command

LOGGER = logging.getLogger(__name__)
CHAT_LOGGER = logging.getLogger("chat")

MENTION_RE = re.compile(r"<@!?(\d+)>")
ROLE_RE = re.compile(r"<@&(\d+)>")
CHANNEL_RE = re.compile(r"<#(\d+)>")
EMOJI_RE = re.compile(r"<a?:(\w+):(\d+)>")
IRC_MENTION_RE = re.compile(r"@([^@\s]+)@")


IGNORED_NICKS = {"travis-ci", "vg-bot", "py-ctcp"}

RELAY_MARKER = "​**IRC:**"
RECONNECT_DELAY = 30.0

RECENT_MEMORY = 3


class IrcConnection:

    def __init__(self, cog: Irc, name: str, config: dict[str, Any]) -> None:
        self.cog = cog
        self.name = name
        self.address = config["address"]
        self.port = int(config.get("port", 6667))
        self.ssl = bool(config.get("ssl", False))
        user = config.get("user", {})
        self.nick = user.get("nick", "MoMMI")
        self.username = user.get("name", self.nick)
        self.realname = user.get("realname", "MoMMI")
        self.server_password = config.get("password")

        self.channels: list[tuple[str, str, str]] = [
            (c["irc"], c["server"], c["channel"]) for c in config.get("channels", [])
        ]
        self.client: Any = None
        self._recent: list[str] = []
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        import bottom

        self.client = bottom.Client(host=self.address, port=self.port, ssl=self.ssl)
        self._register_handlers()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self.client is not None:
            try:
                self.client.send("QUIT", message="*buzz*")
                await self.client.disconnect()
            except Exception:
                LOGGER.debug("Untidy IRC disconnect from %s.", self.name)

    async def _run(self) -> None:
        while True:
            try:
                await self.client.connect()
                await self.client.wait("client_disconnect")
                LOGGER.warning("Disconnected from IRC network %s.", self.name)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("IRC connection to %s failed.", self.name)
            await asyncio.sleep(RECONNECT_DELAY)

    def _register_handlers(self) -> None:
        client = self.client

        @client.on("client_connect")  # type: ignore[untyped-decorator]
        async def on_connect(**kwargs: Any) -> None:
            if self.server_password:
                client.send("PASS", password=self.server_password)
            client.send("NICK", nick=self.nick)
            client.send("USER", user=self.username, realname=self.realname)
            await client.wait("RPL_ENDOFMOTD")
            for irc_channel, _, _ in self.channels:
                client.send("JOIN", channel=irc_channel)
            LOGGER.info("Connected to IRC network %s as %s.", self.name, self.nick)

        @client.on("PING")  # type: ignore[untyped-decorator]
        def on_ping(message: str = "", **kwargs: Any) -> None:
            client.send("PONG", message=message)

        @client.on("PRIVMSG")  # type: ignore[untyped-decorator]
        async def on_privmsg(
            nick: str = "", target: str = "", message: str = "", **kwargs: Any
        ) -> None:
            if nick == self.nick or nick.lower() in IGNORED_NICKS:
                return
            await self.cog.irc_to_discord(self, target, nick, message)

    def send_to_irc(self, channel: str, text: str) -> None:
        for line in text.splitlines():
            line = line.strip()
            if not line or line in self._recent:
                continue
            self._recent.append(line)
            del self._recent[:-RECENT_MEMORY]
            self.client.send("PRIVMSG", target=channel, message=line[:400])


class Irc(MoMMICog):
    def __init__(self, bot: MoMMI) -> None:
        super().__init__(bot)
        self.connections: dict[str, IrcConnection] = {}
        self._starting = asyncio.create_task(self._start_all())

    async def _start_all(self) -> None:
        await self.bot.wait_until_ready()
        config = self.bot.config.module("irc.servers", {})
        if not config:
            LOGGER.info("No `[irc.servers]` in modules.toml; IRC bridge idle.")
            return
        try:
            import bottom  # noqa: F401
        except ImportError:
            LOGGER.error("IRC is configured but the `bottom` package isn't installed.")
            return

        for name, server_config in config.items():
            try:
                connection = IrcConnection(self, name, server_config)
                await connection.start()
                self.connections[name] = connection
            except Exception:
                LOGGER.exception("Could not start IRC connection %s.", name)

    async def cog_unload(self) -> None:
        self._starting.cancel()
        for connection in self.connections.values():
            await connection.stop()

    async def irc_to_discord(
        self, connection: IrcConnection, target: str, nick: str, text: str
    ) -> None:
        for irc_channel, guild_name, channel_alias in connection.channels:
            if irc_channel.lower() != target.lower():
                continue
            server = self.bot.server(guild_name)
            if server is None:
                continue
            ctx = server.channel(channel_alias)
            if ctx is None:
                continue

            def resolve(found: re.Match[str], ctx: ChannelContext = ctx) -> str:
                return self._resolve_mention(ctx, found.group(1))

            body = IRC_MENTION_RE.sub(resolve, text)
            CHAT_LOGGER.info("(irc/%s) %s: %s", target, nick, text)
            await ctx.send(
                f"{RELAY_MARKER} `{discord.utils.escape_markdown(nick)}`: {body}",
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )

    def _resolve_mention(self, ctx: ChannelContext, name: str) -> str:
        guild = ctx.server.guild
        if guild is None:
            return f"@{name}"
        member = guild.get_member_named(name)
        return member.mention if member else f"@{name}"

    @always_command("irc_relay")
    async def discord_to_irc(
        self, ctx: ChannelContext, match: Any, message: discord.Message
    ) -> None:
        if not self.connections:
            return

        if message.content.startswith(RELAY_MARKER) or message.webhook_id is not None:
            return
        if self.bot.user is not None and message.author.id == self.bot.user.id:
            return

        for connection in self.connections.values():
            for irc_channel, guild_name, channel_alias in connection.channels:
                if ctx.server.name != guild_name or ctx.alias != channel_alias:
                    continue
                text = await self._to_irc_text(ctx, message)
                if text:
                    connection.send_to_irc(irc_channel, f"<{message.author.display_name}> {text}")

    async def _to_irc_text(self, ctx: ChannelContext, message: discord.Message) -> str:
        text = message.content
        guild = ctx.server.guild

        def member(match: re.Match[str]) -> str:
            found = guild.get_member(int(match.group(1))) if guild else None
            return f"@{found.display_name}" if found else "@someone"

        def role(match: re.Match[str]) -> str:
            found = guild.get_role(int(match.group(1))) if guild else None
            return f"@{found.name}" if found else "@role"

        def channel(match: re.Match[str]) -> str:
            found = guild.get_channel(int(match.group(1))) if guild else None
            return f"#{found.name}" if found else "#channel"

        text = MENTION_RE.sub(member, text)
        text = ROLE_RE.sub(role, text)
        text = CHANNEL_RE.sub(channel, text)
        text = EMOJI_RE.sub(lambda m: f":{m.group(1)}:", text)


        github = self.bot.get_cog("GitHub")
        if github is not None and "```" in text:
            text = await github.codeblocks_to_gists(text)  # type: ignore[attr-defined]

        for attachment in message.attachments:
            text += f" {attachment.url}"
        return text.strip()


async def setup(bot: MoMMI) -> None:
    await bot.add_cog(Irc(bot))
