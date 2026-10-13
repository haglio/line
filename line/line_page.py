from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from app_support import haglio_settings, integration_runs, siblings
from app_support.integration_runs import RunRecord

_SESSION_SUFFIX = re.compile(r"-[0-9a-f]{6}$")


def who_of(branch: str) -> str:
    name = _SESSION_SUFFIX.sub("", branch.removeprefix("claude/"))
    if name in ("", "HEAD"):
        return "a session on no branch"
    return name.replace("-", " ")


_TEST_FILE = re.compile(r"test_(\w+?)(?:_integration)?(?:\.py|\b)")
ALL = "all"
SOME = "some"
NO_FLAKES = "no flakes"
AGAIN = "again"
MEASURING = "measuring"
REPEAT_CHANGED = "--repeat-changed"
WITHOUT_COVERAGE = "--no-cov"


def describe(args: list[str], kind: str) -> str:
    if kind == NO_FLAKES:
        return "its new tests, over and over"
    if kind == ALL:
        return "the whole suite"
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
    if record.checkout == VERIFYING_MAIN:
        return MEASURING if WITHOUT_COVERAGE in record.what else AGAIN
    return SOME


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


VERDICT_WORDS = {"going": "Still going", "passed": "Passed", "failed": "Failed", "no result": "No result"}
VERDICT_MARKS = {"going": "●", "passed": "✓", "failed": "✗", "no result": "?"}


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
SHORTEST_BAR = 2
SMALLEST_HIT = 10
PLACE_X = 20
LABEL_W = 320
LABEL_CHARS = 42
CHART_W = 860
RIGHT_MARGIN = 20
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
class Words:
    name: str
    text: str
    x: float
    y: float

    def svg(self) -> str:
        return f"<text class={self.name} x={self.x:.1f} y={self.y}>{self.text}</text>"


@dataclass(frozen=True)
class Icon:
    width: float
    shapes: tuple[Stick | Ring | Box | Words, ...]

    @property
    def drawing(self) -> str:
        return "".join(shape.svg() for shape in self.shapes)


def _word_in_a_box(word: str) -> Icon:
    width = 12 + 9.6 * len(word)
    return Icon(width, (Box(width, 2, 5), Words("word", word, width / 2, 18.5)))


def _snowflake_in_a_slashed_circle() -> Icon:
    middle, ring, arm, branch_at, branch, branch_angle = (ICON_H / 2, ICON_H / 2), ICON_H / 2 - 1.6, 7.4, 5, 2.9, 40

    def point(angle: float, reach: float, start: Point = middle) -> Point:
        return start[0] + math.cos(math.radians(angle)) * reach, start[1] + math.sin(math.radians(angle)) * reach

    arms = [Stick("flake", point(angle + 180, arm), point(angle, arm), 1.5) for angle in (90, 30, 150)]
    for angle in range(90, 450, 60):
        fork = point(angle, branch_at)
        arms += [Stick("flake", fork, point(angle + side * branch_angle, branch, fork), 1.5) for side in (-1, 1)]
    return Icon(ICON_H, (Ring(middle, ring, 2.5), *arms, Stick("slash", point(225, ring), point(45, ring), 2.5)))


def _mark(mark: str) -> Icon:
    return Icon(16, (Words("mark", mark, 8, 20.5),))


ICONS = {ALL: _word_in_a_box("ALL"), SOME: _word_in_a_box("SOME"), NO_FLAKES: _snowflake_in_a_slashed_circle(),
         AGAIN: _mark("!"), MEASURING: _mark("%")}
ICON_GAP = 4
KIND_WORDS = {ALL: "The whole suite", SOME: "Some of the tests", NO_FLAKES: "New tests, ten times each",
              AGAIN: "Failed tests, run again", MEASURING: "Measuring which code each test file uses"}


def icon_width(kind: str) -> float:
    return ICONS[kind].width


@dataclass(frozen=True)
class GutterIcon:
    anchor: float
    kind: str
    verdict: str
    left: float = 0.0

    @property
    def width(self) -> float:
        return icon_width(self.kind)


def _one_icon_per_pile(icons: list[GutterIcon]) -> list[GutterIcon]:
    piles: list[GutterIcon] = []
    for icon in sorted(icons, key=lambda icon: icon.anchor):
        pile = piles[-1] if piles else None
        if pile and pile.kind == icon.kind and icon.anchor < pile.anchor + pile.width + ICON_GAP:
            piles[-1] = replace(pile, verdict="failed" if "failed" in (pile.verdict, icon.verdict) else icon.verdict)
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
        verdict, half = icon.verdict.replace(" ", "-"), STEM_W / 2
        yield (f"<polygon class='stem {verdict}' points='{icon.anchor - half:.1f},{bars_bottom} "
               f"{icon.anchor + half:.1f},{bars_bottom} {icon.left + half:.1f},{icon_top} {icon.left - half:.1f},"
               f"{icon_top}'></polygon><g class='icon {icon.kind.replace(' ', '-')} {verdict}' "
               f"transform='translate({icon.left:.1f},{icon_top})'>{ICONS[icon.kind].drawing}</g>")


