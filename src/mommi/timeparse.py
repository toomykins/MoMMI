from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import cast

import dateutil.parser

DATE_RE = re.compile(
    r"^(?:(\d{4})[/-](\d{1,2})[/-](\d{1,2}))?"


    r"(?:(?(1)@|@?)(\d{1,2})(?::(\d{2})(?::(\d{2}))?)?)?$"
)
RELATIVE_SECTION_RE = re.compile(r"(\d+)([wdhms])")
RELATIVE_VERIFY_RE = re.compile(r"^(?:\d+[wdhms])+$", re.IGNORECASE)

UNITS = {
    "w": "weeks",
    "d": "days",
    "h": "hours",
    "m": "minutes",
    "s": "seconds",
}


MAX_OFFSET = timedelta(days=365 * 10)


class TimeParseError(ValueError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_relative(spec: str, now: datetime | None = None) -> datetime:
    if RELATIVE_VERIFY_RE.match(spec) is None:
        raise TimeParseError(f"{spec!r} is not a relative offset.")

    now = now or utcnow()
    seen: set[str] = set()
    delta = timedelta()
    for amount, unit in RELATIVE_SECTION_RE.findall(spec.lower()):
        if unit in seen:
            raise TimeParseError(f"Unit {unit!r} appears more than once in {spec!r}.")
        seen.add(unit)
        delta += timedelta(**{UNITS[unit]: int(amount)})

    if delta > MAX_OFFSET:
        raise TimeParseError("That is an absurd amount of time into the future.")
    return now + delta


def parse_absolute(spec: str, now: datetime | None = None) -> datetime:
    match = DATE_RE.match(spec)
    if match is None or not any(match.groups()):
        raise TimeParseError(f"{spec!r} is not a date.")

    now = now or utcnow()
    year, month, day, hour, minute, second = match.groups()

    if year is not None:
        base = now.replace(year=int(year), month=int(month), day=int(day))
    else:
        base = now

    if hour is not None:
        base = base.replace(
            hour=int(hour), minute=int(minute or 0), second=int(second or 0), microsecond=0
        )
    elif year is not None:

        base = base.replace(hour=0, minute=0, second=0, microsecond=0)

    if not (0 <= base.hour <= 23):
        raise TimeParseError("Hour out of range.")
    return base


def parse_iso(spec: str) -> datetime:
    try:
        parsed = dateutil.parser.isoparse(spec)
    except (ValueError, OverflowError) as e:
        raise TimeParseError(f"{spec!r} is not ISO 8601.") from e
    if parsed.tzinfo is None:
        return cast(datetime, parsed.replace(tzinfo=timezone.utc))
    return cast(datetime, parsed.astimezone(timezone.utc))


def parse_time(spec: str, now: datetime | None = None) -> datetime:
    spec = spec.strip()
    if not spec:
        raise TimeParseError("No time given.")

    for parser in (parse_relative, parse_absolute):
        try:
            return parser(spec, now)
        except (TimeParseError, ValueError):
            continue

    try:
        return parse_iso(spec)
    except TimeParseError:
        pass

    raise TimeParseError(f"Unknown date format: {spec!r}")
