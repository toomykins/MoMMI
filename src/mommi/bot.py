from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

from mommi.commloop import CommLoopServer
from mommi.config import Config
from mommi.context import ChannelContext, ServerContext
from mommi.framework import MoMMICog, RegexCommand, deny
from mommi.storage import Storage

LOGGER = logging.getLogger(__name__)
CHAT_LOGGER = logging.getLogger("chat")


EXTENSIONS = [
    "mommi.cogs.admin",
    "mommi.cogs.chance",
    "mommi.cogs.fun",
    "mommi.cogs.help",
    "mommi.cogs.markov",
    "mommi.cogs.reminders",
    "mommi.cogs.responses",
    "mommi.cogs.serverstatus",
    "mommi.cogs.playerstats",
    "mommi.cogs.restart",
    "mommi.cogs.gamenudge",
    "mommi.cogs.mirror",
    "mommi.cogs.github",
    "mommi.cogs.changelog",
    "mommi.cogs.units",
    "mommi.cogs.runtimelog",
    "mommi.cogs.ss14",
    "mommi.cogs.irc",
    "mommi.cogs.codehandling",
    "mommi.cogs.userinfo",
]


CommHandler = Callable[[ChannelContext, Any, str], Awaitable[None]]

GlobalCommHandler = Callable[[str, Any, str], Awaitable[None]]