@dataclass(frozen=True)
class Rows:
    order: list[str]
    places: dict[str, int]
    names: dict[str, str]


def _chart(runs: list[Run], now: datetime, hours: int, rows: Rows) -> str:
    since = now - timedelta(hours=hours)
    shown = [run for run in runs if (run.ended or now) >= since]
    row_of = {id(run): row_name(run, rows.names) for run in shown}
    lanes = [row for row in rows.order if row in row_of.values()]
    height = 30 + LANE_H * len(lanes)

    def x(when: datetime) -> float:
        return LABEL_W + CHART_W * max(0.0, min(1.0, (when - since) / (now - since)))

    starts = sorted(x(run.got_the_machine) for run in shown if run.got_the_machine)

    def held_width(left: float, end: datetime) -> float:
        following = next((other for other in starts if other > left), x(now))
        return min(max(x(end) - left, SHORTEST_BAR), following - left)

    parts = []
    for tick in _ticks(since, now, GRID_EVERY_MIN[hours]):
        parts.append(f"<rect class=grid x={x(tick) - 0.5:.1f} y=20 width=1 height={height - 20}></rect>")
        if (tick.hour * 60 + tick.minute) % LABEL_EVERY_MIN[hours] == 0:
            parts.append(f"<text class=tick x={x(tick):.1f} y=12>{tick.strftime('%H:%M')}</text>")
    for index, row in enumerate(lanes):
        middle = 30 + LANE_H * index + BARS_H / 2
        if row in rows.places:
            parts.append(f"<text class=place x={PLACE_X} y={middle + 4.5}>{rows.places[row]}</text>")
        fitted = row if len(row) <= LABEL_CHARS else row[:LABEL_CHARS - 1].rstrip() + "…"
        note = ROW_NOTES.get(row) or (row if fitted != row else "")
        title = f"<title>{_e(note)}</title>" if note else ""
        parts.append(f"<text class=lane x={LABEL_W - 10} y={middle + 4.5}>{_e(fitted)}{title}</text>")
    icons_by_lane: dict[float, list[GutterIcon]] = {}
    for run in shown:
        top = 30 + LANE_H * lanes.index(row_of[id(run)])
        middle = top + BARS_H / 2
        end = run.ended or now
        left, right = x(run.joined), max(x(end), x(run.joined) + 3)
        hit_left = left - max(0.0, SMALLEST_HIT - (right - left)) / 2
        split = min(right, x(run.got_the_machine)) if run.got_the_machine else right
        ran = run.ran_s(now)
        tip = _tip(VERDICT_WORDS[run.verdict], [
            row_of[id(run)], run.what,
            f"Joined {_clock(run.joined, now)}, ended {_clock(end, now) if run.ended else 'now'}",
            f"Waited {_span(run.waited_s(now))}, ran "
            + (_span(ran) if ran is not None else "never" if run.ended else "not yet")])
        parts.append(f"<g class='run {run.verdict.replace(' ', '-')}' data-tip='{tip}'>"
                     f"<rect class=hit x={hit_left:.1f} y={top} width={max(right - left, SMALLEST_HIT):.1f} "
                     f"height={BARS_H}></rect>"
                     + (f"<rect class=waited x={left:.1f} y={middle - WAITED_H / 2} width={split - left:.1f} "
                        f"height={WAITED_H} rx={WAITED_H / 2}></rect>" if split > left else "")
                     + (f"<rect class=ran x={split:.1f} y={middle - RAN_H / 2} width={held_width(split, end):.1f} "
                        f"height={RAN_H} rx=5></rect>" if run.got_the_machine else "")
                     + "</g>")
        icons_by_lane.setdefault(top, []).append(GutterIcon(split, run.kind, run.verdict))
    for top, icons in icons_by_lane.items():
        parts.extend(_gutter(top, icons))
    parts.append(f"<rect class=now x={x(now) - 0.75:.2f} y=16 width=1.5 height={height - 16}></rect>")
    return (f"<svg viewBox='0 0 {LABEL_W + CHART_W + RIGHT_MARGIN} {height + 6}' role=img "
            f"aria-label='Integration runs in the last {_hours_words(hours)}'>{''.join(parts)}</svg>")


