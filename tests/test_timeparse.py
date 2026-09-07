from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mommi.timeparse import TimeParseError, parse_time

NOW = datetime(2026, 6, 15, 14, 37, 22, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "spec,delta",
    [
        ("5m", timedelta(minutes=5)),
        ("30s", timedelta(seconds=30)),
        ("2h", timedelta(hours=2)),
        ("3d", timedelta(days=3)),
        ("1w", timedelta(weeks=1)),
        ("1d12h", timedelta(days=1, hours=12)),
        ("1w2d3h4m5s", timedelta(weeks=1, days=2, hours=3, minutes=4, seconds=5)),
    ],
)
def test_relative(spec, delta):
    assert parse_time(spec, NOW) == NOW + delta


def test_repeated_unit_is_rejected():
    with pytest.raises(TimeParseError):
        parse_time("5m5m", NOW)


def test_absolute_date_means_midnight():
    assert parse_time("2026/12/25", NOW) == datetime(2026, 12, 25, 0, 0, 0, tzinfo=timezone.utc)


def test_absolute_hour_zeroes_smaller_units():
    assert parse_time("@18", NOW) == datetime(2026, 6, 15, 18, 0, 0, tzinfo=timezone.utc)


def test_absolute_full():
    assert parse_time("2026/12/25@09:30:15", NOW) == datetime(
        2026, 12, 25, 9, 30, 15, tzinfo=timezone.utc
    )


def test_dash_separator_works():
    assert parse_time("2026-12-25", NOW).date() == datetime(2026, 12, 25).date()


def test_iso_with_timezone_converts_to_utc():
    assert parse_time("2026-12-25T09:00:00+02:00", NOW) == datetime(
        2026, 12, 25, 7, 0, 0, tzinfo=timezone.utc
    )


def test_naive_iso_is_assumed_utc():
    assert parse_time("2026-12-25T09:00:00", NOW) == datetime(
        2026, 12, 25, 9, 0, 0, tzinfo=timezone.utc
    )


def test_empty_string_is_rejected():
    with pytest.raises(TimeParseError):
        parse_time("", NOW)
    with pytest.raises(TimeParseError):
        parse_time("   ", NOW)


@pytest.mark.parametrize("spec", ["tomorrow", "5x", "banana", "99999999w", "2026/13/45"])
def test_nonsense_is_rejected(spec):
    with pytest.raises(TimeParseError):
        parse_time(spec, NOW)


def test_result_is_always_timezone_aware():
    assert parse_time("5m", NOW).tzinfo is not None
