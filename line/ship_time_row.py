from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from shared_ui.spacing import (
    BUTTON_PAD_H_TIGHT,
    BUTTON_PAD_V,
    BUTTON_RADIUS,
    GAP_DIALOG,
    GAP_MEDIUM,
    GAP_SMALL,
)
from shared_ui.type_scale import SIZE_BODY, SIZE_HEADING, SIZE_SMALL, in_pixels

from line.page_words import NO_VALUE, day_words, escaped, minutes_words, monday_of, tip
from line.ship_time import RECORD_NAME, ShipTimeRecord, read_the_record
from line.ship_time_charts import WEEKS_STYLE, weeks_section
from line.ship_time_work import ASKED_AT

MEDIAN = "median_minutes"
SLOWEST_TENTH = "slowest_tenth_minutes"
BUGS = "bug_reports"
CRASHES = "crashes"
NUMBER_NAMES = {MEDIAN: "Median ship time", SLOWEST_TENTH: "Slowest tenth", BUGS: "Bugs you reported",
                CRASHES: "App crashes"}
MEASURING_WORDS = "Ship Time: measuring the weeks, which takes a minute or two the first time."
TAKEN_BACK_HEADING = "Taken back by the main verifier and not shipped again"
WORK_ON_THIS = "Work on this"
WORK_ON_THIS_TIP = "Opens a new session in Claude Desktop with what to look into typed in, ready to send"
COUNTED_HOW = {
    MEDIAN: "From the message where you said ship it to the merge of the last pull request that go-ahead covered. "
            "The median is the typical delivery.",
    SLOWEST_TENTH: "How long one delivery in ten took, counted the same way.",
    BUGS: "Sessions you opened yourself to say something was broken, crashed, stuck or not working. Sessions another "
          "agent opened don't count.",
    CRASHES: "Crash reports Windows recorded for the family's apps on this machine.",
}


def _week_of(moment: datetime) -> str:
    year, week, _ = moment.astimezone().isocalendar()
    return f"{year}-W{week:02d}"


def _shown(key: str, value: float | None) -> str:
    if key in (MEDIAN, SLOWEST_TENTH):
        return minutes_words(value)
    return NO_VALUE if value is None else str(value)


@dataclass(frozen=True)
class NumberThisWeek:
    key: str
    value: float | None
    before: float | None

    @property
    def got_worse(self) -> bool:
        return self.value is not None and self.before is not None and self.value > self.before

    @property
    def got_better(self) -> bool:
        return self.value is not None and self.before is not None and self.value < self.before


def last_finished_weeks(weeks: list[dict], now: datetime) -> tuple[dict | None, dict | None]:
    finished = [row for row in weeks if row["week"] < _week_of(now)]
    return (finished[-1] if finished else None, finished[-2] if len(finished) > 1 else None)


def numbers_of(last: dict, before: dict | None) -> list[NumberThisWeek]:
    return [NumberThisWeek(key, last.get(key), before.get(key) if before else None) for key in NUMBER_NAMES]


def _change(number: NumberThisWeek) -> str:
    if number.before is None or number.value is None:
        return "<div class=number-change>No earlier week to compare</div>"
    before = _shown(number.key, number.before)
    if number.got_worse:
        return f"<div class=number-change><span class=worse>worse</span> than the week before ({before})</div>"
    if number.got_better:
        return f"<div class=number-change><span class=better>better</span> than the week before ({before})</div>"
    return "<div class=number-change>Same as the week before</div>"


def _work_on_this(number: NumberThisWeek, week: str) -> str:
    if not number.got_worse:
        return ""
    return (f"<a class=work-on-this title='{WORK_ON_THIS_TIP}' href='{ASKED_AT}{number.key}/{week}' "
            f">{WORK_ON_THIS}</a>")


def _number(number: NumberThisWeek, week: str) -> str:
    return (f"<div class=number data-tip='{tip(NUMBER_NAMES[number.key], [COUNTED_HOW[number.key]])}'>"
            f"<div class=number-name>{NUMBER_NAMES[number.key]}</div>"
            f"<div class=number-value>{_shown(number.key, number.value)}</div>{_change(number)}"
            f"{_work_on_this(number, week)}</div>")


def _taken_back(rows: list[dict]) -> str:
    if not rows:
        return ""
    items = "".join(f"<li>#{row['pull_request']} {escaped(row['title'])}, taken back "
                    f"{day_words(datetime.fromisoformat(row['taken_back']).date())}</li>" for row in rows)
    return f"<section class=taken-back><div class=taken-back-heading>{TAKEN_BACK_HEADING}</div><ul>{items}</ul></section>"


def ship_time_row(record: ShipTimeRecord | None, now: datetime) -> str:
    if record is None:
        return f"<section class=ship-time><div class=ship-time-heading>{MEASURING_WORDS}</div></section>"
    last, before = last_finished_weeks(record.weeks, now)
    if last is None:
        return ("<section class=ship-time><div class=ship-time-heading>Ship Time: no finished week yet</div></section>"
                f"{_taken_back(record.taken_back)}")
    numbers = "".join(_number(number, last["week"]) for number in numbers_of(last, before))
    return (f"<section class=ship-time><div class=ship-time-heading>Ship Time, week of {day_words(monday_of(last['week']))}"
            f"</div><div class=numbers>{numbers}</div></section>{_taken_back(record.taken_back)}")


def _weeks_of(record: ShipTimeRecord | None) -> str:
    return weeks_section(record.weeks) if record else ""


@dataclass(frozen=True)
class AroundTheChart:
    refresh_s: int | None = None
    ship_time: ShipTimeRecord | None = None

    def above_the_chart(self, now: datetime) -> str:
        return ship_time_row(self.ship_time, now)

    def below_the_chart(self) -> str:
        return _weeks_of(self.ship_time)


NOTHING_AROUND_THE_CHART = AroundTheChart()


def read_around_the_chart(state: Path, refresh_s: int | None) -> AroundTheChart:
    return AroundTheChart(refresh_s, read_the_record(state / RECORD_NAME))


SHIP_TIME_STYLE = f"""
.ship-time {{ margin:0 0 {GAP_DIALOG}px }}
.ship-time-heading, .taken-back-heading {{ color:var(--ink2); font-size:{in_pixels(SIZE_SMALL)}px;
  margin:0 0 {GAP_SMALL}px }}
.numbers {{ display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:{GAP_MEDIUM}px }}
.number {{ border:1px solid var(--border); border-radius:{BUTTON_RADIUS}px;
  padding:{BUTTON_PAD_V}px {BUTTON_PAD_H_TIGHT}px }}
.number-name, .number-change {{ color:var(--ink2); font-size:{in_pixels(SIZE_SMALL)}px }}
.number-value {{ font-size:{in_pixels(SIZE_HEADING)}px; font-weight:bold }}
.worse {{ color:var(--failed); font-weight:bold }} .better {{ color:var(--passed); font-weight:bold }}
.work-on-this {{ display:inline-block; margin-top:{GAP_SMALL}px; color:var(--ink); text-decoration:none;
  font-size:{in_pixels(SIZE_SMALL)}px; border:1px solid var(--border); border-radius:{BUTTON_RADIUS}px;
  padding:{BUTTON_PAD_V}px {BUTTON_PAD_H_TIGHT}px; cursor:pointer }}
.work-on-this:hover {{ background:var(--pressed) }}
.taken-back {{ margin:0 0 {GAP_DIALOG}px; font-size:{in_pixels(SIZE_BODY)}px }}
.taken-back ul {{ margin:0; padding-left:{GAP_DIALOG}px }}
""" + WEEKS_STYLE
