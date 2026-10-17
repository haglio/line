from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app_support import haglio_settings, integration_runs, siblings
from app_support.file_channel import write_whole
from app_support.integration_runs import RunRecord

_SESSION_SUFFIX = re.compile(r"-[0-9a-f]{6}$")


def who_of(branch: str) -> str:
    name = _SESSION_SUFFIX.sub("", branch.removeprefix("claude/"))
    if name in ("", "HEAD"):
        return "a session on no branch"
    return name.replace("-", " ")


_TEST_FILE = re.compile(r"test_(\w+?)(?:_integration)?(?:\.py|\b)")
ALL = "all"
CHANGED = "changed"
NAMED = "named"
NO_FLAKES = "no flakes"
AGAIN = "again"
MEASURING = "measuring"
REPEAT_CHANGED = "--repeat-changed"
CHANGED_FLAG = "--changed"
WITHOUT_COVERAGE = "--no-cov"


def describe(args: list[str], kind: str) -> str:
    if kind in (ALL, CHANGED, NO_FLAKES):
        words = KIND_WORDS[kind]
        return words[:1].lower() + words[1:]
    named = [arg for arg in args if not arg.startswith("-")]
    files = {match[1] for arg in named for match in _TEST_FILE.finditer(arg.split("::")[0])}
    if not files:
        return "some tests"
    if len(files) == 1:
        name = next(iter(files)).replace("_", " ")
        return f'the "{name}" tests' if name.split()[0] in ("a", "an", "the") else f"the {name} tests"
    return f"tests from {len(files)} files"


@dataclass(frozen=True)
class Run:
    who: str
    what: str
    joined: datetime
    ended: datetime | None = None
    exit_code: int | None = None
    got_the_machine: datetime | None = None
    checkout: str = ""
    session: str = ""
    short: bool = False
    kind: str = ALL

    @property
    def holding(self) -> bool:
        return self.ended is None and self.got_the_machine is not None

    def waited_s(self, now: datetime) -> float:
        return ((self.got_the_machine or self.ended or now) - self.joined).total_seconds()

    def ran_s(self, now: datetime) -> float | None:
        return ((self.ended or now) - self.got_the_machine).total_seconds() if self.got_the_machine else None

    @property
    def verdict(self) -> str:
        if self.ended is None:
            return "going"
        if self.exit_code is None or self.got_the_machine is None:
            return "no result"
        return "passed" if self.exit_code == 0 else "failed"


VERIFIER = "main verifier"
VERIFYING_MAIN = "verifying-main"


def kind_of(record: RunRecord) -> str:
    if record.what[:1] == [REPEAT_CHANGED]:
        return NO_FLAKES
    if not record.short:
        return ALL
    if record.what[:1] == [CHANGED_FLAG]:
        return CHANGED
    if record.checkout == VERIFYING_MAIN:
        return MEASURING if WITHOUT_COVERAGE in record.what else AGAIN
    return NAMED


def runs_from(records: Iterable[RunRecord],
              still_going: Callable[[RunRecord], bool] = integration_runs.still_going) -> list[Run]:
    runs = []
    for record in records:
        if record.ended is not None:
            ended, exit_code = record.ended, record.exit_code
        elif still_going(record):
            ended, exit_code = None, None
        elif record.seen is not None:
            ended, exit_code = record.seen, None
        else:
            continue
        who, kind = VERIFIER if record.checkout == VERIFYING_MAIN else who_of(record.branch), kind_of(record)
        runs.append(Run(who, describe(record.what, kind), record.joined, ended, exit_code, record.got_the_machine,
                        record.checkout, record.session, record.short, kind))
    return sorted(runs, key=lambda run: run.joined)


def the_line(runs: Iterable[Run]) -> list[Run]:
    return sorted((run for run in runs if run.ended is None),
                  key=lambda run: (not run.holding, not run.short, run.joined))


VERDICT_WORDS = {"going": "Currently running", "passed": "Passed", "failed": "Failed", "no result": "No result"}
VERDICT_MARKS = {"going": "●", "passed": "✓", "failed": "✗", "no result": "?"}
WAITING_IN_LINE = "Waiting in line"
WAITING = "waiting"


def _state(run: Run) -> str:
    return WAITING if run.verdict == "going" and run.got_the_machine is None else run.verdict


def _state_words(run: Run) -> str:
    return WAITING_IN_LINE if _state(run) == WAITING else VERDICT_WORDS[run.verdict]


def _clock(when: datetime, now: datetime | None = None) -> str:
    local = when.astimezone()
    if now is not None and local.date() != now.astimezone().date():
        return local.strftime("%a %H:%M")
    return local.strftime("%H:%M")


