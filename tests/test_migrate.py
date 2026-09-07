from __future__ import annotations

import functools
import pickle
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mommi.config import Config
from mommi.migrate import convert_rocket_toml, migrate_storage, normalise_markov
from mommi.storage import Storage

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "example"
GUILD = 100000000000000001
FUTURE = datetime(2030, 1, 1, tzinfo=timezone.utc)


def build_v2_data(root: Path) -> None:
    guild = root / "vgstation"
    guild.mkdir(parents=True)


    partial = functools.partial(defaultdict, int)
    chain: defaultdict = defaultdict(partial)
    chain[""]["the"] = 4
    chain["the"]["quick"] = 2
    chain["quick"][""] = 1
    (guild / "markov").write_bytes(pickle.dumps(chain))

    (guild / "resp").write_bytes(pickle.dumps({"hi": "hello there", "bye": "cya"}))

    global_dir = root / "__global__"
    global_dir.mkdir()


    reminders = [
        (FUTURE, "do the thing", GUILD, 111, 222, 7),
        (FUTURE + timedelta(days=1), "later thing", GUILD, 111, 333, 8),

        (FUTURE + timedelta(days=2), "ancient", GUILD, 111, 444),
    ]
    (global_dir / "reminder_queue").write_bytes(pickle.dumps(reminders))
    (global_dir / "reminder_uid").write_bytes(pickle.dumps(9))


    mirrors = [(FUTURE, "held back", "bob", "http://av", "http://hook", 999)]
    (global_dir / "mirror_queue").write_bytes(pickle.dumps(mirrors))

    nudges = [(FUTURE, "round ended", GUILD, 111)]
    (global_dir / "nudge_mirror_queue").write_bytes(pickle.dumps(nudges))


@pytest.fixture
async def migrated(tmp_path: Path):
    old = tmp_path / "olddata"
    build_v2_data(old)
    storage = Storage(tmp_path / "new.sqlite3")
    await storage.open()
    await migrate_storage(old, Config.load(EXAMPLE), storage, dry_run=False)
    yield storage
    await storage.close()


def test_normalise_markov_flattens_defaultdicts():
    partial = functools.partial(defaultdict, int)
    chain: defaultdict = defaultdict(partial)
    chain["a"]["b"] = 2
    result = normalise_markov(chain)
    assert result == {"a": {"b": 2}}
    assert type(result) is dict
    assert type(result["a"]) is dict


async def test_markov_survives(migrated):
    chain = await migrated.get(GUILD, "markov")
    assert chain["the"]["quick"] == 2
    assert chain[""]["the"] == 4


async def test_responses_survive(migrated):
    assert await migrated.get(GUILD, "resp") == {"hi": "hello there", "bye": "cya"}


async def test_reminders_survive_including_old_five_tuples(migrated):
    due = await migrated.pop_due_reminders(FUTURE.timestamp() + 86400 * 5)
    messages = sorted(r.message for r in due)
    assert messages == ["ancient", "do the thing", "later thing"]


async def test_reminder_fields_are_preserved(migrated):
    due = await migrated.pop_due_reminders(FUTURE.timestamp() + 1)
    assert len(due) == 1
    reminder = due[0]
    assert reminder.message == "do the thing"
    assert reminder.guild_id == GUILD
    assert reminder.channel_id == 111
    assert reminder.user_id == 222


async def test_pending_mirrors_survive(migrated):
    entries = await migrated.pop_due_mirrors(FUTURE.timestamp() + 1)
    assert len(entries) == 1
    assert entries[0].content == "held back"
    assert entries[0].source_message_id == 999


async def test_pending_nudges_survive(migrated):
    entries = await migrated.pop_due_nudges(FUTURE.timestamp() + 1)
    assert [e.content for e in entries] == ["round ended"]


async def test_dry_run_writes_nothing(tmp_path: Path):
    old = tmp_path / "olddata"
    build_v2_data(old)
    storage = Storage(tmp_path / "dry.sqlite3")
    await storage.open()
    await migrate_storage(old, Config.load(EXAMPLE), storage, dry_run=True)
    assert await storage.get(GUILD, "markov") is None
    assert await storage.pop_due_reminders(1e12) == []
    await storage.close()


async def test_unknown_guild_directory_is_skipped(tmp_path: Path, caplog):
    old = tmp_path / "olddata"
    build_v2_data(old)
    (old / "someotherserver").mkdir()
    (old / "someotherserver" / "resp").write_bytes(pickle.dumps({"a": "b"}))

    storage = Storage(tmp_path / "skip.sqlite3")
    await storage.open()
    await migrate_storage(old, Config.load(EXAMPLE), storage, dry_run=False)

    assert await storage.get(GUILD, "resp") is not None
    await storage.close()


async def test_corrupt_jar_does_not_abort_the_run(tmp_path: Path):
    old = tmp_path / "olddata"
    build_v2_data(old)
    (old / "vgstation" / "broken").write_bytes(b"not a pickle at all")

    storage = Storage(tmp_path / "corrupt.sqlite3")
    await storage.open()
    await migrate_storage(old, Config.load(EXAMPLE), storage, dry_run=False)
    assert await storage.get(GUILD, "resp") == {"hi": "hello there", "bye": "cya"}
    await storage.close()


ROCKET = """
[development]
address = "0.0.0.0"
port = 40000
commloop-address = "127.0.0.1:1679"
commloop-password = "secret"
github-key = "ghsecret"
changelog-delay = 5
repo-path = "toomykins/vgstation13"

[production]
commloop-address = "127.0.0.1:1679"
changelog-delay = 30
"""


def test_rocket_conversion(tmp_path: Path):
    path = tmp_path / "Rocket.toml"
    path.write_text(ROCKET)
    output = convert_rocket_toml(path)

    assert "[web]" in output
    assert 'address = "0.0.0.0"' in output
    assert "port = 40000" in output
    assert 'github-key = "ghsecret"' in output

    assert "delay = 30" in output
    assert "[web.changelog]" in output

    assert "no longer needed" in output


def test_rocket_conversion_output_is_valid_toml(tmp_path: Path):
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

    path = tmp_path / "Rocket.toml"
    path.write_text(ROCKET)
    parsed = tomllib.loads(convert_rocket_toml(path))
    assert parsed["web"]["port"] == 40000
    assert parsed["web"]["changelog"]["delay"] == 30
