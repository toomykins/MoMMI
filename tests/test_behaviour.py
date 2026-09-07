from __future__ import annotations

import random
from datetime import timedelta
from pathlib import Path

import pytest

from mommi.cogs.chance import MAX_DICE, format_roll, pick_from, roll_dice
from mommi.cogs.fun import BASED_RE, WYCI_RE, based_reply
from mommi.cogs.markov import generate, learn, sentences
from mommi.cogs.playerstats import parse_period
from mommi.config import Config, ConfigError, RoleType


def test_roll_is_in_range():
    rolls, total = roll_dice(10, 6)
    assert len(rolls) == 10
    assert all(1 <= r <= 6 for r in rolls)
    assert total == sum(rolls)


def test_roll_modifier_is_added_once():
    rolls, total = roll_dice(3, 6, 5)
    assert total == sum(rolls) + 5


def test_format_roll_omits_a_zero_modifier():
    assert format_roll([1, 2], 3, 0) == "Results: 1, 2 = 3"
    assert format_roll([1, 2], 8, 5) == "Results: 1, 2 + 5 = 8"


def test_pick_needs_two_options():
    assert pick_from("only one") is None
    assert pick_from("") is None
    assert pick_from("a, b") in {"a", "b"}


def test_pick_ignores_empty_entries():
    assert pick_from("a, , b") in {"a", "b"}


def test_dice_cap_exists():
    assert MAX_DICE == 100


@pytest.mark.parametrize("text", ["based", "Based.", "  based!!", "gebaseerd", "basiert", "ベース"])
def test_based_matches(text):
    assert BASED_RE.search(text) is not None


@pytest.mark.parametrize("text", ["based on what", "unbased", "it was based on x"])
def test_based_does_not_match_mid_sentence(text):
    assert BASED_RE.search(text) is None


def test_based_replies_in_the_right_language():
    random.seed(1)
    assert based_reply("gebaseerd") in {"Gebaseerd op wat?", "Niet Gebaseerd."}
    assert based_reply("based") in {"Based on what?", "Not Based."}


@pytest.mark.parametrize("text", ["it'll be done when", "soon? when", "ports when!"])
def test_wyci_matches(text):
    assert WYCI_RE.search(text) is not None


@pytest.mark.parametrize("text", ["when will it be done", "when", ""])
def test_wyci_needs_a_preceding_word(text):
    assert WYCI_RE.search(text) is None


def test_sentences_split_on_punctuation():
    assert sentences("one thing. two thing? three") == ["one thing", "two thing", "three"]


def test_sentences_ignores_mention_bang():
    assert sentences("hello <@!123> there") == ["hello <@!123> there"]


def test_learn_ignores_short_sentences():
    chain = {}
    learn(chain, "too short")
    assert chain == {}


def test_learn_builds_a_chain():
    chain = {}
    learn(chain, "the quick brown fox jumps over the lazy dog")
    assert chain[""]["the"] == 1
    assert chain["quick"]["brown"] == 1
    assert chain["dog"][""] == 1


def test_learn_accumulates_counts():
    chain = {}
    for _ in range(3):
        learn(chain, "the quick brown fox jumps over the lazy dog")
    assert chain["quick"]["brown"] == 3


def test_generate_returns_none_for_unknown_seed():
    assert generate({"": {"a": 1}}, "nothere") is None


def test_generate_terminates():
    chain = {}
    learn(chain, "the quick brown fox jumps over the lazy dog")
    words = generate(chain, "the")
    assert words is not None and words[0] == "the"
    assert len(words) <= 101


def test_generate_never_loops_forever_on_a_cycle():
    chain = {"a": {"a": 1}}
    words = generate(chain, "a")
    assert words is not None and len(words) <= 101


@pytest.mark.parametrize(
    "raw,expected",
    [("24h", timedelta(hours=24)), ("7d", timedelta(days=7)), ("2w", timedelta(weeks=2))],
)
def test_parse_period(raw, expected):
    assert parse_period(raw) == expected


