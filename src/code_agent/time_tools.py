from __future__ import annotations

import difflib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CITY_TIMEZONES = {
    "beijing": "Asia/Shanghai",
    "chongqing": "Asia/Shanghai",
    "chungking": "Asia/Shanghai",
    "chhongcbin": "Asia/Shanghai",
    "shanghai": "Asia/Shanghai",
    "wuhan": "Asia/Shanghai",
    "wuhsn": "Asia/Shanghai",
    "hong kong": "Asia/Hong_Kong",
    "kyoto": "Asia/Tokyo",
    "osaka": "Asia/Tokyo",
    "tokyo": "Asia/Tokyo",
    "seoul": "Asia/Seoul",
    "singapore": "Asia/Singapore",
    "kolkata": "Asia/Kolkata",
    "calcutta": "Asia/Kolkata",
    "london": "Europe/London",
    "new york": "America/New_York",
    "san francisco": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles",
}

FIXED_OFFSETS = {
    "Asia/Shanghai": 8,
    "Asia/Hong_Kong": 8,
    "Asia/Tokyo": 9,
    "Asia/Seoul": 9,
    "Asia/Singapore": 8,
    "Asia/Kolkata": 5.5,
    "Europe/London": 1,
    "America/New_York": -4,
    "America/Los_Angeles": -7,
}


def local_time_for(location: str) -> str:
    normalized = " ".join(location.lower().strip().split())
    timezone_name = CITY_TIMEZONES.get(normalized)
    matched_location = normalized
    if timezone_name is None:
        matches = difflib.get_close_matches(normalized, CITY_TIMEZONES.keys(), n=1, cutoff=0.74)
        if matches:
            matched_location = matches[0]
            timezone_name = CITY_TIMEZONES[matched_location]
    if timezone_name is None:
        return (
            f"I do not know the timezone for {location!r}. "
            "Use web_search for unfamiliar places or provide a country/region."
        )

    tzinfo = _timezone_for(timezone_name)
    now = datetime.now(tzinfo)

    label = _title_location(matched_location)
    offset = now.strftime("%z")
    formatted_offset = f"UTC{offset[:3]}:{offset[3:]}" if offset else "UTC"
    return f"{label}: {now.strftime('%I:%M %p on %B %d, %Y')} ({timezone_name}, {formatted_offset})"


def _title_location(location: str) -> str:
    special = {
        "chhongcbin": "Chongqing",
        "chungking": "Chongqing",
        "wuhsn": "Wuhan",
    }
    return special.get(location, location.title())


def _timezone_for(timezone_name: str):
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        hours = FIXED_OFFSETS.get(timezone_name)
        if hours is None:
            raise
        whole_hours = int(hours)
        minutes = int((hours - whole_hours) * 60)
        return timezone(timedelta(hours=whole_hours, minutes=minutes))
