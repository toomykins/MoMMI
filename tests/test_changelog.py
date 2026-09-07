from __future__ import annotations

from mommi.changelog import parse_body_changelog


def test_real_vgstation_pr_body():
    body = (
        "Ghosts can now possess dionae and mushmonkeys via clicking on them, "
        "should they have no client controlling them.\r\n\r\n:cl:\r\n"
        " * rscadd: Ghosts can now possess inactive diona nymphs and mushrum monkeys "
        "by clicking on them.  \r\n"
        " * rscadd: Dionae now don't expire after harvesting, should they not be "
        "possessed in the given time.  "
    )
    assert parse_body_changelog(body) == [
        {
            "rscadd": "Ghosts can now possess inactive diona nymphs and mushrum monkeys "
            "by clicking on them."
        },
        {
            "rscadd": "Dionae now don't expire after harvesting, should they not be "
            "possessed in the given time."
        },
    ]


def test_emoji_header_works():
    assert parse_body_changelog("🆑\n- bugfix: fixed a thing") == [{"bugfix": "fixed a thing"}]


def test_all_entry_types_are_recognised():
    types = [
        "bugfix", "wip", "tweak", "soundadd", "sounddel", "rscdel",
        "rscadd", "imageadd", "imagedel", "spellcheck", "experiment", "tgs",
    ]
    body = ":cl:\n" + "\n".join(f"- {t}: did {t}" for t in types)
    assert parse_body_changelog(body) == [{t: f"did {t}"} for t in types]


def test_no_header_means_no_entries():
    assert parse_body_changelog("Just a normal PR description.\n- bugfix: nope") == []


def test_empty_and_none_bodies():
    assert parse_body_changelog("") == []
    assert parse_body_changelog(None) == []


def test_unknown_entry_type_is_ignored():
    assert parse_body_changelog(":cl:\n- notatype: whatever") == []


def test_entries_before_the_header_are_ignored():
    body = "- bugfix: before\n\n:cl:\n- tweak: after"
    assert parse_body_changelog(body) == [{"tweak": "after"}]


def test_dash_and_asterisk_bullets_both_work():
    body = ":cl:\n* tweak: star\n- tweak: dash\ntweak: bare"
    assert parse_body_changelog(body) == [
        {"tweak": "star"}, {"tweak": "dash"}, {"tweak": "bare"}
    ]