def test_parse_period_defaults_to_a_day():
    assert parse_period("") == timedelta(days=1)
    assert parse_period(None) == timedelta(days=1)


@pytest.mark.parametrize("raw", ["banana", "0d", "-5h", "9999w", "5"])
def test_parse_period_rejects_nonsense(raw):
    assert parse_period(raw) is None


EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "example"


def test_example_config_loads():
    config = Config.load(EXAMPLE)
    assert len(config.servers) == 1
    assert config.servers[0].name == "vgstation"


def test_server_lookup_by_name_and_id():
    config = Config.load(EXAMPLE)
    assert config.server_by_name("vgstation") is not None
    assert config.server_by_id(100000000000000001) is not None
    assert config.server_by_name("nope") is None


def test_dotted_server_config_lookup():
    config = Config.load(EXAMPLE)
    server = config.servers[0]
    assert server.get("modules.serverstatus.default") == "vg"
    assert server.get("modules.serverstatus.vg.address") == "game.ss13.moe"
    assert server.get("modules.nope.nope", "fallback") == "fallback"


def test_missing_key_without_default_raises():
    config = Config.load(EXAMPLE)
    with pytest.raises(ConfigError):
        config.servers[0].get("modules.definitely.not.here")


def test_roles_resolve_to_snowflake_sets():
    config = Config.load(EXAMPLE)
    roles = config.servers[0].resolved_roles()
    assert roles[RoleType.OWNER] == {100000000000000006}

    assert roles[RoleType.ADMIN] == {100000000000000001}


def test_commloop_routes_resolve():
    config = Config.load(EXAMPLE)
    assert config.routes_for("gamenudge", "ick") == [("vgstation", "ick")]
    assert config.routes_for("gamenudge", "nosuchmeta") == []
    assert config.routes_for("nosuchtype", "x") == []


def test_missing_config_file_is_a_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="Missing config file"):
        Config.load(tmp_path)


def test_broken_toml_is_a_clear_error(tmp_path):
    for name in ("main.toml", "servers.toml", "modules.toml"):
        (tmp_path / name).write_text("this is [not valid toml")
    with pytest.raises(ConfigError, match="not valid TOML"):
        Config.load(tmp_path)


def test_chart_renders_a_png():
    pytest.importorskip("matplotlib")
    from mommi.charts import render_player_chart

    series = {"vg": [(1_700_000_000.0 + i * 300, 20 + i % 7) for i in range(200)]}
    png = render_player_chart(series, "test")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 5000


def test_chart_with_multiple_series():
    pytest.importorskip("matplotlib")
    from mommi.charts import render_player_chart

    series = {
        "vg": [(1_700_000_000.0 + i * 300, 20) for i in range(50)],
        "vg2": [(1_700_000_000.0 + i * 300, 5) for i in range(50)],
    }
    assert render_player_chart(series, "test")[:4] == b"\x89PNG"


def test_chart_refuses_empty_data():
    pytest.importorskip("matplotlib")
    from mommi.charts import render_player_chart

    with pytest.raises(ValueError):
        render_player_chart({}, "test")


def test_status_line_for_a_live_ss13_server():
    from mommi.cogs.serverstatus import format_status
    from mommi.gameserver import Snapshot

    snapshot = Snapshot(
        key="vg", kind="ss13", online=True, players=42,
        map_name="Box Station", station_time="19:49", admins=2, afk_admins=1,
    )
    assert format_status(snapshot, False) == (
        "42 players online, map is Box Station, station time: 19:49."
    )
    assert "2** active admins" in format_status(snapshot, True)
    assert "1** AFK admin online" in format_status(snapshot, True)


def test_status_line_for_a_dead_server():
    from mommi.cogs.serverstatus import format_status
    from mommi.gameserver import Snapshot

    result = format_status(Snapshot(key="vg", kind="ss13", error="timed out"), False)
    assert "timed out" in result


def test_configured_servers_ignores_scalar_settings():
    from mommi.gameserver import configured_servers

    raw = {"default": "vg", "vg": {"type": "ss13"}, "vg2": {"type": "ss14"}}
    assert sorted(configured_servers(raw)) == ["vg", "vg2"]
    assert configured_servers("not a dict") == {}


