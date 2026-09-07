from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from mommi.bot import MoMMI
from mommi.config import Config
from mommi.context import ChannelContext, ServerContext

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "example"

GUILD_ID = 100000000000000001
CHANNEL_ID = 100000000000000005
BOT_ID = 999
USER_ID = 4242
OWNER_ID = 100000000000000000


@dataclass
class FakeRole:
    id: int
    name: str = "role"

    def is_default(self) -> bool:
        return self.name == "@everyone"

    @property
    def mention(self) -> str:
        return f"<@&{self.id}>"


@dataclass
class FakeUser:
    id: int = BOT_ID
    name: str = "MoMMI"
    bot: bool = True

    def __str__(self) -> str:
        return f"{self.name}#0001"

    def mentioned_in(self, message: Any) -> bool:
        return f"<@{self.id}>" in message.content or f"<@!{self.id}>" in message.content


@dataclass
class FakeMember:
    id: int = USER_ID
    name: str = "someguy"
    display_name: str = "someguy"
    bot: bool = False
    roles: list[FakeRole] = field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.name}#0002"

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"


@dataclass
class FakeChannel:
    id: int = CHANNEL_ID
    name: str = "coderbus"


@dataclass
class FakeGuild:
    id: int = GUILD_ID
    name: str = "vgstation"
    roles: list[FakeRole] = field(default_factory=list)
    members: dict[int, FakeMember] = field(default_factory=dict)

    def get_member(self, snowflake: int) -> FakeMember | None:
        return self.members.get(snowflake)

    def get_role(self, snowflake: int) -> FakeRole | None:
        return next((r for r in self.roles if r.id == snowflake), None)

    def get_channel(self, snowflake: int) -> FakeChannel | None:
        return FakeChannel() if snowflake == CHANNEL_ID else None

    @property
    def text_channels(self) -> list[FakeChannel]:
        return [FakeChannel()]


class FakeMessage:

    def __init__(self, content: str, author: Any, guild: FakeGuild, channel: FakeChannel) -> None:
        self.id = 555
        self.content = content
        self.author = author
        self.guild = guild
        self.channel = channel
        self.attachments: list[Any] = []
        self.webhook_id = None
        self.reactions: list[str] = []

    async def add_reaction(self, emoji: str) -> None:
        self.reactions.append(emoji)

    async def remove_reaction(self, emoji: str, member: Any) -> None:
        if emoji in self.reactions:
            self.reactions.remove(emoji)


class Harness:

    def __init__(self, bot: MoMMI, guild: FakeGuild) -> None:
        self.bot = bot
        self.guild = guild
        self.sent: list[dict[str, Any]] = []

    async def say(self, content: str, *, author: Any = None, mention: bool = True) -> list[dict]:
        self.sent.clear()
        text = f"<@{BOT_ID}> {content}" if mention else content
        message = FakeMessage(text, author or FakeMember(), self.guild, FakeChannel())
        await self.bot.on_message(message)  # type: ignore[arg-type]
        self.last_message = message
        return list(self.sent)

    @property
    def texts(self) -> list[str]:
        return [s["content"] for s in self.sent if s.get("content")]

    @property
    def embeds(self) -> list[Any]:
        return [s["embed"] for s in self.sent if s.get("embed") is not None]

    def only_text(self) -> str:
        assert len(self.texts) == 1, f"expected exactly one message, got {self.texts}"
        return self.texts[0]


@pytest.fixture
async def harness(tmp_path: Path, monkeypatch):
    config = Config.load(EXAMPLE)

    config.main.web.enabled = False
    config.main.commloop.port = 0

    bot = MoMMI(config, tmp_path / "data")
    bot._connection.user = FakeUser()  # type: ignore[assignment]
    bot._prefix_re = re.compile(rf"^<@!?{BOT_ID}>\s*")

    guild = FakeGuild(
        roles=[FakeRole(100000000000000006, "owner"), FakeRole(100000000000000001, "admin")]
    )

    collected: list[dict[str, Any]] = []

    async def capture(self: ChannelContext, content: str | None = None, **kwargs: Any) -> None:
        collected.append({"channel": self.id, "content": content, **kwargs})
        return None

    monkeypatch.setattr(ChannelContext, "send", capture)


    monkeypatch.setattr(ServerContext, "guild", property(lambda self: guild))

    await bot.setup_hook()

    h = Harness(bot, guild)
    h.sent = collected
    try:
        yield h
    finally:
        await bot.close()
