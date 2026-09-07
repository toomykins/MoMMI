from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord

from mommi.config import RoleType, ServerConfig

if TYPE_CHECKING:
    from mommi.bot import MoMMI

LOGGER = logging.getLogger(__name__)

_MISSING = object()


def member_roles(user: object) -> list[Any] | None:
    roles = getattr(user, "roles", None)
    return list(roles) if roles is not None else None


class ServerContext:

    def __init__(self, bot: MoMMI, config: ServerConfig) -> None:
        self.bot = bot
        self.config = config
        self.id = config.id

        self.name = config.name
        self.roles = config.resolved_roles()

        self.channel_aliases = dict(config.channels)
        self._alias_by_id = {v: k for k, v in config.channels.items()}

    @property
    def guild(self) -> discord.Guild | None:
        return self.bot.get_guild(self.id)

    @property
    def visible_name(self) -> str:
        guild = self.guild
        return guild.name if guild else f"<uncached guild {self.id}>"

    def alias_for(self, channel_id: int) -> str | None:
        return self._alias_by_id.get(channel_id)

    def resolve_channel_id(self, identifier: int | str) -> int | None:
        if isinstance(identifier, int):
            return identifier
        if identifier.isdigit():
            return int(identifier)
        return self.channel_aliases.get(identifier)

    def channel(self, identifier: int | str) -> ChannelContext | None:
        channel_id = self.resolve_channel_id(identifier)
        if channel_id is None:
            return None
        return ChannelContext(self, channel_id)

    def get(self, key: str, default: Any = _MISSING) -> Any:
        return self.config.get(key, default)


class ChannelContext:

    def __init__(self, server: ServerContext, channel_id: int) -> None:
        self.server = server
        self.bot = server.bot
        self.id = channel_id

    def __repr__(self) -> str:
        return f"<ChannelContext {self.server.name}/{self.alias or self.id}>"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ChannelContext) and other.id == self.id

    def __hash__(self) -> int:
        return hash(self.id)

    @property
    def alias(self) -> str | None:
        return self.server.alias_for(self.id)

    @property
    def channel(self) -> discord.abc.Messageable | None:
        found = self.bot.get_channel(self.id)
        return found if isinstance(found, discord.abc.Messageable) else None

    @property
    def name(self) -> str:
        channel = self.bot.get_channel(self.id)
        return getattr(channel, "name", str(self.id))

    def matches(self, identifier: int | str) -> bool:
        if isinstance(identifier, int):
            return self.id == identifier
        return self.alias == identifier or (identifier.isdigit() and int(identifier) == self.id)

    async def send(self, content: str | None = None, **kwargs: Any) -> discord.Message | None:
        channel = self.channel
        if channel is None:
            LOGGER.warning("Tried to send to unknown/uncached channel %s.", self.id)
            return None
        try:
            return await channel.send(content, **kwargs)
        except discord.Forbidden:
            LOGGER.warning("Missing permission to send in %s (%s).", self.name, self.id)
        except discord.HTTPException:
            LOGGER.exception("Failed to send a message to %s (%s).", self.name, self.id)
        return None


    def server_config(self, key: str, default: Any = _MISSING) -> Any:
        return self.server.get(key, default)

    def module_config(self, key: str, default: Any = _MISSING) -> Any:
        return self.bot.config.module(key, default)

    def main_config(self, key: str, default: Any = _MISSING) -> Any:
        from mommi.config import _dotted

        return _dotted(self.bot.config.main.model_dump(by_alias=True), key, default, "main.toml")


    def is_role(self, member: discord.abc.User, role: RoleType) -> bool:
        if member.id == self.bot.config.main.bot.owner:
            return True
        held = member_roles(member)
        if held is None:


            LOGGER.debug("Role check for non-member %s in guild %s.", member.id, self.server.id)
            return False

        allowed = self.server.roles.get(role)
        if not allowed:
            return False
        return any(r.id in allowed for r in held)


    async def get_storage(self, key: str, default: Any = None) -> Any:
        return await self.bot.storage.get(self.server.id, key, default)

    async def set_storage(self, key: str, value: Any) -> None:
        await self.bot.storage.set(self.server.id, key, value)