def test_plural():
    from mommi.cogs.playerstats import plural

    assert plural(0, "player") == "0 players"
    assert plural(1, "player") == "1 player"
    assert plural(2, "player") == "2 players"


def test_name_columns_read_top_to_bottom():
    from mommi.cogs.playerstats import name_columns

    names = [f"n{i}" for i in range(9)]
    assert name_columns(names, 3) == ["n0\nn1\nn2", "n3\nn4\nn5", "n6\nn7\nn8"]


def test_name_columns_handles_uneven_splits():
    from mommi.cogs.playerstats import name_columns

    blocks = name_columns([f"n{i}" for i in range(10)], 3)
    assert len(blocks) <= 3
    assert sum(b.count("\n") + 1 for b in blocks) == 10


def test_name_columns_with_fewer_names_than_columns():
    from mommi.cogs.playerstats import name_columns

    assert name_columns(["a", "b"], 3) == ["a", "b"]


def test_name_columns_empty():
    from mommi.cogs.playerstats import name_columns

    assert name_columns([], 3) == []


def test_name_columns_never_exceeds_the_field_limit():
    from mommi.cogs.playerstats import MAX_FIELD, name_columns

    blocks = name_columns(["x" * 60 for _ in range(120)], 3)
    assert all(len(b) <= MAX_FIELD for b in blocks)


_MONDAY_MIDNIGHT_UTC = 1704067200.0


def _sample(day: int, hour: int, count: int, minute: int = 0):
    return (_MONDAY_MIDNIGHT_UTC + day * 86400 + hour * 3600 + minute * 60, count)


def test_summarise_returns_none_without_data():
    from mommi.cogs.playerstats import summarise

    assert summarise([]) is None


def test_summarise_finds_the_peak_and_when_it_happened():
    from mommi.cogs.playerstats import summarise

    stats = summarise([_sample(0, 3, 5), _sample(2, 20, 41), _sample(1, 9, 12)])
    assert stats.peak == 41
    assert stats.peak_at == _sample(2, 20, 0)[0]


def test_summarise_mean():
    from mommi.cogs.playerstats import summarise

    stats = summarise([_sample(0, 1, 10), _sample(0, 2, 20), _sample(0, 3, 30)])
    assert stats.mean == 20.0


def test_busiest_hour_is_by_average_not_by_peak():
    from mommi.cogs.playerstats import summarise

    samples = [_sample(0, 4, 100, 0)]
    samples += [_sample(0, 4, 1, m) for m in range(5, 100, 5)]
    samples += [_sample(d, 20, 30, m) for d in range(3) for m in (0, 5, 10)]
    stats = summarise(samples)
    assert stats.busiest_hour == 20
    assert stats.by_hour[20] == 30.0

    assert stats.peak == 100


def test_busiest_weekday():
    from mommi.cogs.playerstats import summarise

    samples = [_sample(0, 12, 5, m) for m in (0, 5, 10)]
    samples += [_sample(5, 12, 40, m) for m in (0, 5, 10)]
    stats = summarise(samples)
    assert stats.busiest_weekday == 5


def test_weekday_index_maps_to_the_right_name():
    from mommi.cogs.playerstats import WEEKDAYS, summarise

    stats = summarise([_sample(5, 12, 9, m) for m in (0, 5, 10)])
    assert WEEKDAYS[stats.busiest_weekday] == "Saturday"


def test_quietest_hour():
    from mommi.cogs.playerstats import summarise

    samples = [_sample(d, 8, 1, m) for d in range(3) for m in (0, 5, 10)]
    samples += [_sample(d, 20, 30, m) for d in range(3) for m in (0, 5, 10)]
    stats = summarise(samples)
    assert stats.quietest_hour == 8


