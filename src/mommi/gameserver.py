from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from mommi.byond import ByondError, topic

LOGGER = logging.getLogger(__name__)

TIMEOUT = 5.0
PLAYER_KEY_RE = re.compile(r"^player(\d+)$")


@dataclass
class Snapshot:

    key: str
    kind: str
    online: bool = False
    players: int = 0

    player_names: list[str] = field(default_factory=list)

    names_available: bool = False
    map_name: str | None = None
    station_time: str | None = None
    round_duration: str | None = None
    gamestate: str | None = None
    mode: str | None = None
    admins: int | None = None
    afk_admins: int | None = None
    server_name: str | None = None
    error: str | None = None


def plural(count: int, singular: str, suffix: str = "s") -> str:
    return f"{count} {singular}" if count == 1 else f"{count} {singular}{suffix}"


GAMESTATES = {
    "1": "lobby",
    "2": "setting up",
    "3": "in progress",
    "4": "finished",
}


def parse_ss13_status(response: dict[str, list[str]]) -> Snapshot:

    def first(key: str) -> str | None:
        values = response.get(key)
        return values[0] if values else None

    snapshot = Snapshot(key="", kind="ss13", online=True, names_available=True)

    raw_players = first("players")
    if raw_players is None:
        raise ValueError("status response had no player count")
    snapshot.players = int(raw_players)


    numbered: list[tuple[int, str]] = []
    for key, values in response.items():
        match = PLAYER_KEY_RE.match(key)
        if match and values:
            numbered.append((int(match.group(1)), values[0]))
    snapshot.player_names = [name for _, name in sorted(numbered)]

    snapshot.map_name = first("map_name")
    snapshot.station_time = first("station_time")
    snapshot.mode = first("mode")
    snapshot.server_name = first("version")
    state = first("gamestate")
    snapshot.gamestate = GAMESTATES.get(state or "", state)

    for name, attr in (("admins", "admins"), ("afk_admins", "afk_admins")):
        raw = first(name)
        if raw is not None:
            try:
                setattr(snapshot, attr, int(raw))
            except ValueError:
                pass

    return snapshot


async def _get_json(url: str) -> Any:
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.json(content_type=None)


async def query(key: str, config: dict[str, Any]) -> Snapshot:
    kind = str(config.get("type", "ss13"))
    try:
        if kind == "ss13":
            response = await topic(
                str(config["address"]), int(config["port"]), b"?status", timeout=TIMEOUT
            )
            if not isinstance(response, dict):
                return Snapshot(key=key, kind=kind, error="server sent a number, not a status")
            snapshot = parse_ss13_status(response)
            snapshot.key = key
            return snapshot

        if kind == "ss14":
            data = await _get_json(str(config["url"]).rstrip("/") + "/status")
            return Snapshot(
                key=key,
                kind=kind,
                online=True,
                players=int(data.get("players", 0)),
                server_name=data.get("name"),
                map_name=data.get("map"),
                gamestate=str(data.get("run_level", "")) or None,
                round_duration=data.get("round_start_time"),

                names_available=False,
            )

        if kind == "bluespess":
            data = await _get_json(str(config["url"]))
            return Snapshot(
                key=key, kind=kind, online=True, players=int(data.get("player_count", 0))
            )

        return Snapshot(key=key, kind=kind, error=f"unknown server type {kind!r}")

    except asyncio.TimeoutError:
        return Snapshot(key=key, kind=kind, error="timed out")
    except (KeyError, ValueError, TypeError) as e:
        return Snapshot(key=key, kind=kind, error=f"bad config or response: {e}")
    except (ByondError, OSError, aiohttp.ClientError) as e:
        return Snapshot(key=key, kind=kind, error=str(e) or "unreachable")


def configured_servers(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, dict)}
