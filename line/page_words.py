from __future__ import annotations

import html
from datetime import date

NO_VALUE = "\N{EN DASH}"
SHOWN_IN_HOURS_FROM_MINUTES = 120


def escaped(text: object) -> str:
    return html.escape(str(text), quote=True)


def tip(head: str, lines: list[str]) -> str:
    return escaped("|".join([head, *lines]))


def monday_of(week: str) -> date:
    year, number = week.split("-W")
    return date.fromisocalendar(int(year), int(number), 1)


def day_words(day: date) -> str:
    return f"{day:%b} {day.day}"


def minutes_words(minutes: float | None) -> str:
    if minutes is None:
        return NO_VALUE
    return f"{minutes / 60:.1f} h" if minutes >= SHOWN_IN_HOURS_FROM_MINUTES else f"{minutes} min"
