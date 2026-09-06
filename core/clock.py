"""Granica doby. Doba logiczna zaczyna się o 4:00, nie o północy."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

DEFAULT_DAY_START_HOUR = 4


def current_day(now: datetime, day_start_hour: int = DEFAULT_DAY_START_HOUR) -> str:
    """Zwraca logiczny dzień jako 'YYYY-MM-DD'.

    Wtorek 02:00 przy day_start_hour=4 należy jeszcze do poniedziałku.
    """
    if not 0 <= day_start_hour <= 23:
        raise ValueError(f"day_start_hour poza zakresem: {day_start_hour}")
    return (now - timedelta(hours=day_start_hour)).date().isoformat()


def next_day_boundary(
    now: datetime, day_start_hour: int = DEFAULT_DAY_START_HOUR
) -> datetime:
    """Moment, w którym liczniki się wyzerują."""
    day = date.fromisoformat(current_day(now, day_start_hour))
    return datetime.combine(day + timedelta(days=1), time(hour=day_start_hour))