from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass

from shared_ui.palette import BLUE, RED, YELLOW, as_hex
from shared_ui.spacing import BUTTON_RADIUS, GAP_DIALOG, GAP_MEDIUM
from shared_ui.type_scale import SIZE_SMALL, in_pixels

from line.page_words import (
    SHOWN_IN_HOURS_FROM_MINUTES,
    day_words,
    escaped,
    minutes_words,
    monday_of,
    tip,
)

SHIP_TIME_HEADING = "Ship time, minutes"
GOT_THROUGH_HEADING = "What got through"
EVERY_WEEK_HEADING = "Every week"
MEDIAN_WORD = "Median"
SLOWEST_WORD = "Slowest tenth"
BUGS_WORD = "Bugs you reported"
CRASHES_WORD = "App crashes"
TABLE_COLUMNS = ("Week of", "Shipped", MEDIAN_WORD, SLOWEST_WORD, "Bugs", "Crashes")

CHART_W = 1140
CHART_H = 260
LEFT = 52
RIGHT = 150
TOP = 14
BOTTOM = 30
GRID_LINES = 4
LABEL_EVERY_OTHER_WEEK_FROM = 13
POINT_R = 4
LINE_H = 2
BAR_W_LARGEST = 18
BAR_SHARE_OF_A_WEEK = 3
BAR_RX = 3
NICE_STEPS = (1, 2, 2.5, 5, 10)
LINES = (("slowest_tenth_minutes", "slowest-tenth", SLOWEST_WORD), ("median_minutes", "median", MEDIAN_WORD))
BARS = (("bug_reports", "bugs", -1), ("crashes", "crashes", 1))


def week_words(week: str) -> str:
    return day_words(monday_of(week))


def nice_top(highest: float) -> float:
    if highest <= 0:
        return 1
    step = 10 ** math.floor(math.log10(highest))
    return next(factor * step for factor in NICE_STEPS if factor * step >= highest)


@dataclass(frozen=True)
class Frame:
    weeks: int
    top_value: float

    @property
    def week_w(self) -> float:
        return (CHART_W - LEFT - RIGHT) / self.weeks

    def x(self, index: int) -> float:
        return LEFT + self.week_w * (index + 0.5)

    def y(self, value: float) -> float:
        return TOP + (CHART_H - TOP - BOTTOM) * (1 - value / self.top_value)


def _axes(frame: Frame, rows: list[dict], value_words: Callable[[float], str]) -> list[str]:
    parts = []
    for line in range(GRID_LINES + 1):
        value = frame.top_value * line / GRID_LINES
        parts.append(f"<rect class=grid x={LEFT} y={frame.y(value):.1f} width={CHART_W - LEFT - RIGHT} height=1></rect>"
                     f"<text class=axis-value x={LEFT - 8} y={frame.y(value) + 4:.1f}>{value_words(value)}</text>")
    step = 2 if len(rows) >= LABEL_EVERY_OTHER_WEEK_FROM else 1
    parts.extend(f"<text class=tick x={frame.x(index):.1f} y={CHART_H - 10}>{week_words(row['week'])}</text>"
                 for index, row in enumerate(rows) if index % step == 0)
    return parts


def _svg(label: str, parts: list[str]) -> str:
    return (f"<svg class=week-chart viewBox='0 0 {CHART_W} {CHART_H}' role=img aria-label='{escaped(label)}'>"
            f"{''.join(parts)}</svg>")


def _hit(frame: Frame, index: int, row: dict, lines: list[str]) -> str:
    return (f"<rect class=week-hit x={frame.x(index) - frame.week_w / 2:.1f} y={TOP} width={frame.week_w:.1f} "
            f"height={CHART_H - TOP - BOTTOM} data-tip='{tip(f'Week of {week_words(row['week'])}', lines)}'></rect>")


def _line(frame: Frame, rows: list[dict], key: str, css: str, word: str) -> list[str]:
    points = [(frame.x(index), frame.y(row[key]), row[key]) for index, row in enumerate(rows)
              if row.get(key) is not None]
    if not points:
        return []
    path = " ".join(f"M{x1:.1f},{y1 - LINE_H / 2:.1f} L{x2:.1f},{y2 - LINE_H / 2:.1f} "
                    f"L{x2:.1f},{y2 + LINE_H / 2:.1f} L{x1:.1f},{y1 + LINE_H / 2:.1f} Z"
                    for (x1, y1, _first), (x2, y2, _second) in itertools.pairwise(points))
    dots = "".join(f"<circle class={css} cx={x:.1f} cy={y:.1f} r={POINT_R}></circle>" for x, y, _value in points)
    x, y, value = points[-1]
    end_word = f"<text class=line-end x={x + 10:.1f} y={y + 4:.1f}>{word} {minutes_words(value)}</text>"
    return [f"<path class={css} d='{path}'></path>{dots}{end_word}"]


def _hours_or_minutes(minutes: float) -> str:
    return f"{round(minutes / 60)} h" if minutes >= SHOWN_IN_HOURS_FROM_MINUTES else f"{round(minutes)}m"