def test_thin_buckets_are_excluded():
    from mommi.cogs.playerstats import MIN_SAMPLES_PER_BUCKET, summarise

    samples = [_sample(0, 4, 999)]
    samples += [_sample(d, 20, 10, m) for d in range(3) for m in (0, 5, 10)]
    stats = summarise(samples)
    assert MIN_SAMPLES_PER_BUCKET > 1
    assert 4 not in stats.by_hour
    assert stats.busiest_hour == 20

    assert stats.peak == 999


def test_hours_are_bucketed_in_utc():
    from mommi.cogs.playerstats import summarise

    stats = summarise([_sample(0, 13, 7, m) for m in (0, 5, 10)])
    assert list(stats.by_hour) == [13]


def test_split_args_accepts_either_order():
    from datetime import timedelta

    from mommi.cogs.playerstats import _split_args

    default = timedelta(days=30)
    assert _split_args("vg", "7d", default) == ("vg", timedelta(days=7))
    assert _split_args("7d", "", default) == ("", timedelta(days=7))
    assert _split_args("vg", "", default) == ("vg", default)
    assert _split_args("", "", default) == ("", default)
    assert _split_args("vg", "banana", default) == ("vg", None)


def test_hourly_chart_renders():
    import pytest as _pytest

    _pytest.importorskip("matplotlib")
    from mommi.charts import render_hourly_chart

    png = render_hourly_chart({h: float(h) for h in range(24)}, "test")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_hourly_chart_refuses_empty():
    import pytest as _pytest

    _pytest.importorskip("matplotlib")
    from mommi.charts import ChartUnavailable, render_hourly_chart  # noqa: F401

    with _pytest.raises(ValueError):
        render_hourly_chart({}, "test")


def test_plural_is_shared_and_used_by_status():
    from mommi.cogs.serverstatus import format_status
    from mommi.gameserver import Snapshot, plural

    assert plural(1, "player") == "1 player"
    one = Snapshot(key="vg", kind="ss13", online=True, players=1, map_name="Box Station")
    assert format_status(one, False) == "1 player online, map is Box Station."
    many = Snapshot(key="vg", kind="ss13", online=True, players=2)
    assert format_status(many, False) == "2 players online."


def test_admin_counts_are_also_singularised():
    from mommi.cogs.serverstatus import format_status
    from mommi.gameserver import Snapshot

    snap = Snapshot(key="vg", kind="ss13", online=True, players=5, admins=1, afk_admins=1)
    text = format_status(snap, True)
    assert "**1** active admin online" in text
    assert "**1** AFK admin online" in text


@pytest.mark.parametrize(
    "source,target,expected",
    [
        ("100 degC", "degF", "212 °F"),
        ("100 C", "F", "212 °F"),
        ("100c", "f", "212 °F"),
        ("100 celsius", "fahrenheit", "212 °F"),
        ("50 KG", "LB", "110.231 lb"),
        ("10 KM", "mi", "6.21371 mi"),
        ("10 mph", "km/h", "16.0934 km/h"),
        ("32 degF", "degC", "0 °C"),
    ],
)
def test_convert_handles_sloppy_input(source, target, expected):
    from mommi.cogs.units import convert

    assert convert(source, target) == expected


@pytest.mark.parametrize(
    "source,target,expected",
    [
        ("100 C", "mC", "100,000 mC"),
        ("1 F", "uF", "1,000,000 µF"),
        ("1 mm", "m", "0.001 m"),
        ("1 Mm", "m", "1,000,000 m"),
    ],
)
def test_exact_parse_beats_the_forgiving_fallback(source, target, expected):
    from mommi.cogs.units import convert

    assert convert(source, target) == expected


def test_convert_still_rejects_nonsense():
    from mommi.cogs.units import convert

    for source, target in [("100deC", "degF"), ("10 blorp", "km"), ("hello", "there")]:
        with pytest.raises(ValueError):
            convert(source, target)


def test_incompatible_units_give_a_readable_error():
    from mommi.cogs.units import convert

    with pytest.raises(ValueError, match="aren't the same kind of thing"):
        convert("10 kg", "m")


def test_registry_does_not_redefine_builtin_units():
    import warnings

    from mommi.cogs.units import convert

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert convert("2 hr", "min") == "120 min"
