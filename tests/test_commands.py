from __future__ import annotations

import re

from tests.conftest import BOT_ID, OWNER_ID, FakeMember, FakeRole


async def test_unprefixed_message_does_not_trigger_a_prefix_command(harness):
    assert await harness.say("magic8ball", mention=False) == []


async def test_prefix_is_required_and_accepted_with_nickname_form(harness):
    harness.sent.clear()
    from tests.conftest import FakeChannel, FakeMessage

    message = FakeMessage(f"<@!{BOT_ID}> magic8ball", FakeMember(), harness.guild, FakeChannel())
    await harness.bot.on_message(message)
    assert len(harness.texts) == 1


async def test_bot_ignores_its_own_messages(harness):
    from tests.conftest import FakeChannel, FakeMessage, FakeUser

    message = FakeMessage(f"<@{BOT_ID}> magic8ball", FakeUser(), harness.guild, FakeChannel())
    await harness.bot.on_message(message)
    assert harness.texts == []


async def test_unconfigured_guild_is_ignored(harness):
    from tests.conftest import FakeChannel, FakeGuild, FakeMessage

    other = FakeGuild(id=1234567890, name="somewhere else")
    message = FakeMessage(f"<@{BOT_ID}> magic8ball", FakeMember(), other, FakeChannel())
    await harness.bot.on_message(message)
    assert harness.texts == []


async def test_magic8ball(harness):
    from mommi.cogs.chance import EIGHTBALL

    assert harness.only_text() in EIGHTBALL if await harness.say("magic8ball") else False


async def test_roll(harness):
    await harness.say("2d6")
    assert re.match(r"Results: \d+, \d+ = \d+$", harness.only_text())


async def test_roll_with_modifier(harness):
    await harness.say("1d1+5")
    assert harness.only_text() == "Results: 1 + 5 = 6"


async def test_roll_cap(harness):
    await harness.say("99999d6")
    assert "Max is 100" in harness.only_text()


async def test_pick(harness):
    await harness.say("pick(alpha, beta)")
    assert harness.only_text() in ("**alpha**", "**beta**")


async def test_pick_needs_two(harness):
    await harness.say("pick(lonely)")
    assert "at least 2" in harness.only_text()


async def test_rand(harness):
    await harness.say("rand 5 5")
    assert harness.only_text() == "5"


async def test_rand_handles_reversed_bounds(harness):
    await harness.say("rand 10 1")
    assert 1 <= int(harness.only_text()) <= 10


async def test_based_fires_without_a_mention(harness):
    await harness.say("based", mention=False)
    assert harness.only_text() in ("Based on what?", "Not Based.")


async def test_based_in_dutch(harness):
    await harness.say("gebaseerd", mention=False)
    assert harness.only_text() in ("Gebaseerd op wat?", "Niet Gebaseerd.")


async def test_wyci(harness):
    await harness.say("ports when", mention=False)
    assert harness.only_text() in ("When You Code It.", "Never.")


async def test_tetris_is_off_by_default_in_the_example_config(harness):
    assert await harness.say("i like tetris", mention=False) == []


async def test_ordinary_chatter_says_nothing(harness):
    assert await harness.say("just talking about the round", mention=False) == []


async def test_gettingstarted(harness):
    await harness.say("gettingstarted")
    assert "hackmd.io" in harness.only_text()


async def test_testmerge_stub(harness):
    await harness.say("testmerge")
    assert "can't do that" in harness.only_text()


async def test_help_lists_topics(harness):
    await harness.say("help")
    text = harness.only_text()
    assert "Available topics" in text
    for topic in ("dice", "status", "reminders", "who", "graph"):
        assert topic in text


async def test_help_topic(harness):
    await harness.say("help dice")
    assert "saving throw" in harness.only_text()


async def test_help_unknown_topic(harness):
    await harness.say("help nonsense")
    assert "Invalid topic" in harness.only_text()


async def test_help_callable_article_is_awaited(harness):
    await harness.say("help status")
    assert "IS THE SERVER DOWN" in harness.only_text()


async def test_resp_requires_the_configured_role(harness):
    await harness.say("resp add greet hello")
    assert "not allowed" in harness.only_text()


async def test_resp_add_list_read_remove(harness):
    editor = FakeMember(roles=[FakeRole(100000000000000006, "owner")])

    await harness.say("resp add greet hello there", author=editor)
    assert "✅" in harness.last_message.reactions

    await harness.say("resp list")
    assert "greet" in harness.only_text()

    await harness.say("$greet", mention=False)
    assert harness.only_text() == "hello there"

    await harness.say("resp remove greet", author=editor)
    assert "✅" in harness.last_message.reactions

    assert await harness.say("$greet", mention=False) == []


