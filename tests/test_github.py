from __future__ import annotations

from mommi.cogs.github import extension_colour, format_body, repo_matches


class FakeCtx:
    def __init__(self, alias=None):
        self.alias = alias


def test_prefix_required_blocks_bare_references():
    config = {"repo": "a/b", "prefix": "vg", "prefix_required": True}
    assert repo_matches(config, FakeCtx(), None) is False
    assert repo_matches(config, FakeCtx(), "vg") is True


def test_prefix_not_required_allows_bare():
    config = {"repo": "a/b", "prefix": "vg", "prefix_required": False}
    assert repo_matches(config, FakeCtx(), None) is True


def test_wrong_prefix_never_matches():
    config = {"repo": "a/b", "prefix": "vg", "prefix_required": False}
    assert repo_matches(config, FakeCtx(), "tg") is False


def test_whitelisted_channel_drops_the_prefix_requirement():
    config = {"repo": "a/b", "prefix": "vg", "prefix_required": True, "prefix_whitelist": ["coderbus"]}
    assert repo_matches(config, FakeCtx("coderbus"), None) is True
    assert repo_matches(config, FakeCtx("general"), None) is False


def test_accepts_the_example_configs_key_spelling():
    config = {"repo": "a/b", "repo_prefix": "vg", "repo_prefix_required": False}
    assert repo_matches(config, FakeCtx(), "vg") is True
    assert repo_matches(config, FakeCtx(), None) is True


def test_format_body_strips_html_comments():
    body = "Real text.<!-- template boilerplate\nover lines -->More text."
    assert format_body(body) == "Real text.More text."


def test_format_body_truncates():
    assert format_body("x" * 900).endswith("...")
    assert len(format_body("x" * 900)) == 503


def test_format_body_handles_none():
    assert format_body(None) == ""


def test_extension_colour_is_stable_and_differs_by_extension():
    assert extension_colour("a/b/thing.dm") == extension_colour("other.dm")
    assert extension_colour("x.dm") != extension_colour("x.py")
