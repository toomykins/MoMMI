from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

LOGGER = logging.getLogger(__name__)

T = TypeVar("T")


_MISSING = object()


class RoleType(str, Enum):

    OWNER = "OWNER"
    CODER = "CODER"
    ADMIN = "ADMIN"


class BotConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    token: str
    owner: int

    deny_messages: list[str] = Field(default_factory=lambda: ["*buzz*"], alias="deny-messages")


class CommloopConfig(BaseModel):

    model_config = ConfigDict(extra="allow")

    address: str = "localhost"
    port: int = 1679
    password: str

    route: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ChangelogConfig(BaseModel):

    model_config = ConfigDict(extra="allow")


    repo_path: Path | None = Field(default=None, alias="repo-path")

    repo_name: str | None = Field(default=None, alias="repo-name")

    delay: int = 30
    ssh_key: Path | None = Field(default=None, alias="ssh-key")

    script: str = "tools/changelog/ss13_genchangelog.py"
    changelog_html: str = Field(default="html/changelog.html", alias="changelog-html")
    changelog_dir: str = Field(default="html/changelogs", alias="changelog-dir")


class WebConfig(BaseModel):

    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    address: str = "0.0.0.0"
    port: int = 40000

    github_key: str | None = Field(default=None, alias="github-key")

    verify_github: bool = Field(default=True, alias="verify-github")
    changelog: ChangelogConfig = Field(default_factory=ChangelogConfig)


class ServerConfig(BaseModel):

    model_config = ConfigDict(extra="allow")

    id: int

    name: str

    channels: dict[str, int] = Field(default_factory=dict)

    roles: dict[str, int | list[int]] = Field(default_factory=dict)

    modules: dict[str, Any] = Field(default_factory=dict)

    def resolved_roles(self) -> dict[RoleType, set[int]]:
        out: dict[RoleType, set[int]] = {}
        for name, value in self.roles.items():
            try:
                role = RoleType[name.upper()]
            except KeyError:
                LOGGER.warning("Server %s: unknown role tier %r in config, ignoring.", self.name, name)
                continue
            out[role] = {int(v) for v in (value if isinstance(value, list) else [value])}
        return out

    def get(self, key: str, default: Any = _MISSING) -> Any:
        return _dotted(self.model_dump(by_alias=True), key, default, f"server {self.name}")


class MainConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    bot: BotConfig
    commloop: CommloopConfig
    web: WebConfig = Field(default_factory=WebConfig)


class ConfigError(Exception):
    pass


def _dotted(data: Any, key: str, default: Any, where: str) -> Any:
    current = data
    for node in key.split("."):
        if not isinstance(current, dict) or node not in current:
            if default is _MISSING:
                raise ConfigError(f"Missing config key {key!r} for {where}, and no default was given.")
            return default
        current = current[node]
    return current


class Config:

    def __init__(self, main: MainConfig, servers: list[ServerConfig], modules: dict[str, Any]) -> None:
        self.main = main
        self.servers = servers
        self.modules = modules
        self._by_id = {s.id: s for s in servers}
        self._by_name: dict[str, ServerConfig] = {}
        for server in servers:
            if server.name in self._by_name:
                raise ConfigError(
                    f"Duplicate server name {server.name!r} in servers.toml "
                    f"(ids {self._by_name[server.name].id} and {server.id})."
                )
            self._by_name[server.name] = server

    @classmethod
    def load(cls, directory: Path) -> Config:
        main_raw = _read_toml(directory / "main.toml")
        servers_raw = _read_toml(directory / "servers.toml")
        modules_raw = _read_toml(directory / "modules.toml")

        try:
            main = MainConfig.model_validate(main_raw)
        except ValidationError as e:
            raise ConfigError(f"main.toml is invalid:\n{e}") from e

        entries = servers_raw.get("servers", [])
        if not isinstance(entries, list):
            raise ConfigError("servers.toml: `servers` must be an array of tables ([[servers]]).")

        servers = []
        for i, entry in enumerate(entries):
            try:
                servers.append(ServerConfig.model_validate(entry))
            except ValidationError as e:
                raise ConfigError(f"servers.toml: entry #{i} is invalid:\n{e}") from e

        config = cls(main, servers, modules_raw)
        config._check_routes()
        return config

    def _check_routes(self) -> None:
        for msg_type, metas in self.main.commloop.route.items():
            if not isinstance(metas, dict):
                LOGGER.error("commloop.route.%s must be a table of meta -> targets.", msg_type)
                continue
            for meta, targets in metas.items():
                if not isinstance(targets, list) or not all(
                    isinstance(t, list) and len(t) == 2 for t in targets
                ):
                    LOGGER.error(
                        "commloop.route.%s.%s is malformed: expected [[server, channel], ...], got %r. "
                        "Messages for this route will be dropped.",
                        msg_type,
                        meta,
                        targets,
                    )

    def server_by_id(self, snowflake: int) -> ServerConfig | None:
        return self._by_id.get(snowflake)

    def server_by_name(self, name: str) -> ServerConfig | None:
        return self._by_name.get(name)

    def module(self, key: str, default: Any = _MISSING) -> Any:
        return _dotted(self.modules, key, default, "modules.toml")

    def routes_for(self, msg_type: str, meta: str) -> list[tuple[int | str, int | str]]:
        targets = self.main.commloop.route.get(msg_type, {})
        if not isinstance(targets, dict):
            return []
        raw = targets.get(meta)
        if not isinstance(raw, list):
            return []
        out = []
        for pair in raw:
            if isinstance(pair, list) and len(pair) == 2:
                out.append((pair[0], pair[1]))
        return out


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Missing config file: {path}")
    try:
        with path.open("rb") as f:
            return cast(dict[str, Any], tomllib.load(f))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} is not valid TOML: {e}") from e