def _span(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    hours, minutes = divmod(round(seconds / 60), 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def _tip(head: str, lines: list[str]) -> str:
    return _e("|".join([head, *lines]))


BARS_H = 30
GUTTER_H = 36
LANE_H = BARS_H + GUTTER_H
RAN_H = 20
WAITED_H = 8
ICON_H = 26
STEM_W = 1.2
RAN_RX = 5
SESSION_H = 2.4
DOT_EVERY = 6
KEY_W = 4 * DOT_EVERY
SHORTEST_BAR = 2
SMALLEST_HIT = 10
PLACE_X = 20
LABEL_W = 320
LABEL_CHARS = 42
CHART_W = 860
RIGHT_MARGIN = 20
TIMES_H = 22
CHART_TOP = 8
PERIODS_H = (1, 4, 12, 24)
SHOWN_FIRST_H = 4
GRID_EVERY_MIN = {1: 10, 4: 30, 12: 60, 24: 60}
LABEL_EVERY_MIN = {1: 10, 4: 60, 12: 120, 24: 180}
APP_NAME = "Line"
VERIFIER_ROW = "MAIN VERIFIER"
ROW_NOTES = {VERIFIER_ROW: "Runs the whole integration suite on each change that ships to main. Production Fun Time "
                           "moves to a change only once that run passes."}


def row_name(run: Run, names: dict[str, str]) -> str:
    if run.who == VERIFIER:
        return VERIFIER_ROW
    return names.get(run.session) or names.get(run.checkout) or run.who


def rows_in_line_order(runs: list[Run], line: list[Run], now: datetime, names: dict[str, str]) -> list[str]:
    in_line = list(dict.fromkeys(row_name(entry, names) for entry in line))
    last_seen: dict[str, datetime] = {}
    for run in runs:
        row = row_name(run, names)
        last_seen[row] = max(last_seen.get(row, run.joined), run.ended or now)
    return in_line + sorted((row for row in last_seen if row not in in_line), key=last_seen.get, reverse=True)


def _ticks(since: datetime, now: datetime, every_min: int) -> Iterator[datetime]:
    local = since.astimezone().replace(second=0, microsecond=0)
    tick = local + timedelta(minutes=-(local.hour * 60 + local.minute) % every_min)
    if tick < since:
        tick += timedelta(minutes=every_min)
    while tick <= now:
        yield tick
        tick += timedelta(minutes=every_min)


def _hours_words(hours: int) -> str:
    return "1 hour" if hours == 1 else f"{hours} hours"


Point = tuple[float, float]


@dataclass(frozen=True)
class Stick:
    name: str
    start: Point
    end: Point
    thickness: float

    def svg(self) -> str:
        (x1, y1), (x2, y2) = self.start, self.end
        length, middle_x, middle_y = math.dist(self.start, self.end) + self.thickness, (x1 + x2) / 2, (y1 + y2) / 2
        return (f"<rect class={self.name} x={middle_x - length / 2:.2f} y={middle_y - self.thickness / 2:.2f} "
                f"width={length:.2f} height={self.thickness} rx={self.thickness / 2} "
                f"transform='rotate({math.degrees(math.atan2(y2 - y1, x2 - x1)):.1f} {middle_x:.2f} {middle_y:.2f})'>"
                "</rect>")


def _circle_path(middle: Point, radius: float) -> str:
    (x, y), r = middle, radius
    return f"M{x - r:.2f},{y:.2f}A{r:.2f},{r:.2f} 0 1 0 {x + r:.2f},{y:.2f}A{r:.2f},{r:.2f} 0 1 0 {x - r:.2f},{y:.2f}Z"


@dataclass(frozen=True)
class Ring:
    middle: Point
    radius: float
    thickness: float

    def svg(self) -> str:
        return (f"<path class=ring fill-rule=evenodd d='{_circle_path(self.middle, self.radius + self.thickness / 2)}"
                f"{_circle_path(self.middle, self.radius - self.thickness / 2)}'></path>")


def _rounded_path(left: float, top: float, right: float, bottom: float, corner: float) -> str:
    arc = f"A{corner},{corner} 0 0 1"
    return (f"M{left + corner:.2f},{top:.2f}H{right - corner:.2f}{arc} {right:.2f},{top + corner:.2f}"
            f"V{bottom - corner:.2f}{arc} {right - corner:.2f},{bottom:.2f}H{left + corner:.2f}{arc} {left:.2f},"
            f"{bottom - corner:.2f}V{top + corner:.2f}{arc} {left + corner:.2f},{top:.2f}Z")


@dataclass(frozen=True)
class Box:
    width: float
    thickness: float
    corner: float

    def svg(self) -> str:
        inner = self.thickness
        return (f"<path class=box fill-rule=evenodd d='{_rounded_path(0, 0, self.width, ICON_H, self.corner)}"
                f"{_rounded_path(inner, inner, self.width - inner, ICON_H - inner, self.corner - inner)}'></path>")


@dataclass(frozen=True)
class Cell:
    left: float
    top: float
    size: float

    def svg(self) -> str:
        return (f"<rect class=cell x={self.left:.2f} y={self.top:.2f} width={self.size} height={self.size} "
                f"rx={self.size / 5:.2f}></rect>")


@dataclass(frozen=True)
class Dot:
    middle: Point
    radius: float

    def svg(self) -> str:
        return f"<circle class=dot cx={self.middle[0]:.2f} cy={self.middle[1]:.2f} r={self.radius}></circle>"


def _toward(point: Point, center: Point, share: float) -> Point:
    return center[0] + (point[0] - center[0]) * share, center[1] + (point[1] - center[1]) * share


@dataclass(frozen=True)
class Triangle:
    corners: tuple[Point, Point, Point]
    thickness: float

    def svg(self) -> str:
        sides = [math.dist(self.corners[(n + 1) % 3], self.corners[(n + 2) % 3]) for n in range(3)]
        center = tuple(sum(side * corner[axis] for side, corner in zip(sides, self.corners, strict=True)) / sum(sides)
                       for axis in (0, 1))
        (ax, ay), (bx, by), (cx, cy) = self.corners
        inradius = abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) / sum(sides)
        inner = [_toward(corner, center, 1 - self.thickness / inradius) for corner in self.corners]
        outline = "".join(f"M{a[0]:.2f},{a[1]:.2f}L{b[0]:.2f},{b[1]:.2f}L{c[0]:.2f},{c[1]:.2f}Z"
                          for a, b, c in (self.corners, inner))
        return f"<path class=triangle fill-rule=evenodd d='{outline}'></path>"


TAB, BLANK = 1, -1


def _one_jigsaw_side(tab_size: float) -> list[Point]:
    t = tab_size
    return [(0.2, 0), (0.5, -t), (0.5 - t, t), (0.5 - 2 * t, 3 * t), (0.5 + 2 * t, 3 * t), (0.5 + t, t), (0.5, -t),
            (0.8, 0), (1, 0)]


@dataclass(frozen=True)
class JigsawPiece:
    middle: Point
    side: float
    tab_size: float

    def svg(self) -> str:
        (x, y), half = self.middle, self.side / 2
        corners = [(x - half, y - half), (x + half, y - half), (x + half, y + half), (x - half, y + half)]
        outline = f"M{corners[0][0]:.2f},{corners[0][1]:.2f}"
        for start, end, facing in zip(corners, corners[1:] + corners[:1], (BLANK, TAB, BLANK, TAB), strict=True):
            along = ((end[0] - start[0]) / self.side, (end[1] - start[1]) / self.side)
            out = (along[1] * facing, -along[0] * facing)
            handles = [(start[0] + (u * along[0] + v * out[0]) * self.side, start[1] + (u * along[1] + v * out[1]) * self.side)
                       for u, v in _one_jigsaw_side(self.tab_size)]
            outline += "".join(f"C{a[0]:.2f},{a[1]:.2f} {b[0]:.2f},{b[1]:.2f} {c[0]:.2f},{c[1]:.2f}"
                               for a, b, c in zip(handles[::3], handles[1::3], handles[2::3], strict=True))
        return f"<path class=jigsaw d='{outline}Z'></path>"


@dataclass(frozen=True)
class Icon:
    width: float
    shapes: tuple[Stick | Ring | Box | Cell | Dot | Triangle | JigsawPiece, ...]

    @property
    def drawing(self) -> str:
        return "".join(shape.svg() for shape in self.shapes)


def _cells(columns_by_row: list[list[int]], across: int, size: float, gap: float) -> list[Cell]:
    width, height = across * (size + gap) - gap, len(columns_by_row) * (size + gap) - gap
    left, top = (ICON_H - width) / 2, (ICON_H - height) / 2
    return [Cell(left + column * (size + gap), top + row * (size + gap), size)
            for row, columns in enumerate(columns_by_row) for column in columns]


SQUARE = Box(ICON_H, 2, 4)
CIRCLE = Ring((ICON_H / 2, ICON_H / 2), ICON_H / 2 - 1.6, 2.5)


def _square_packed_with_a_grid() -> Icon:
    return Icon(ICON_H, (SQUARE, *_cells([[0, 1, 2, 3]] * 4, 4, 3.2, 1.4)))


def _puzzle_piece_in_a_circle() -> Icon:
    return Icon(ICON_H, (CIRCLE, *_cells([[1, 3], [0, 1, 2, 3, 4], [1, 3]], 5, 3, 0.6)))


def _jigsaw_piece_in_a_square() -> Icon:
    return Icon(ICON_H, (SQUARE, JigsawPiece((ICON_H / 2, ICON_H / 2), 11, 0.13)))


def _two_squares_in_a_circle() -> Icon:
    return Icon(ICON_H, (CIRCLE, *_cells([[1, 3]], 5, 3, 0.6)))


def _exclamation_mark_in_a_warning_triangle() -> Icon:
    middle = ICON_H / 2
    triangle = Triangle(((middle, 1.8), (1.2, ICON_H - 1.8), (ICON_H - 1.2, ICON_H - 1.8)), 2.2)
    return Icon(ICON_H, (triangle, Stick("bar", (middle, 11), (middle, 15.8), 2.4), Dot((middle, 19.6), 1.45)))


def _snowflake_in_a_slashed_circle() -> Icon:
    middle, ring, arm, branch_at, branch, branch_angle = CIRCLE.middle, CIRCLE.radius, 7.4, 5, 2.9, 40

    def point(angle: float, reach: float, start: Point = middle) -> Point:
        return start[0] + math.cos(math.radians(angle)) * reach, start[1] + math.sin(math.radians(angle)) * reach

    arms = [Stick("flake", point(angle + 180, arm), point(angle, arm), 1.5) for angle in (90, 30, 150)]
    for angle in range(90, 450, 60):
        fork = point(angle, branch_at)
        arms += [Stick("flake", fork, point(angle + side * branch_angle, branch, fork), 1.5) for side in (-1, 1)]
    return Icon(ICON_H, (CIRCLE, *arms, Stick("slash", point(225, ring), point(45, ring), CIRCLE.thickness)))


ICONS = {ALL: _square_packed_with_a_grid(), CHANGED: _puzzle_piece_in_a_circle(), NAMED: _two_squares_in_a_circle(),
         NO_FLAKES: _snowflake_in_a_slashed_circle(),
         AGAIN: _exclamation_mark_in_a_warning_triangle(), MEASURING: _jigsaw_piece_in_a_square()}
ICON_GAP = 4
KIND_WORDS = {ALL: "The whole suite", MEASURING: "Update relevance map", CHANGED: "Only all relevant tests",
              NAMED: "Specific tests", NO_FLAKES: "Nondeterministic test prevention", AGAIN: "Rerun failed tests"}
RELEVANCE = ["Relevance map",
             ("Once a day the main verifier runs each test file by itself and notes which code it uses: "
              "Update relevance map."),
             "A branch's Only all relevant tests run then picks just the tests that use the code it changed."]
KIND_TIPS = {MEASURING: RELEVANCE, CHANGED: RELEVANCE,
             NO_FLAKES: ["Nondeterministic test prevention",
                         ("Runs each new or changed test 10 times before its branch ships, so a test that only "
                          "passes some of the time is caught first.")]}


def icon_width(kind: str) -> float:
    return ICONS[kind].width


@dataclass(frozen=True)
class GutterIcon:
    anchor: float
    kind: str
    state: str
    left: float = 0.0

    @property
    def width(self) -> float:
        return icon_width(self.kind)


def _one_icon_per_pile(icons: list[GutterIcon]) -> list[GutterIcon]:
    piles: list[GutterIcon] = []
    for icon in sorted(icons, key=lambda icon: icon.anchor):
        pile = piles[-1] if piles else None
        if pile and pile.kind == icon.kind and icon.anchor < pile.anchor + pile.width + ICON_GAP:
            piles[-1] = replace(pile, state="failed" if "failed" in (pile.state, icon.state) else icon.state)
        else:
            piles.append(icon)
    return piles


def _spread_apart(icons: list[GutterIcon], right_edge: float) -> list[GutterIcon]:
    placed: list[GutterIcon] = []
    for icon in _one_icon_per_pile(icons):
        after_the_last = placed[-1].left + placed[-1].width + ICON_GAP if placed else icon.anchor
        placed.append(replace(icon, left=max(icon.anchor, after_the_last)))
    pulled_in: list[GutterIcon] = []
    for icon in reversed(placed):
        pulled_in.append(replace(icon, left=min(icon.left, right_edge - icon.width)))
        right_edge = pulled_in[-1].left - ICON_GAP
    return pulled_in[::-1]


def _gutter(top: float, icons: list[GutterIcon]) -> Iterator[str]:
    bars_bottom, icon_top = top + BARS_H / 2 + RAN_H / 2, top + BARS_H + (GUTTER_H - ICON_H) / 2
    for icon in _spread_apart(icons, LABEL_W + CHART_W + RIGHT_MARGIN):
        state, half = icon.state.replace(" ", "-"), STEM_W / 2
        yield (f"<polygon class='stem {state}' points='{icon.anchor - half:.1f},{bars_bottom} "
               f"{icon.anchor + half:.1f},{bars_bottom} {icon.left + half:.1f},{icon_top} {icon.left - half:.1f},"
               f"{icon_top}'></polygon><g class='icon {icon.kind.replace(' ', '-')} {state}' "
               f"transform='translate({icon.left:.1f},{icon_top})'>{ICONS[icon.kind].drawing}</g>")


@dataclass(frozen=True)
class Lifetime:
    spawned: datetime
    archived: datetime | None


@dataclass(frozen=True)
class Sessions:
    names: dict[str, str] = field(default_factory=dict)
    lifetimes: dict[str, Lifetime] = field(default_factory=dict)

    def lifetime_of(self, runs: Iterable[Run]) -> Lifetime | None:
        return next((self.lifetimes[key] for run in runs for key in (run.session, run.checkout)
                     if key in self.lifetimes), None)


@dataclass(frozen=True)
class Rows:
    order: list[str]
    places: dict[str, int]
    sessions: Sessions


def _across(now: datetime, hours: int) -> Callable[[datetime], float]:
    since = now - timedelta(hours=hours)

    def x(when: datetime) -> float:
        return LABEL_W + CHART_W * max(0.0, min(1.0, (when - since) / (now - since)))

    return x


def _times(now: datetime, hours: int) -> str:
    x = _across(now, hours)
    labels = "".join(f"<text class=tick x={x(tick):.1f} y=15>{tick.strftime('%H:%M')}</text>"
                     for tick in _ticks(now - timedelta(hours=hours), now, GRID_EVERY_MIN[hours])
                     if (tick.hour * 60 + tick.minute) % LABEL_EVERY_MIN[hours] == 0)
    return f"<svg viewBox='0 0 {LABEL_W + CHART_W + RIGHT_MARGIN} {TIMES_H}' aria-hidden=true>{labels}</svg>"


def _chart(runs: list[Run], now: datetime, hours: int, rows: Rows) -> str:
    since = now - timedelta(hours=hours)
    shown = [run for run in runs if (run.ended or now) >= since]
    row_of = {id(run): row_name(run, rows.sessions.names) for run in shown}
    lanes = [row for row in rows.order if row in row_of.values()]
    height = CHART_TOP + LANE_H * len(lanes)
    x = _across(now, hours)

    starts = sorted(x(run.got_the_machine) for run in shown if run.got_the_machine)

    def held_width(left: float, end: datetime) -> float:
        following = next((other for other in starts if other > left), x(now))
        return min(max(x(end) - left, SHORTEST_BAR), following - left)

    parts = [f"<rect class=grid x={x(tick) - 0.5:.1f} y=0 width=1 height={height}></rect>"
             for tick in _ticks(since, now, GRID_EVERY_MIN[hours])]
    for index, row in enumerate(lanes):
        middle = CHART_TOP + LANE_H * index + BARS_H / 2
        lifetime = rows.sessions.lifetime_of(run for run in shown if row_of[id(run)] == row)
        if lifetime is not None:
            start, end = x(lifetime.spawned), x(lifetime.archived or now)
            ended = f"archived {_clock(lifetime.archived, now)}" if lifetime.archived else "still open"
            parts.append(f"<rect class=session x={start:.1f} y={middle - SESSION_H / 2} width={end - start:.1f} "
                         f"height={SESSION_H} fill='url(#dots-{hours})'><title>Session spawned "
                         f"{_clock(lifetime.spawned, now)}, {ended}</title></rect>")
        if row in rows.places:
            parts.append(f"<text class=place x={PLACE_X} y={middle + 4.5}>{rows.places[row]}</text>")
        fitted = row if len(row) <= LABEL_CHARS else row[:LABEL_CHARS - 1].rstrip() + "…"
        note = ROW_NOTES.get(row) or (row if fitted != row else "")
        title = f"<title>{_e(note)}</title>" if note else ""
        parts.append(f"<text class=lane x={LABEL_W - 10} y={middle + 4.5}>{_e(fitted)}{title}</text>")
    icons_by_lane: dict[float, list[GutterIcon]] = {}
    bars: list[str] = []
    for run in shown:
        top = CHART_TOP + LANE_H * lanes.index(row_of[id(run)])
        middle = top + BARS_H / 2
        end = run.ended or now
        left, right = x(run.joined), max(x(end), x(run.joined) + 3)
        hit_left = left - max(0.0, SMALLEST_HIT - (right - left)) / 2
        split = min(right, x(run.got_the_machine)) if run.got_the_machine else right
        ran = run.ran_s(now)
        tip = _tip(_state_words(run), [
            row_of[id(run)], run.what,
            f"Joined {_clock(run.joined, now)}, ended {_clock(end, now) if run.ended else 'now'}",
            f"Waited {_span(run.waited_s(now))}, ran "
            + (_span(ran) if ran is not None else "never" if run.ended else "not yet")])
        group = f"<g class='run {run.verdict.replace(' ', '-')}' data-tip='{tip}'>"
        parts.append(f"{group}<rect class=hit x={hit_left:.1f} y={top} width={max(right - left, SMALLEST_HIT):.1f} "
                     f"height={BARS_H}></rect>"
                     + (f"<rect class=waited x={left:.1f} y={middle - WAITED_H / 2} width={split - left:.1f} "
                        f"height={WAITED_H} rx={WAITED_H / 2}></rect>" if split > left else "")
                     + "</g>")
        if run.got_the_machine:
            bars.append(f"{group}<rect class=ran x={split:.1f} y={middle - RAN_H / 2} "
                        f"width={held_width(split, end):.1f} height={RAN_H} rx={RAN_RX}></rect></g>")
        icons_by_lane.setdefault(top, []).append(GutterIcon(split, run.kind, _state(run)))
    parts.extend(bars)
    for top, icons in icons_by_lane.items():
        parts.extend(_gutter(top, icons))
    parts.append(f"<rect class=now x={x(now) - 0.75:.2f} y=0 width=1.5 height={height}></rect>")
    dots = (f"<defs><pattern id=dots-{hours} width={DOT_EVERY} height={SESSION_H} patternUnits=userSpaceOnUse>"
            f"<circle class=dot cx={DOT_EVERY / 2} cy={SESSION_H / 2} r={SESSION_H / 2}></circle></pattern></defs>")
    return (f"<svg viewBox='0 0 {LABEL_W + CHART_W + RIGHT_MARGIN} {height + 6}' role=img "
            f"aria-label='Integration runs in the last {_hours_words(hours)}'>{dots}{''.join(parts)}</svg>")


SESSION_TIP = _tip("Session", [("The dotted line runs from when the session was spawned until it was archived, or up "
                                "to now while it is still open.")])


def _kind_tip(kind: str) -> str:
    return f" data-tip='{_tip(KIND_TIPS[kind][0], KIND_TIPS[kind][1:])}'" if kind in KIND_TIPS else ""


def _charts_with_their_legend_and_periods(runs: list[Run], line: list[Run], now: datetime, sessions: Sessions) -> str:
    names = sessions.names
    rows = Rows(rows_in_line_order(runs, line, now, names),
                {row: place for place, row in enumerate(dict.fromkeys(row_name(entry, names) for entry in line),
                                                        start=1)},
                sessions)
    buttons = "".join(f"<button type=button data-hours={hours} aria-pressed={str(hours == SHOWN_FIRST_H).lower()}>"
                      f"{hours} h</button>" for hours in PERIODS_H)
    def shown_first(hours: int) -> str:
        return "" if hours == SHOWN_FIRST_H else " hidden"

    times = "".join(f"<div class='times period' data-hours={hours}{shown_first(hours)}>{_times(now, hours)}</div>"
                    for hours in PERIODS_H)
    charts = "".join(f"<figure class=period data-hours={hours}{shown_first(hours)}>{_chart(runs, now, hours, rows)}"
                     "</figure>" for hours in PERIODS_H)
    legend = "".join(f"<span class=key><span class='swatch {verdict.replace(' ', '-')}'></span>"
                     f"{VERDICT_MARKS[verdict]} {word}</span>" for verdict, word in VERDICT_WORDS.items())
    kinds = "".join(f"<span class=key{_kind_tip(kind)}><svg viewBox='0 0 {icon_width(kind):.1f} {ICON_H}'>"
                    f"<g class='icon {kind.replace(' ', '-')} key-ink'>"
                    f"{ICONS[kind].drawing}</g></svg>{words}</span>" for kind, words in KIND_WORDS.items())
    return (f"<div class=frozen><div class=chart-head><div class=legend>{legend}<span class=key>"
            f"<span class='swatch waited-key'></span>{WAITING_IN_LINE}</span><span class=key data-tip='{SESSION_TIP}'>"
            f"<span class='swatch session-key'></span>Session</span></div>"
            f"<div class=periods>{buttons}</div>"
            f"<div class=kinds>{kinds}</div></div>{times}</div>{charts}")


DARK_BACKGROUND = "#0d0d0d"
STYLE = f"""
:root {{ --bg:{DARK_BACKGROUND}; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781; --waiting:#454441; --grid:#2c2c2a;
  --border:rgba(255,255,255,.10); --passed:#0ca30c; --failed:#d03b3b; --noresult:#f0c93d; --going:#3987e5;
  --pressed:#2c2c2a; --unit:.95px; color-scheme:dark }}
.swatch {{ display:inline-block; width:calc({KEY_W} * var(--unit)); height:calc({RAN_H} * var(--unit));
  border-radius:calc({RAN_RX} * var(--unit)) }}
.swatch.waited-key {{ background:var(--waiting); height:calc({WAITED_H} * var(--unit));
  border-radius:calc({WAITED_H / 2} * var(--unit)) }}
.swatch.session-key {{ width:calc({KEY_W} * var(--unit)); height:calc({SESSION_H} * var(--unit)); border-radius:0;
  background:radial-gradient(circle, var(--muted) calc({SESSION_H / 2} * var(--unit)),
  transparent calc({SESSION_H / 2} * var(--unit) + .5px)) 0 0 / calc({DOT_EVERY} * var(--unit)) 100% }}
.kinds svg {{ height:calc({ICON_H} * var(--unit)); width:auto }}
""" + """
body { margin:0; padding:24px 16px 48px; background:var(--bg); color:var(--ink); font:14px/1.45 system-ui,"Segoe UI",sans-serif }
main { max-width:1140px; margin:0 auto }
.chart-head { display:flex; flex-wrap:wrap; align-items:center; gap:8px 14px; margin:0 0 10px }
.periods { margin-left:auto; display:inline-flex; gap:4px }
.periods button { font:inherit; font-size:12px; color:var(--ink2); background:transparent; border:1px solid var(--border);
  border-radius:6px; padding:3px 9px; cursor:pointer }
.periods button[aria-pressed=true] { color:var(--ink); background:var(--pressed); font-weight:600 }
.frozen { position:sticky; top:0; z-index:1; background:var(--bg); padding-top:8px }
.period { margin:0 } .period svg { display:block; width:100%; min-width:760px; height:auto }
.grid { fill:var(--grid) } .now { fill:var(--ink) }
.tick { fill:var(--muted); font-size:12px; text-anchor:middle } .lane { fill:var(--ink2); font-size:13px; text-anchor:end }
.place { fill:var(--ink); font-size:13px; font-weight:600; text-anchor:end }
.run .hit { fill:transparent } .run .waited { fill:var(--waiting) }
.run.passed .ran { fill:var(--passed) } .run.failed .ran { fill:var(--failed) }
.run.no-result .ran { fill:var(--noresult) } .run.going .ran { fill:var(--going) }
.run:hover .ran { filter:brightness(.8) }
.icon.passed, .stem.passed { color:var(--passed) } .icon.failed, .stem.failed { color:var(--failed) }
.icon.no-result, .stem.no-result { color:var(--noresult) } .icon.going, .stem.going { color:var(--going) }
.icon.waiting, .stem.waiting { color:var(--waiting) }
.icon.key-ink { color:var(--ink2) } .stem { fill:currentColor; opacity:.7 } .icon * { fill:currentColor }
.legend, .kinds { display:flex; flex-wrap:wrap; gap:14px; color:var(--ink2); font-size:13px }
.kinds { flex-basis:100%; gap:8px 18px }
.key { display:inline-flex; align-items:center; gap:6px }
.swatch.passed { background:var(--passed) } .swatch.failed { background:var(--failed) }
.swatch.no-result { background:var(--noresult) } .swatch.going { background:var(--going) }
pattern .dot { fill:var(--muted) }
#tip { position:fixed; z-index:2; pointer-events:none; background:var(--surface); color:var(--ink); border:1px solid var(--border);
  border-radius:8px; padding:8px 10px; font-size:12px; box-shadow:0 4px 14px rgba(0,0,0,.15); display:none; max-width:320px }
#tip strong { display:block; font-size:13px }
"""

SCRIPT = """
const tip = document.getElementById('tip');
const drawTheLegendAtTheChartsSize = () => {
  const chart = document.querySelector('figure.period:not([hidden]) svg');
  if (chart && chart.viewBox.baseVal.width) {
    document.documentElement.style.setProperty('--unit', chart.getBoundingClientRect().width / chart.viewBox.baseVal.width + 'px');
  }
};
drawTheLegendAtTheChartsSize();
addEventListener('resize', drawTheLegendAtTheChartsSize);
document.querySelectorAll('[data-tip]').forEach(run => {
  run.addEventListener('pointermove', event => {
    const [head, ...lines] = run.dataset.tip.split('|');
    tip.replaceChildren();
    const strong = document.createElement('strong');
    strong.textContent = head;
    tip.append(strong);
    for (const line of lines) {
      const row = document.createElement('div');
      row.textContent = line;
      tip.append(row);
    }
    tip.style.display = 'block';
    tip.style.left = Math.min(event.clientX + 14, innerWidth - tip.offsetWidth - 8) + 'px';
    tip.style.top = (event.clientY + 14) + 'px';
  });
  run.addEventListener('pointerleave', () => { tip.style.display = 'none'; });
});
const show = hours => {
  document.querySelectorAll('.period').forEach(period => { period.hidden = period.dataset.hours !== hours; });
  document.querySelectorAll('.periods button').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.hours === hours));
  });
  try { localStorage.setItem('line-hours', hours); } catch (error) {}
};
document.querySelectorAll('.periods button').forEach(button => {
  button.addEventListener('click', () => show(button.dataset.hours));
});
let remembered = null;
try { remembered = localStorage.getItem('line-hours'); } catch (error) {}
if (remembered && document.querySelector('.period[data-hours="' + remembered + '"]')) show(remembered);
const reloadEvery = Number(document.body.dataset.refresh || 0) * 1000;
if (reloadEvery) setInterval(() => { if (tip.style.display !== 'block') location.reload(); }, reloadEvery);
"""


def render(line: list[Run], runs: list[Run], now: datetime, refresh_s: int | None = None,
           sessions: Sessions | None = None) -> str:
    refresh = f" data-refresh={refresh_s}" if refresh_s else ""
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width, initial-scale=1'>"
            f"<title>{APP_NAME}</title><style>{STYLE}</style></head><body{refresh}><main>"
            f"{_charts_with_their_legend_and_periods(runs, line, now, sessions or Sessions())}</main>"
            f"<div id=tip></div><script>{SCRIPT}</script></body></html>")


