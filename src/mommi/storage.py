from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

LOGGER = logging.getLogger(__name__)


GLOBAL_SCOPE = "__global__"

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    scope TEXT NOT NULL,
    key   TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (scope, key)
);

CREATE TABLE IF NOT EXISTS reminders (
    uid        INTEGER PRIMARY KEY AUTOINCREMENT,
    due        REAL    NOT NULL,
    message    TEXT    NOT NULL,
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    created    REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS reminders_due ON reminders (due);
CREATE INDEX IF NOT EXISTS reminders_user ON reminders (guild_id, user_id);

-- Messages held back for the delayed webhook mirror.
CREATE TABLE IF NOT EXISTS mirror_queue (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    due               REAL    NOT NULL,
    content           TEXT    NOT NULL,
    sender            TEXT    NOT NULL,
    avatar            TEXT    NOT NULL,
    target            TEXT    NOT NULL,
    source_message_id INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS mirror_due ON mirror_queue (due);
CREATE INDEX IF NOT EXISTS mirror_source ON mirror_queue (source_message_id);

-- Game nudges echoed into a second channel after a delay.
CREATE TABLE IF NOT EXISTS nudge_queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    due        REAL    NOT NULL,
    content    TEXT    NOT NULL,
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS nudge_due ON nudge_queue (due);

-- Player-count history, sampled by the playerstats poller.
CREATE TABLE IF NOT EXISTS player_counts (
    guild_id   INTEGER NOT NULL,
    server_key TEXT    NOT NULL,
    ts         REAL    NOT NULL,
    players    INTEGER NOT NULL,
    PRIMARY KEY (guild_id, server_key, ts)
);
CREATE INDEX IF NOT EXISTS player_counts_lookup
    ON player_counts (guild_id, server_key, ts);
"""


@dataclass(frozen=True)
class Reminder:
    uid: int
    due: float
    message: str
    guild_id: int
    channel_id: int
    user_id: int


@dataclass(frozen=True)
class MirrorEntry:
    id: int
    due: float
    content: str
    sender: str
    avatar: str
    target: str
    source_message_id: int


@dataclass(frozen=True)
class Sample:
    ts: float
    players: int


@dataclass(frozen=True)
class NudgeEntry:
    id: int
    due: float
    content: str
    guild_id: int
    channel_id: int


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None


        self._has_returning = sqlite3.sqlite_version_info >= (3, 35, 0)

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Storage has not been opened.")
        return self._db

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row

        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        LOGGER.info("Opened storage at %s (SQLite %s)", self.path, sqlite3.sqlite_version)
        if not self._has_returning:
            LOGGER.warning(
                "SQLite %s predates DELETE ... RETURNING; using the slower fallback. "
                "Upgrade to 3.35+ when convenient.",
                sqlite3.sqlite_version,
            )

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def _claim_due(self, table: str, columns: str, now: float) -> list[dict[str, Any]]:
        if self._has_returning:
            async with self.db.execute(
                f"DELETE FROM {table} WHERE due <= ? RETURNING {columns}", (now,)
            ) as cursor:
                returned = await cursor.fetchall()
            await self.db.commit()
            return [dict(row) for row in returned]

        await self.db.execute("BEGIN IMMEDIATE")
        try:
            async with self.db.execute(
                f"SELECT {columns} FROM {table} WHERE due <= ?", (now,)
            ) as cursor:
                rows = [dict(row) for row in await cursor.fetchall()]
            if rows:
                await self.db.execute(f"DELETE FROM {table} WHERE due <= ?", (now,))
        except Exception:
            await self.db.rollback()
            raise
        await self.db.commit()
        return rows


    async def get(self, scope: str | int, key: str, default: Any = None) -> Any:
        async with self.db.execute(
            "SELECT value FROM kv WHERE scope = ? AND key = ?", (str(scope), key)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            LOGGER.exception("Corrupt JSON in kv for scope=%s key=%s; returning default.", scope, key)
            return default

    async def set(self, scope: str | int, key: str, value: Any) -> None:
        await self.db.execute(
            "INSERT INTO kv (scope, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT (scope, key) DO UPDATE SET value = excluded.value",
            (str(scope), key, json.dumps(value)),
        )
        await self.db.commit()

    async def delete(self, scope: str | int, key: str) -> None:
        await self.db.execute("DELETE FROM kv WHERE scope = ? AND key = ?", (str(scope), key))
        await self.db.commit()


    async def add_reminder(
        self, due: float, message: str, guild_id: int, channel_id: int, user_id: int, created: float
    ) -> int:
        cursor = await self.db.execute(
            "INSERT INTO reminders (due, message, guild_id, channel_id, user_id, created) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (due, message, guild_id, channel_id, user_id, created),
        )
        await self.db.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def pop_due_reminders(self, now: float) -> list[Reminder]:
        rows = await self._claim_due(
            "reminders", "uid, due, message, guild_id, channel_id, user_id", now
        )
        return [Reminder(**row) for row in rows]

    async def list_reminders(self, guild_id: int, user_id: int) -> list[Reminder]:
        async with self.db.execute(
            "SELECT uid, due, message, guild_id, channel_id, user_id FROM reminders "
            "WHERE guild_id = ? AND user_id = ? ORDER BY due",
            (guild_id, user_id),
        ) as cursor:
            rows = await cursor.fetchall()
        return [Reminder(**dict(row)) for row in rows]

    async def get_reminder(self, uid: int) -> Reminder | None:
        async with self.db.execute(
            "SELECT uid, due, message, guild_id, channel_id, user_id FROM reminders WHERE uid = ?",
            (uid,),
        ) as cursor:
            row = await cursor.fetchone()
        return Reminder(**dict(row)) if row else None

    async def delete_reminder(self, uid: int) -> bool:
        cursor = await self.db.execute("DELETE FROM reminders WHERE uid = ?", (uid,))
        await self.db.commit()
        return cursor.rowcount > 0


    async def add_mirror(
        self, due: float, content: str, sender: str, avatar: str, target: str, source_message_id: int
    ) -> int:
        cursor = await self.db.execute(
            "INSERT INTO mirror_queue (due, content, sender, avatar, target, source_message_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (due, content, sender, avatar, target, source_message_id),
        )
        await self.db.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def pop_due_mirrors(self, now: float) -> list[MirrorEntry]:
        rows = await self._claim_due(
            "mirror_queue", "id, due, content, sender, avatar, target, source_message_id", now
        )
        return [MirrorEntry(**row) for row in rows]

    async def cancel_mirror(self, source_message_id: int) -> bool:
        cursor = await self.db.execute(
            "DELETE FROM mirror_queue WHERE source_message_id = ?", (source_message_id,)
        )
        await self.db.commit()
        return cursor.rowcount > 0


    async def add_nudge(self, due: float, content: str, guild_id: int, channel_id: int) -> int:
        cursor = await self.db.execute(
            "INSERT INTO nudge_queue (due, content, guild_id, channel_id) VALUES (?, ?, ?, ?)",
            (due, content, guild_id, channel_id),
        )
        await self.db.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def pop_due_nudges(self, now: float) -> list[NudgeEntry]:
        rows = await self._claim_due("nudge_queue", "id, due, content, guild_id, channel_id", now)
        return [NudgeEntry(**row) for row in rows]


    async def record_players(
        self, guild_id: int, server_key: str, ts: float, players: int
    ) -> None:
        await self.db.execute(
            "INSERT INTO player_counts (guild_id, server_key, ts, players) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (guild_id, server_key, ts) DO UPDATE SET players = excluded.players",
            (guild_id, server_key, ts, players),
        )
        await self.db.commit()

    async def player_history(
        self, guild_id: int, server_key: str, since: float
    ) -> list[Sample]:
        async with self.db.execute(
            "SELECT ts, players FROM player_counts "
            "WHERE guild_id = ? AND server_key = ? AND ts >= ? ORDER BY ts",
            (guild_id, server_key, since),
        ) as cursor:
            rows = await cursor.fetchall()
        return [Sample(ts=row["ts"], players=row["players"]) for row in rows]

    async def player_keys(self, guild_id: int) -> list[str]:
        async with self.db.execute(
            "SELECT DISTINCT server_key FROM player_counts WHERE guild_id = ? ORDER BY server_key",
            (guild_id,),
        ) as cursor:
            return [row["server_key"] for row in await cursor.fetchall()]

    async def prune_player_history(self, before: float) -> int:
        cursor = await self.db.execute("DELETE FROM player_counts WHERE ts < ?", (before,))
        await self.db.commit()
        return cursor.rowcount