async def test_resp_remove_unknown(harness):
    editor = FakeMember(roles=[FakeRole(100000000000000006, "owner")])
    await harness.say("resp remove nope", author=editor)
    assert "❓" in harness.last_message.reactions


async def test_resp_list_when_empty(harness):
    await harness.say("resp list")
    assert "no responses" in harness.only_text()


async def test_markov_learns_then_generates(harness):
    for _ in range(3):
        await harness.say("the quick brown fox jumps over the lazy dog", mention=False)
    await harness.say("markov(the)")
    text = harness.only_text()
    assert text.startswith("the")


async def test_markov_unknown_word(harness):
    await harness.say("the quick brown fox jumps over the lazy dog", mention=False)
    await harness.say("markov(zzzz)")
    assert "unknown word" in harness.only_text()


async def test_markov_with_nothing_learned(harness):
    await harness.say("markov")
    assert "haven't learned" in harness.only_text()


async def test_markov_does_not_learn_from_commands_aimed_at_us(harness):
    await harness.say("the quick brown fox jumps over the lazy dog")
    await harness.say("markov(the)")
    assert "haven't learned" in harness.only_text()


async def test_remind_schedules(harness):
    await harness.say("remind 5m check the thing")
    text = harness.only_text()
    assert text.startswith("#") and "<t:" in text
    assert "✅" in harness.last_message.reactions


async def test_remind_rejects_the_past(harness):
    await harness.say("remind 2020/01/01 too late")
    assert "no time travel" in harness.only_text()
    assert "❌" in harness.last_message.reactions


async def test_remind_rejects_garbage_time(harness):
    await harness.say("remind soonish do a thing")
    assert "Invalid time format" in harness.only_text()


async def test_remindlist_and_unremind(harness):
    await harness.say("remind 1h thing one")
    uid = int(re.match(r"#(\d+)", harness.only_text()).group(1))

    await harness.say("remindlist")
    assert str(uid) in str(harness.embeds[0].description)

    await harness.say(f"unremind {uid}")
    assert "away it goes" in harness.only_text()

    await harness.say("remindlist")
    assert "No reminders pending" in harness.only_text()


async def test_unremind_unknown_uid(harness):
    await harness.say("unremind 4242")
    assert "No reminder by that UID" in harness.only_text()


async def test_cannot_unremind_someone_elses(harness):
    await harness.say("remind 1h mine", author=FakeMember(id=1111))
    uid = int(re.match(r"#(\d+)", harness.only_text()).group(1))
    await harness.say(f"unremind {uid}", author=FakeMember(id=2222))
    assert "admin perms" in harness.only_text()


async def test_remindlist_for_others_needs_admin(harness):
    await harness.say("remindlist <@1111>", author=FakeMember(id=2222))
    assert "No perms" in harness.only_text()


async def test_unit_conversion(harness):
    await harness.say("10 mph as km/h")
    assert harness.only_text() == "16.0934 km/h"


async def test_unit_temperature(harness):
    await harness.say("100 degC to degF")
    assert harness.only_text() == "212 °F"


async def test_unit_incompatible(harness):
    await harness.say("10 kg as m")
    assert "aren't the same kind of thing" in harness.only_text()


async def test_unit_nonsense(harness):
    await harness.say("10 blorp as km")
    assert "understand" in harness.only_text()


async def test_owner_only_command_is_denied(harness):
    await harness.say("shutdown")
    assert harness.only_text() == "*buzz*"


async def test_owner_bypasses_role_checks(harness):
    await harness.say("modules", author=FakeMember(id=OWNER_ID))
    assert "Reminders" in harness.only_text()


async def test_testperm(harness):
    await harness.say("testperm OWNER", author=FakeMember(roles=[FakeRole(100000000000000006)]))
    assert harness.only_text() == "True"


async def test_testperm_unknown_tier(harness):
    await harness.say("testperm WIZARD")
    assert "Unknown tier" in harness.only_text()


async def test_status_reports_a_live_server(harness, monkeypatch):
    from mommi.gameserver import Snapshot

    async def fake(key, config):
        return Snapshot(key=key, kind="ss13", online=True, players=17, map_name="Box Station")

    import mommi.gameserver as gs
    monkeypatch.setattr(gs, "query", fake)
    await harness.say("status")
    assert harness.only_text() == "17 players online, map is Box Station."


async def test_status_unknown_key(harness):
    await harness.say("status nosuchserver")
    assert "Unknown key" in harness.only_text()


async def test_status_list(harness):
    await harness.say("status list")
    assert "vg" in harness.only_text()


async def test_who(harness, monkeypatch):
    from mommi.gameserver import Snapshot

    async def fake(key, config):
        return Snapshot(
            key=key, kind="ss13", online=True, players=3, names_available=True,
            player_names=["Zed", "alice", "Bob"], map_name="Box Station",
        )

    import mommi.gameserver as gs
    monkeypatch.setattr(gs, "query", fake)
    await harness.say("who")
    embed = harness.embeds[0]
    assert embed.title == "vg — 3 players online"
    assert embed.description == "alice\nBob\nZed"


