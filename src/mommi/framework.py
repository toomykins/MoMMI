from __future__ import annotations

import logging
import random
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, TypeAlias, TypeVar

import discord
from discord.ext import commands

from mommi.config import RoleType
from mommi.context import ChannelContext

if TYPE_CHECKING:
    from mommi.bot import MoMMI

LOGGER = logging.getLogger(__name__)

Handler = Callable[[Any, ChannelContext, "re.Match[str]", discord.Message], Awaitable[None]]


HelpArticle: TypeAlias = "str | Callable[[ChannelContext], Awaitable[str]]"
T = TypeVar("T", bound=Handler)

_ATTR = "__mommi_command__"
_COMM_ATTR = "__mommi_comm_event__"
_GLOBAL_COMM_ATTR = "__mommi_global_comm_event__"


class RegexCommand:

    def __init__(
        self,
        name: str,
        pattern: re.Pattern[str] | None,
        callback: Handler,
        *,
        prefix: bool,
        roles: list[RoleType] | None,
        allow_self: bool,
        help_topic: str | None,
    ) -> None:
        self.name = name
        self.pattern = pattern
        self.callback = callback

        self.prefix = prefix
        self.roles = roles

        self.allow_self = allow_self
        self.help_topic = help_topic
        self.cog: commands.Cog | None = None

    def __repr__(self) -> str:
        return f"<RegexCommand {self.name}>"

    def match(self, content: str, offset: int) -> re.Match[str] | None:
        if self.pattern is None:


            return re.compile("").match(content, offset)
        return self.pattern.match(content, offset)

    async def invoke(
        self, ctx: ChannelContext, match: re.Match[str], message: discord.Message
    ) -> None:
        await self.callback(self.cog, ctx, match, message)


def regex_command(
    name: str,
    pattern: str,
    *,
    flags: int = re.IGNORECASE,
    roles: list[RoleType] | None = None,
    allow_self: bool = False,
    help_topic: str | None = None,
) -> Callable[[T], T]:

    def decorator(func: T) -> T:
        setattr(
            func,
            _ATTR,
            RegexCommand(
                name,
                re.compile(pattern, flags),
                func,
                prefix=True,
                roles=roles,
                allow_self=allow_self,
                help_topic=help_topic,
            ),
        )
        return func

    return decorator


def always_command(
    name: str, *, allow_self: bool = False, roles: list[RoleType] | None = None
) -> Callable[[T], T]:

    def decorator(func: T) -> T:
        setattr(
            func,
            _ATTR,
            RegexCommand(
                name, None, func, prefix=False, roles=roles, allow_self=allow_self, help_topic=None
            ),
        )
        return func

    return decorator


CommHandler = Callable[[Any, ChannelContext, Any, str], Awaitable[None]]
GlobalCommHandler = Callable[[Any, str, Any, str], Awaitable[None]]
C = TypeVar("C", bound=CommHandler)
G = TypeVar("G", bound=GlobalCommHandler)


def comm_event(msg_type: str) -> Callable[[C], C]:

    def decorator(func: C) -> C:
        setattr(func, _COMM_ATTR, msg_type)
        return func

    return decorator


def global_comm_event(func: G) -> G:
    setattr(func, _GLOBAL_COMM_ATTR, True)
    return func


class MoMMICog(commands.Cog):

    def __init__(self, bot: MoMMI) -> None:
        self.bot = bot
        self.regex_commands: list[RegexCommand] = []

        self.comm_events: dict[str, Callable[[ChannelContext, Any, str], Awaitable[None]]] = {}
        self._global_comm: list[Callable[[str, Any, str], Awaitable[None]]] = []

        for attr in dir(type(self)):
            member = getattr(type(self), attr, None)
            command = getattr(member, _ATTR, None)
            if isinstance(command, RegexCommand):

                bound = RegexCommand(
                    command.name,
                    command.pattern,
                    command.callback,
                    prefix=command.prefix,
                    roles=command.roles,
                    allow_self=command.allow_self,
                    help_topic=command.help_topic,
                )
                bound.cog = self
                self.regex_commands.append(bound)
                continue

            msg_type = getattr(member, _COMM_ATTR, None)
            if msg_type is not None:
                if msg_type in self.comm_events:
                    LOGGER.error(
                        "Cog %s declares two handlers for commloop type %r.",
                        type(self).__name__,
                        msg_type,
                    )
                self.comm_events[msg_type] = getattr(self, attr)
                continue

            if getattr(member, _GLOBAL_COMM_ATTR, False):
                self._global_comm.append(getattr(self, attr))
        LOGGER.debug("Cog %s registered %d regex commands.", type(self).__name__, len(self.regex_commands))

    async def on_global_comm(self, msg_type: str, content: Any, meta: str) -> None:
        for handler in self._global_comm:
            await handler(msg_type, content, meta)

    def help_articles(self) -> Mapping[str, HelpArticle]:
        return {}


async def deny(ctx: ChannelContext) -> None:
    choices = ctx.main_config("bot.deny-messages", ["*buzz*"])
    await ctx.send(random.choice(choices) if choices else "*buzz*")