PROJECTS = Path.home() / ".claude" / "projects"
_TRANSCRIPT_TAIL = 1 << 18


def project_dir_name(checkout: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(checkout))


def _last_title(transcript: Path) -> str | None:
    with transcript.open("rb") as handle:
        handle.seek(max(0, transcript.stat().st_size - _TRANSCRIPT_TAIL))
        tail = handle.read().decode("utf-8", errors="replace")
    titles = []
    for line in tail.splitlines():
        if '"custom-title"' not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("type") == "custom-title" and record.get("customTitle"):
            titles.append(record["customTitle"])
    return titles[-1] if titles else None


def session_names(projects: Path, worktrees: Path, checkouts: Iterable[str], sessions: Iterable[str]) -> dict[str, str]:
    newest_in = {checkout: sorted((projects / project_dir_name(worktrees / checkout)).glob("*.jsonl"),
                                  key=lambda transcript: transcript.stat().st_mtime)[-1:]
                 for checkout in set(checkouts) if checkout}
    own = {session: list(projects.glob(f"*/{session}.jsonl"))[:1] for session in set(sessions) if session}
    names = {}
    for key, transcripts in {**newest_in, **own}.items():
        title = _last_title(transcripts[0]) if transcripts else None
        if title:
            names[key] = title
    return names