async def test_who_on_an_empty_server(harness, monkeypatch):
    from mommi.gameserver import Snapshot

    async def fake(key, config):
        return Snapshot(key=key, kind="ss13", online=True, players=0, names_available=True)

    import mommi.gameserver as gs
    monkeypatch.setattr(gs, "query", fake)
    await harness.say("who")
    assert harness.embeds[0].description == "Nobody's on."


async def test_players_summary(harness, monkeypatch):
    from mommi.gameserver import Snapshot

    async def fake(key, config):
        return Snapshot(key=key, kind="ss13", online=True, players=9)

    import mommi.gameserver as gs
    monkeypatch.setattr(gs, "query", fake)
    await harness.say("players")
    assert "**vg**: 9 players" in harness.only_text()


async def test_graph_with_no_history_says_so(harness):
    await harness.say("graph")
    assert "don't have any player history" in harness.only_text()


async def test_graph_rejects_a_bad_period(harness):
    await harness.say("graph vg banana")
    assert "don't understand that period" in harness.only_text()


async def test_github_lookup_is_silent_without_a_matching_repo(harness):
    assert await harness.say("see issue [12]", mention=False) == []


async def test_autolabels_lists_the_configured_mappings(harness):
    await harness.say("autolabels")
    embed = harness.embeds[0]
    assert embed.title == "Autolabels for toomykins/vgstation13"
    assert "`[fix]` <> Bugfix" in embed.description
    assert "`[balance]` <> Balance" in embed.description


async def test_autolabels_reacts_negatively_when_nothing_is_configured(harness, monkeypatch):

    cog = harness.bot.get_cog("GitHub")
    monkeypatch.setattr(type(cog), "repos_for", lambda self, ctx: [])
    await harness.say("autolabels")
    assert "❌" in harness.last_message.reactions


async def test_highpop_with_no_history(harness):
    await harness.say("highpop")
    assert "don't have any player history" in str(harness.embeds[0].description)


async def test_highpop_reports_peak_average_time_and_day(harness):
    import time

    now = time.time()
    storage = harness.bot.storage

    for day_offset in range(21):
        ts_day = now - day_offset * 86400
        for hour in (4, 20):
            for minute in (0, 5, 10):
                ts = ts_day - (ts_day % 86400) + hour * 3600 + minute * 60
                if ts > now:
                    continue
                await storage.record_players(100000000000000001, "vg", ts, 40 if hour == 20 else 2)

    await harness.say("highpop 30d")
    embed = harness.embeds[0]
    fields = {f.name: str(f.value) for f in embed.fields}
    assert "population over the last" in embed.title
    assert "Peak" in fields and "40 players" in fields["Peak"]
    assert "20:00–21:00" in fields["Busiest time"]
    assert "04:00–05:00" in fields["Deadest time"]
    assert "Average" in fields
    assert "samples" in embed.footer.text


async def test_highpop_rejects_a_bad_period(harness):
    await harness.say("highpop vg wednesday")
    assert "don't understand that period" in harness.only_text()


async def test_highpop_unknown_server(harness):
    await harness.say("highpop nosuchserver")
    assert "Unknown key" in str(harness.embeds[0].description)


async def test_emoji_falls_back_when_the_bot_cannot_use_the_custom_one(harness):
    cog = harness.bot.get_cog("GitHub")
    assert cog.emoji("PRopened") == "🟢"
    assert cog.emoji("PRmerged") == "🟣"
    assert cog.emoji("ISSclosed") == "🟥"
    assert cog.emoji("upvote") == "👍"


async def test_emoji_uses_the_custom_one_when_available(harness, monkeypatch):
    cog = harness.bot.get_cog("GitHub")
    monkeypatch.setattr(harness.bot, "get_emoji", lambda eid: object())
    assert cog.emoji("PRopened") == "<:PRopened:245910125041287168>"


async def test_emoji_id_is_configurable(harness, monkeypatch):
    cog = harness.bot.get_cog("GitHub")
    monkeypatch.setattr(harness.bot, "get_emoji", lambda eid: object())
    monkeypatch.setitem(harness.bot.config.modules, "github", {"emoji": {"PRopened": 12345}})
    assert cog.emoji("PRopened") == "<:PRopened:12345>"


async def test_bad_emoji_id_falls_back_rather_than_raising(harness, monkeypatch):
    cog = harness.bot.get_cog("GitHub")
    monkeypatch.setitem(harness.bot.config.modules, "github", {"emoji": {"PRopened": "nonsense"}})
    assert cog.emoji("PRopened") == "🟢"
