from __future__ import annotations

from pathlib import Path

import pytest

from mommi.storage import Storage

GUILD = 100000000000000001


@pytest.fixture
async def store(tmp_path: Path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.open()
    yield storage
    await storage.close()


async def test_kv_round_trip(store):
    await store.set(GUILD, "resp", {"hi": "hello"})
    assert await store.get(GUILD, "resp") == {"hi": "hello"}


async def test_kv_default_when_absent(store):
    assert await store.get(GUILD, "nope", {"a": 1}) == {"a": 1}


async def test_kv_scopes_are_isolated(store):
    await store.set(GUILD, "resp", {"a": 1})
    await store.set(999, "resp", {"b": 2})
    assert await store.get(GUILD, "resp") == {"a": 1}
    assert await store.get(999, "resp") == {"b": 2}


async def test_kv_overwrites(store):
    await store.set(GUILD, "k", 1)
    await store.set(GUILD, "k", 2)
    assert await store.get(GUILD, "k") == 2


async def test_reminders_pop_only_what_is_due(store):
    early = await store.add_reminder(100.0, "early", GUILD, 1, 2, 0.0)
    await store.add_reminder(500.0, "late", GUILD, 1, 2, 0.0)

    due = await store.pop_due_reminders(200.0)
    assert [r.uid for r in due] == [early]
    assert [r.message for r in due] == ["early"]


    assert await store.pop_due_reminders(200.0) == []
    assert len(await store.pop_due_reminders(1000.0)) == 1


async def test_reminder_uids_are_unique_and_stable(store):
    a = await store.add_reminder(1.0, "a", GUILD, 1, 2, 0.0)
    b = await store.add_reminder(2.0, "b", GUILD, 1, 2, 0.0)
    assert a != b
    assert (await store.get_reminder(a)).message == "a"


async def test_delete_reminder(store):
    uid = await store.add_reminder(500.0, "x", GUILD, 1, 2, 0.0)
    assert await store.delete_reminder(uid) is True
    assert await store.get_reminder(uid) is None
    assert await store.delete_reminder(uid) is False


async def test_list_reminders_is_scoped_to_guild_and_user(store):
    await store.add_reminder(1.0, "mine", GUILD, 1, 42, 0.0)
    await store.add_reminder(2.0, "theirs", GUILD, 1, 99, 0.0)
    await store.add_reminder(3.0, "elsewhere", 111, 1, 42, 0.0)

    mine = await store.list_reminders(GUILD, 42)
    assert [r.message for r in mine] == ["mine"]


async def test_mirror_cancel_retracts_pending(store):
    await store.add_mirror(500.0, "secret", "bob", "http://a", "http://hook", 12345)
    assert await store.cancel_mirror(12345) is True
    assert await store.pop_due_mirrors(1000.0) == []


async def test_mirror_cancel_reports_when_nothing_held(store):
    assert await store.cancel_mirror(404) is False


async def test_player_history_round_trip(store):
    for ts, count in [(100.0, 5), (200.0, 8), (300.0, 3)]:
        await store.record_players(GUILD, "vg", ts, count)

    history = await store.player_history(GUILD, "vg", 0.0)
    assert [(s.ts, s.players) for s in history] == [(100.0, 5), (200.0, 8), (300.0, 3)]

    recent = await store.player_history(GUILD, "vg", 150.0)
    assert [s.players for s in recent] == [8, 3]


async def test_player_history_is_ordered_by_time(store):
    for ts in (300.0, 100.0, 200.0):
        await store.record_players(GUILD, "vg", ts, int(ts))
    history = await store.player_history(GUILD, "vg", 0.0)
    assert [s.ts for s in history] == [100.0, 200.0, 300.0]


async def test_prune_player_history(store):
    await store.record_players(GUILD, "vg", 100.0, 5)
    await store.record_players(GUILD, "vg", 900.0, 7)
    removed = await store.prune_player_history(500.0)
    assert removed == 1
    assert [s.players for s in await store.player_history(GUILD, "vg", 0.0)] == [7]


async def test_player_keys(store):
    await store.record_players(GUILD, "vg", 1.0, 1)
    await store.record_players(GUILD, "vg2", 1.0, 1)
    assert await store.player_keys(GUILD) == ["vg", "vg2"]


async def test_survives_reopen(tmp_path: Path):
    path = tmp_path / "persist.sqlite3"
    first = Storage(path)
    await first.open()
    await first.set(GUILD, "markov", {"": {"hello": 3}})
    uid = await first.add_reminder(999.0, "later", GUILD, 1, 2, 0.0)
    await first.close()

    second = Storage(path)
    await second.open()
    assert await second.get(GUILD, "markov") == {"": {"hello": 3}}
    assert (await second.get_reminder(uid)).message == "later"
    await second.close()