def _charts_with_their_legend_and_periods(runs: list[Run], line: list[Run], now: datetime,
                                          names: dict[str, str]) -> str:
    rows = Rows(rows_in_line_order(runs, line, now, names),
                {row: place for place, row in enumerate(dict.fromkeys(row_name(entry, names) for entry in line),
                                                        start=1)},
                names)
    buttons = "".join(f"<button type=button data-hours={hours} aria-pressed={str(hours == SHOWN_FIRST_H).lower()}>"
                      f"{hours} h</button>" for hours in PERIODS_H)
    charts = "".join(f"<figure class=period data-hours={hours}{'' if hours == SHOWN_FIRST_H else ' hidden'}>"
                     f"{_chart(runs, now, hours, rows)}</figure>" for hours in PERIODS_H)
    legend = "".join(f"<span class=key><span class='swatch {verdict.replace(' ', '-')}'></span>"
                     f"{VERDICT_MARKS[verdict]} {word}</span>" for verdict, word in VERDICT_WORDS.items())
    kinds = "".join(f"<span class=key><svg viewBox='0 0 {icon_width(kind):.1f} {ICON_H}' "
                    f"width={icon_width(kind):.1f} height={ICON_H}><g class='icon {kind.replace(' ', '-')} key-ink'>"
                    f"{ICONS[kind].drawing}</g></svg>{words}</span>" for kind, words in KIND_WORDS.items())
    return (f"<div class=chart-head><div class=legend>{legend}<span class=key><span class='swatch waited-key'></span>"
            f"Waiting in line</span></div><div class=periods>{buttons}</div><div class=kinds>{kinds}</div></div>"
            f"{charts}")


DARK_BACKGROUND = "#0d0d0d"
STYLE = f"""
:root {{ --bg:{DARK_BACKGROUND}; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781; --grid:#2c2c2a;
  --border:rgba(255,255,255,.10); --passed:#0ca30c; --failed:#d03b3b; --noresult:#f0c93d; --going:#3987e5;
  --pressed:#2c2c2a; color-scheme:dark }}
""" + """
body { margin:0; padding:24px 16px 48px; background:var(--bg); color:var(--ink); font:14px/1.45 system-ui,"Segoe UI",sans-serif }
main { max-width:1140px; margin:0 auto }
.chart-head { display:flex; flex-wrap:wrap; align-items:center; gap:8px 14px; margin:0 0 10px }
.periods { margin-left:auto; display:inline-flex; gap:4px }
.periods button { font:inherit; font-size:12px; color:var(--ink2); background:transparent; border:1px solid var(--border);
  border-radius:6px; padding:3px 9px; cursor:pointer }
.periods button[aria-pressed=true] { color:var(--ink); background:var(--pressed); font-weight:600 }
.period { margin:0; overflow-x:auto } .period svg { display:block; width:100%; min-width:760px; height:auto }
.grid { fill:var(--grid) } .now { fill:var(--ink) }
.tick { fill:var(--muted); font-size:12px; text-anchor:middle } .lane { fill:var(--ink2); font-size:13px; text-anchor:end }
.place { fill:var(--ink); font-size:13px; font-weight:600; text-anchor:end }
.run .hit { fill:transparent } .run .waited { fill:var(--muted); opacity:.45 }
.run.passed .ran { fill:var(--passed) } .run.failed .ran { fill:var(--failed) }
.run.no-result .ran { fill:var(--noresult) } .run.going .ran { fill:var(--going) }
.run:hover .ran { opacity:.75 }
.icon.passed, .stem.passed { color:var(--passed) } .icon.failed, .stem.failed { color:var(--failed) }
.icon.no-result, .stem.no-result { color:var(--noresult) } .icon.going, .stem.going { color:var(--going) }
.icon.key-ink { color:var(--ink2) } .stem { fill:currentColor; opacity:.7 } .icon * { fill:currentColor }
.icon .word { font-size:14px; font-weight:700; letter-spacing:.5px; text-anchor:middle }
.icon .mark { font-size:21px; font-weight:800; text-anchor:middle }
.legend, .kinds { display:flex; flex-wrap:wrap; gap:14px; color:var(--ink2); font-size:13px }
.kinds { flex-basis:100%; gap:8px 18px }
.key { display:inline-flex; align-items:center; gap:6px }
.swatch { display:inline-block; width:14px; height:8px; border-radius:3px }
.swatch.passed { background:var(--passed) } .swatch.failed { background:var(--failed) }
.swatch.no-result { background:var(--noresult) } .swatch.going { background:var(--going) }
.swatch.waited-key { background:var(--muted); opacity:.45; height:4px }
#tip { position:fixed; pointer-events:none; background:var(--surface); color:var(--ink); border:1px solid var(--border);
  border-radius:8px; padding:8px 10px; font-size:12px; box-shadow:0 4px 14px rgba(0,0,0,.15); display:none; max-width:320px }
#tip strong { display:block; font-size:13px }
"""

SCRIPT = """
const tip = document.getElementById('tip');
document.querySelectorAll('.run').forEach(run => {
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
           names: dict[str, str] | None = None) -> str:
    refresh = f" data-refresh={refresh_s}" if refresh_s else ""
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width, initial-scale=1'>"
            f"<title>{APP_NAME}</title><style>{STYLE}</style></head><body{refresh}><main>"
            f"{_charts_with_their_legend_and_periods(runs, line, now, names or {})}</main>"
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
    unfinished = page.with_suffix(".partial")
    unfinished.write_text(render(the_line(runs), runs, now, refresh_s, names), encoding="utf-8")
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