def ship_time_chart(rows: list[dict]) -> str:
    highest = max([row[key] for row in rows for key, _css, _word in LINES if row.get(key) is not None], default=0)
    frame = Frame(len(rows), nice_top(highest))
    parts = _axes(frame, rows, _hours_or_minutes)
    for key, css, word in LINES:
        parts.extend(_line(frame, rows, key, css, word))
    parts.extend(_hit(frame, index, row, [f"Shipped: {row.get('deliveries', 0)}",
                                          f"{MEDIAN_WORD}: {minutes_words(row.get('median_minutes'))}",
                                          f"{SLOWEST_WORD}: {minutes_words(row.get('slowest_tenth_minutes'))}"])
                 for index, row in enumerate(rows))
    return _svg(SHIP_TIME_HEADING, parts)


def got_through_chart(rows: list[dict]) -> str:
    highest = max([row.get(key) or 0 for row in rows for key, _css, _side in BARS], default=0)
    frame = Frame(len(rows), nice_top(highest))
    bar_w = min(BAR_W_LARGEST, frame.week_w / BAR_SHARE_OF_A_WEEK)
    parts = _axes(frame, rows, lambda value: str(round(value)))
    for index, row in enumerate(rows):
        for key, css, side in BARS:
            value = row.get(key) or 0
            if value:
                left = frame.x(index) + (-bar_w - 1 if side < 0 else 1)
                parts.append(f"<rect class={css} x={left:.1f} y={frame.y(value):.1f} width={bar_w:.1f} "
                             f"height={frame.y(0) - frame.y(value):.1f} rx={BAR_RX}></rect>")
        parts.append(_hit(frame, index, row, [f"{BUGS_WORD}: {row.get('bug_reports', 0)}",
                                              f"{CRASHES_WORD}: {row.get('crashes', 0)}"]))
    return _svg(GOT_THROUGH_HEADING, parts)


def every_week_table(rows: list[dict]) -> str:
    head = "".join(f"<th scope=col>{column}</th>" for column in TABLE_COLUMNS)
    body = "".join(
        "<tr>" + "".join(f"<td>{value}</td>" for value in (
            week_words(row["week"]), row.get("deliveries", 0), minutes_words(row.get("median_minutes")),
            minutes_words(row.get("slowest_tenth_minutes")), row.get("bug_reports", 0), row.get("crashes", 0))) + "</tr>"
        for row in reversed(rows))
    return f"<table class=every-week><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _key(css: str, word: str) -> str:
    return f"<span class=key><span class='week-key-color {css}'></span>{word}</span>"


def weeks_section(rows: list[dict]) -> str:
    if not rows:
        return ""
    ship_keys = _key("median", MEDIAN_WORD) + _key("slowest-tenth", SLOWEST_WORD)
    through_keys = _key("bugs", BUGS_WORD) + _key("crashes", CRASHES_WORD)
    return (f"<section class=weeks><div class=week-head><h2>{SHIP_TIME_HEADING}</h2><div class=legend>{ship_keys}</div>"
            f"</div>{ship_time_chart(rows)}"
            f"<div class=week-head><h2>{GOT_THROUGH_HEADING}</h2><div class=legend>{through_keys}</div></div>"
            f"{got_through_chart(rows)}<h2>{EVERY_WEEK_HEADING}</h2>{every_week_table(rows)}</section>")


WEEKS_STYLE = f"""
.weeks {{ margin-top:{GAP_DIALOG}px; --median:{as_hex(BLUE)}; --slowest:{as_hex(YELLOW)}; --bugs:{as_hex(YELLOW)};
  --crashes:{as_hex(RED)} }}
.weeks h2 {{ font-size:inherit; margin:{GAP_DIALOG}px 0 {GAP_MEDIUM}px }}
.week-head {{ display:flex; flex-wrap:wrap; justify-content:space-between; align-items:baseline; gap:{GAP_DIALOG}px }}
.week-chart {{ display:block; width:100%; height:auto; min-width:760px }}
.week-chart text {{ fill:var(--muted); font-size:{in_pixels(SIZE_SMALL)}px }}
.week-chart .tick {{ text-anchor:middle }} .week-chart .axis-value {{ text-anchor:end }}
.week-chart .line-end {{ fill:var(--ink) }}
.week-hit {{ fill:transparent }} .week-hit:hover {{ fill:var(--border) }}
.week-chart .median, .week-key-color.median {{ fill:var(--median); background:var(--median) }}
.week-chart .slowest-tenth, .week-key-color.slowest-tenth {{ fill:var(--slowest); background:var(--slowest) }}
rect.bugs, .week-key-color.bugs {{ fill:var(--bugs); background:var(--bugs) }}
rect.crashes, .week-key-color.crashes {{ fill:var(--crashes); background:var(--crashes) }}
.week-key-color {{ display:inline-block; width:12px; height:12px; border-radius:{BUTTON_RADIUS}px }}
.every-week {{ border-collapse:collapse; width:100%; font-size:{in_pixels(SIZE_SMALL)}px }}
.every-week th, .every-week td {{ text-align:right; padding:{GAP_MEDIUM}px; border-bottom:1px solid var(--border) }}
.every-week th:first-child, .every-week td:first-child {{ text-align:left }}
.every-week th {{ color:var(--ink2); font-weight:normal }}
"""