CLAUDE_APP_SESSIONS = (
    (Path.home() / "AppData" / "Local" / "Packages",
     "Claude_*/LocalCache/Roaming/Claude/claude-code-sessions/*/*/local_*.json"),
    (Path.home() / "AppData" / "Roaming" / "Claude" / "claude-code-sessions", "*/*/local_*.json"),
)


@dataclass(frozen=True)
class AppSession:
    app_id: str
    claude_code_ids: tuple[str, ...]
    spawned: datetime
    archived: bool
    last_active: datetime


def _from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


def app_sessions(places: Iterable[tuple[Path, str]] = CLAUDE_APP_SESSIONS) -> list[AppSession]:
    sessions: dict[str, AppSession] = {}
    for root, pattern in places:
        for path in root.glob(pattern):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                ids = tuple(filter(None, [record.get("cliSessionId"), *record.get("priorCliSessionIds", [])]))
                session = AppSession(record["sessionId"], ids, _from_ms(record["createdAt"]),
                                     bool(record.get("isArchived")), _from_ms(record["lastActivityAt"]))
            except (OSError, ValueError, KeyError, TypeError):
                continue
            sessions.setdefault(session.app_id, session)
    return list(sessions.values())


SEEN_KEPT_FOR = timedelta(days=15)
SEEN_SESSIONS = "sessions_seen.json"