class MoMMI(commands.Bot):
    def __init__(self, config: Config, data_dir: Path) -> None:
        intents = discord.Intents.default()


        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False),
        )

        self.config = config
        self.data_dir = data_dir
        self.storage = Storage(data_dir / "mommi.sqlite3")
        self.servers: dict[int, ServerContext] = {}
        self.servers_by_name: dict[str, ServerContext] = {}
        self.commloop: CommLoopServer | None = None
        self.web_runner: Any = None

        self._prefix_re: re.Pattern[str] | None = None
        self._closing = False


    async def setup_hook(self) -> None:
        await self.storage.open()

        for server_config in self.config.servers:
            context = ServerContext(self, server_config)
            self.servers[context.id] = context
            self.servers_by_name[context.name] = context

        failed = 0
        for extension in EXTENSIONS:
            try:
                await self.load_extension(extension)
            except Exception:


                LOGGER.exception("Failed to load extension %s.", extension)
                failed += 1
        LOGGER.info("Loaded %d/%d extensions.", len(EXTENSIONS) - failed, len(EXTENSIONS))

        await self._start_commloop()
        await self._start_web()

    async def _start_commloop(self) -> None:
        cfg = self.config.main.commloop
        self.commloop = CommLoopServer(cfg.address, cfg.port, cfg.password, self.dispatch_comm)
        try:
            await self.commloop.start()
        except OSError:
            LOGGER.exception("Could not bind the commloop to %s:%s.", cfg.address, cfg.port)
            self.commloop = None

    async def _start_web(self) -> None:
        if not self.config.main.web.enabled:
            LOGGER.info("Web server disabled by config.")
            return
        from mommi.web import start_web

        try:
            self.web_runner = await start_web(self)
        except OSError:
            LOGGER.exception("Could not start the web server.")

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        LOGGER.info("Shutting down.")

        if self.commloop is not None:
            await self.commloop.stop()
        if self.web_runner is not None:
            await self.web_runner.cleanup()


        await super().close()
        await self.storage.close()
        LOGGER.info("Goodbye.")

    async def on_ready(self) -> None:
        assert self.user is not None
        self._prefix_re = re.compile(rf"^<@!?{self.user.id}>\s*")
        LOGGER.info("Logged in as %s (%s).", self.user, self.user.id)

        configured = set(self.servers)
        joined = {g.id for g in self.guilds}
        for guild in self.guilds:
            mark = "" if guild.id in configured else "  [NO CONFIG]"
            LOGGER.info("  %s (%s)%s", guild.name, guild.id, mark)
        for missing in configured - joined:
            LOGGER.warning(
                "servers.toml configures guild %s (%s) that we are not in.",
                self.servers[missing].name,
                missing,
            )


    def server(self, identifier: int | str) -> ServerContext | None:
        if isinstance(identifier, int):
            return self.servers.get(identifier)
        if identifier.isdigit():
            return self.servers.get(int(identifier))
        return self.servers_by_name.get(identifier)

    def context_for(self, channel: discord.abc.Messageable, guild_id: int) -> ChannelContext | None:
        server = self.servers.get(guild_id)
        if server is None:
            return None
        return ChannelContext(server, getattr(channel, "id", 0))

    def iter_regex_commands(self) -> list[RegexCommand]:
        out: list[RegexCommand] = []
        for cog in self.cogs.values():
            if isinstance(cog, MoMMICog):
                out.extend(cog.regex_commands)
        return out


    async def on_message(self, message: discord.Message) -> None:
        if self._closing or message.guild is None:
            return

        server = self.servers.get(message.guild.id)
        if server is None:

            return

        ctx = ChannelContext(server, message.channel.id)
        self._log_chat(server, message)

        is_self = self.user is not None and message.author.id == self.user.id
        offset = 0
        prefixed = False
        if self._prefix_re is not None:
            prefix_match = self._prefix_re.match(message.content)
            if prefix_match:
                prefixed = True
                offset = prefix_match.end()

        for command in self.iter_regex_commands():
            if is_self and not command.allow_self:
                continue
            if command.prefix and not prefixed:
                continue

            match = command.match(message.content, offset)
            if match is None:
                continue

            if command.roles and not any(ctx.is_role(message.author, r) for r in command.roles):
                await deny(ctx)
                continue

            try:
                await command.invoke(ctx, match, message)
            except Exception:


                LOGGER.exception("Error in command %s.", command.name)

    def _log_chat(self, server: ServerContext, message: discord.Message) -> None:
        line = f"({server.name}/{getattr(message.channel, 'name', '?')}) {message.author}: {message.content}"
        if message.attachments:
            line += " [attachments] " + " ".join(a.url for a in message.attachments)
        CHAT_LOGGER.info(line)


    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if self._closing or payload.guild_id is None:
            return
        if self.user is not None and payload.user_id == self.user.id:
            return
        server = self.servers.get(payload.guild_id)
        if server is None:
            return
        ctx = ChannelContext(server, payload.channel_id)
        for cog in self.cogs.values():
            handler = getattr(cog, "on_mommi_reaction", None)
            if handler is None:
                continue
            try:
                await handler(ctx, payload)
            except Exception:
                LOGGER.exception("Error in reaction handler of %s.", type(cog).__name__)

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if self._closing or payload.guild_id is None:
            return
        server = self.servers.get(payload.guild_id)
        if server is None:
            return
        ctx = ChannelContext(server, payload.channel_id)
        for cog in self.cogs.values():
            handler = getattr(cog, "on_mommi_delete", None)
            if handler is None:
                continue
            try:
                await handler(ctx, payload)
            except Exception:
                LOGGER.exception("Error in delete handler of %s.", type(cog).__name__)


    async def dispatch_comm(self, msg_type: str, meta: str, content: Any) -> None:
        for cog in self.cogs.values():
            handler = getattr(cog, "on_global_comm", None)
            if handler is None:
                continue
            try:
                await handler(msg_type, content, meta)
            except Exception:
                LOGGER.exception("Error in global comm handler of %s.", type(cog).__name__)

        handlers = self._comm_handlers(msg_type)
        if not handlers:
            LOGGER.debug("No cog handles commloop type %r.", msg_type)
            return

        targets = self.config.routes_for(msg_type, meta)
        if not targets:
            LOGGER.warning("Commloop message type=%r meta=%r has no configured route.", msg_type, meta)
            return

        for server_ident, channel_ident in targets:
            server = self.server(server_ident)
            if server is None:
                LOGGER.error("Route for %s/%s names unknown server %r.", msg_type, meta, server_ident)
                continue
            ctx = server.channel(channel_ident)
            if ctx is None:
                LOGGER.error("Route for %s/%s names unknown channel %r.", msg_type, meta, channel_ident)
                continue
            for handler in handlers:
                try:
                    await handler(ctx, content, meta)
                except Exception:
                    LOGGER.exception("Error in comm handler for %r.", msg_type)

    def _comm_handlers(self, msg_type: str) -> list[CommHandler]:
        out = []
        for cog in self.cogs.values():
            registry: dict[str, CommHandler] = getattr(cog, "comm_events", {})
            handler = registry.get(msg_type)
            if handler is not None:
                out.append(handler)
        return out


async def run(config: Config, data_dir: Path) -> None:
    bot = MoMMI(config, data_dir)
    async with bot:
        await bot.start(config.main.bot.token)


def run_blocking(config: Config, data_dir: Path) -> None:
    try:
        asyncio.run(run(config, data_dir))
    except KeyboardInterrupt:
        LOGGER.info("Interrupted.")