def archive_moments(seen: Path, sessions: list[AppSession], now: datetime) -> dict[str, datetime]:
    try:
        remembered = json.loads(seen.read_text(encoding="utf-8"))
        was_open = set(remembered["open"])
        moments = {app_id: datetime.fromisoformat(when) for app_id, when in remembered["archived"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        was_open, moments = set(), {}
    for session in sessions:
        if session.archived and session.app_id in was_open:
            moments.setdefault(session.app_id, now)
    moments = {app_id: when for app_id, when in moments.items() if when >= now - SEEN_KEPT_FOR}
    write_whole(seen, json.dumps({"open": sorted(session.app_id for session in sessions if not session.archived),
                                  "archived": {app_id: when.isoformat() for app_id, when in sorted(moments.items())}}))
    return moments


def lifetimes(sessions: list[AppSession], archived_at: dict[str, datetime]) -> dict[str, Lifetime]:
    spans = {}
    for session in sessions:
        ended = archived_at.get(session.app_id, session.last_active) if session.archived else None
        spans.update(dict.fromkeys(session.claude_code_ids, Lifetime(session.spawned, ended)))
    return spans


WATCH_EVERY_S = 30


def fun_time() -> Path:
    return siblings.project_dir("fun_time", haglio_settings.project_roots())


def write_the_page(page: Path, days: int, refresh_s: int | None) -> None:
    now = datetime.now().astimezone()
    since = now - timedelta(days=days)
    checkout = fun_time()
    runs = [run for run in runs_from(integration_runs.read_runs(integration_runs.runs_dir(checkout)))
            if (run.ended or now) >= since]
    names = session_names(PROJECTS, checkout / ".claude" / "worktrees", [run.checkout for run in runs],
                          [run.session for run in runs])
    page.parent.mkdir(parents=True, exist_ok=True)
    sessions = app_sessions()
    spans = lifetimes(sessions, archive_moments(page.parent / SEEN_SESSIONS, sessions, now))
    unfinished = page.with_suffix(".partial")
    unfinished.write_text(render(the_line(runs), runs, now, refresh_s, Sessions(names, spans)), encoding="utf-8")
    os.replace(unfinished, page)


def refresh_command(page: Path, days: int, refresh_s: int | None) -> list[str]:
    command = [sys.executable, str(Path(__file__).resolve()), "--out", str(page), "--days", str(days)]
    return [*command, "--refresh", str(refresh_s)] if refresh_s else command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="line_page")
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--refresh", type=int, default=None, metavar="SECONDS")
    args = parser.parse_args(argv)
    write_the_page(args.out, args.days, args.refresh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
