from __future__ import annotations

import argparse
import ast
import ctypes
import html
import json
import os
import re
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from ctypes import wintypes
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psutil

RUNNER = "tests.integration.hidden_desktop"
SESSION_VARIABLE = "CLAUDE_CODE_SESSION_ID"
_MODULE_RUN = re.compile(r"-m\s+" + re.escape(RUNNER) + r"(?P<args>[^;&|<>\n\"')]*)")
_RUNPY_ARGV = re.compile(r"sys\.argv\s*=\s*(?P<argv>\[[^\]]*\])")


def runner_invocations(command: str) -> list[list[str]]:
    found = [(match.start(), [arg.strip("\"'") for arg in match["args"].split()])
             for match in _MODULE_RUN.finditer(command)]
    for match in _RUNPY_ARGV.finditer(command):
        if RUNNER in command[match.end():]:
            try:
                argv = ast.literal_eval(match["argv"])
            except (ValueError, SyntaxError):
                continue
            found.append((match.start(), [str(arg) for arg in argv[1:]]))
    return [args for _, args in sorted(found)]


_WAITED = re.compile(r"another integration run holds .*?\((\d+)s elapsed\)")
_SUMMARY = re.compile(r"^=*\s*(?P<counts>\d+ \w+.*?) in (?P<seconds>[\d.]+)s\b", re.MULTILINE)
_COUNT = re.compile(r"(\d+) (passed|failed|errors?)\b")


@dataclass(frozen=True)
class Outcome:
    waited_s: float | None = None
    ran_s: float | None = None
    passed: int = 0
    failed: int = 0

    @property
    def verdict(self) -> str:
        if self.ran_s is None:
            return "no result"
        return "failed" if self.failed else "passed"


def read_output(text: str) -> Outcome:
    waits = [int(seconds) for seconds in _WAITED.findall(text)]
    summaries = list(_SUMMARY.finditer(text))
    if not summaries:
        return Outcome(waited_s=max(waits, default=None))
    last = summaries[-1]
    counts = {kind: int(number) for number, kind in _COUNT.findall(last["counts"])}
    failed = counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0)
    return Outcome(waited_s=max(waits, default=None), ran_s=float(last["seconds"]),
                   passed=counts.get("passed", 0), failed=failed)


_SESSION_SUFFIX = re.compile(r"-[0-9a-f]{6}$")


def who_of(branch: str) -> str:
    name = _SESSION_SUFFIX.sub("", branch.removeprefix("claude/"))
    if name in ("", "HEAD"):
        return "a session on no branch"
    return name.replace("-", " ")


_TEST_FILE = re.compile(r"test_(\w+?)(?:_integration)?(?:\.py|\b)")


def describe(args: list[str]) -> str:
    if "--repeat-changed" in args:
        return "its new tests, over and over"
    if "--changed" in args:
        return "what its change needs"
    named = [arg for arg in args if not arg.startswith("-")]
    files = {match[1] for arg in named for match in _TEST_FILE.finditer(arg.split("::")[0])}
    if not files:
        return "the whole suite"
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
    outcome: Outcome = field(default_factory=Outcome)
    exit_code: int | None = None
    got_the_machine: datetime | None = None
    checkout: str = ""
    session: str = ""

    def waited_s(self, now: datetime) -> float:
        return ((self.got_the_machine or self.ended or now) - self.joined).total_seconds()

    def ran_s(self, now: datetime) -> float | None:
        if self.outcome.ran_s is not None:
            return self.outcome.ran_s
        return ((self.ended or now) - self.got_the_machine).total_seconds() if self.got_the_machine else None

    @property
    def verdict(self) -> str:
        if self.ended is None:
            return "going"
        if self.outcome.ran_s is not None:
            return self.outcome.verdict
        if self.exit_code is None or self.got_the_machine is None:
            return "no result"
        return "passed" if self.exit_code == 0 else "failed"


@dataclass(frozen=True)
class Process:
    pid: int
    parent: int
    command: str
    started: float
    cwd: str | None
    session: str | None = None


@dataclass(frozen=True)
class InLine:
    who: str
    what: str
    since: datetime
    short: bool
    got_the_machine: datetime | None = None
    checkout: str = ""
    session: str = ""

    @property
    def holding(self) -> bool:
        return self.got_the_machine is not None

    def as_run(self) -> Run:
        return Run(self.who, self.what, self.since, got_the_machine=self.got_the_machine, checkout=self.checkout,
                   session=self.session)


def _goes_as_a_short_run(args: list[str]) -> bool:
    if "--repeat-changed" in args:
        return True
    return "--changed" not in args and any(not arg.startswith("-") for arg in args)


def _who_runs_from(cwd: str | None, branch_of: Callable[[str], str | None]) -> str:
    if cwd and Path(cwd).name == "verifying-main":
        return "main verifier"
    return who_of((branch_of(cwd) if cwd else None) or "")


def the_line_now(processes: Iterable[Process], branch_of: Callable[[str], str | None]) -> list[InLine]:
    processes = list(processes)
    children = defaultdict(list)
    for process in processes:
        children[process.parent].append(process)

    def tests_started_under(pid: int) -> datetime | None:
        waiting = list(children[pid])
        while waiting:
            child = waiting.pop()
            if "pytest" in child.command and RUNNER not in child.command:
                return datetime.fromtimestamp(child.started, UTC)
            waiting.extend(children[child.pid])
        return None

    line = []
    for process in processes:
        if RUNNER not in process.command or any(RUNNER in child.command for child in children[process.pid]):
            continue
        args = next(iter(runner_invocations(process.command)), [])
        got = tests_started_under(process.pid)
        line.append(InLine(_who_runs_from(process.cwd, branch_of), describe(args),
                           datetime.fromtimestamp(process.started, UTC), _goes_as_a_short_run(args), got,
                           Path(process.cwd).name if process.cwd else "", process.session or ""))
    return sorted(line, key=lambda entry: (not entry.holding, not entry.short, entry.since))


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("pid", wintypes.DWORD),
                ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                ("parent", wintypes.DWORD), ("priority", ctypes.c_long), ("flags", wintypes.DWORD),
                ("exe", ctypes.c_wchar * 260)]


def _pythons_and_their_parents() -> list[tuple[int, int]]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    entry = _ProcessEntry(size=ctypes.sizeof(_ProcessEntry))
    found = []
    try:
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.exe.lower().startswith("python"):
                found.append((entry.pid, entry.parent))
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def _session_of(process: psutil.Process) -> str | None:
    try:
        return process.environ().get(SESSION_VARIABLE)
    except (psutil.Error, OSError):
        return None


def processes_now() -> list[Process]:
    found = []
    for pid, parent in _pythons_and_their_parents():
        try:
            process = psutil.Process(pid)
            command = " ".join(process.cmdline())
            runner = RUNNER in command
            found.append(Process(pid, parent, command, process.create_time(), process.cwd() if runner else None,
                                 _session_of(process) if runner else None))
        except (psutil.Error, OSError):
            continue
    return found


def branch_of(checkout: str) -> str | None:
    marker = Path(checkout) / ".git"
    try:
        git_dir = Path(marker.read_text(encoding="utf-8").removeprefix("gitdir:").strip()) if marker.is_file() else marker
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return head.removeprefix("ref: refs/heads/") if head.startswith("ref: ") else "HEAD"


_COLLECTED = re.compile(r"collected (\d+) items?")
_TESTS_STARTED = "test session starts"
FEWEST_TESTS_IN_THE_WHOLE_SUITE = 100


def _what_the_verifier_ran(text: str) -> str:
    counts = [int(count) for count in _COLLECTED.findall(text)]
    if not counts:
        return "some tests" if "this short run waits" in text else "the whole suite"
    if counts[-1] >= FEWEST_TESTS_IN_THE_WHOLE_SUITE:
        return "the whole suite"
    return "1 test" if counts[-1] == 1 else f"{counts[-1]} tests"


def verifier_runs(runs_dir: Path) -> list[Run]:
    runs = []
    for path in sorted(runs_dir.glob("*.txt")):
        try:
            joined = datetime.strptime(path.name[:15], "%Y%m%d-%H%M%S").astimezone()
            text = path.read_text(encoding="utf-8", errors="replace")
            last_written = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        except (ValueError, OSError):
            continue
        outcome = read_output(text)
        got = joined + timedelta(seconds=outcome.waited_s or 0) if _TESTS_STARTED in text else None
        runs.append(Run("main verifier", _what_the_verifier_ran(text), joined, last_written, outcome,
                        got_the_machine=got, checkout="verifying-main"))
    return runs


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


LANE_H = 22
SHORTEST_BAR = 2
SMALLEST_HIT = 10
PLACE_X = 20
LABEL_W = 320
LABEL_CHARS = 42
CHART_W = 860
PERIODS_H = (1, 4, 12, 24)
SHOWN_FIRST_H = 4
GRID_EVERY_MIN = {1: 10, 4: 30, 12: 60, 24: 60}
LABEL_EVERY_MIN = {1: 10, 4: 60, 12: 120, 24: 180}
APP_NAME = "Line"
VERIFIER = "main verifier"
VERIFIER_ROW = "MAIN VERIFIER"
ROW_NOTES = {VERIFIER_ROW: "Runs the whole integration suite on each change that ships to main. Production Fun Time "
                           "moves to a change only once that run passes."}


def row_name(run: Run | InLine, names: dict[str, str]) -> str:
    if run.who == VERIFIER:
        return VERIFIER_ROW
    return names.get(run.session) or names.get(run.checkout) or run.who


def rows_in_line_order(runs: list[Run], line: list[InLine], now: datetime, names: dict[str, str]) -> list[str]:
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
        y = 30 + LANE_H * index
        if row in rows.places:
            parts.append(f"<text class=place x={PLACE_X} y={y + 11}>{rows.places[row]}</text>")
        fitted = row if len(row) <= LABEL_CHARS else row[:LABEL_CHARS - 1].rstrip() + "…"
        note = ROW_NOTES.get(row) or (row if fitted != row else "")
        title = f"<title>{_e(note)}</title>" if note else ""
        parts.append(f"<text class=lane x={LABEL_W - 10} y={y + 11}>{_e(fitted)}{title}</text>")
    for run in shown:
        y = 30 + LANE_H * lanes.index(row_of[id(run)]) + 3
        end = run.ended or now
        left, right = x(run.joined), max(x(end), x(run.joined) + 3)
        hit_left = left - max(0.0, SMALLEST_HIT - (right - left)) / 2
        split = min(right, x(run.got_the_machine)) if run.got_the_machine else right
        ran = run.ran_s(now)
        counts = f" — {run.outcome.passed} passed, {run.outcome.failed} failed" if run.outcome.ran_s else ""
        tip = _tip(VERDICT_WORDS[run.verdict] + counts, [
            row_of[id(run)], run.what,
            f"Joined {_clock(run.joined, now)}, ended {_clock(end, now) if run.ended else 'now'}",
            f"Waited {_span(run.waited_s(now))}, ran "
            + (_span(ran) if ran is not None else "never" if run.ended else "not yet")])
        parts.append(f"<g class='run {run.verdict.replace(' ', '-')}' data-tip='{tip}'>"
                     f"<rect class=hit x={hit_left:.1f} y={y - 3} width={max(right - left, SMALLEST_HIT):.1f} "
                     f"height={LANE_H - 2}></rect>"
                     + (f"<rect class=waited x={left:.1f} y={y + 3} width={split - left:.1f} height=4 rx=2></rect>"
                        if split > left else "")
                     + (f"<rect class=ran x={split:.1f} y={y} width={held_width(split, end):.1f} height=10 rx=3>"
                        "</rect>" if run.got_the_machine else "")
                     + "</g>")
    parts.append(f"<rect class=now x={x(now) - 0.75:.2f} y=16 width=1.5 height={height - 16}></rect>")
    return (f"<svg viewBox='0 0 {LABEL_W + CHART_W + 20} {height + 6}' role=img "
            f"aria-label='Integration runs in the last {_hours_words(hours)}'>{''.join(parts)}</svg>")


def _charts_with_their_legend_and_periods(runs: list[Run], line: list[InLine], now: datetime,
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
    return (f"<div class=chart-head><div class=legend>{legend}<span class=key><span class='swatch waited-key'></span>"
            f"Waiting in line</span></div><div class=periods>{buttons}</div></div>{charts}")


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
.period { margin:0; overflow-x:auto } svg { display:block; width:100%; min-width:760px; height:auto }
.grid { fill:var(--grid) } .now { fill:var(--ink) }
.tick { fill:var(--muted); font-size:11px; text-anchor:middle } .lane { fill:var(--ink2); font-size:12px; text-anchor:end }
.place { fill:var(--ink); font-size:12px; font-weight:600; text-anchor:end }
.run .hit { fill:transparent } .run .waited { fill:var(--muted); opacity:.45 }
.run.passed .ran { fill:var(--passed) } .run.failed .ran { fill:var(--failed) }
.run.no-result .ran { fill:var(--noresult) } .run.going .ran { fill:var(--going) }
.run:hover .ran { opacity:.75 }
.legend { display:flex; flex-wrap:wrap; gap:14px; color:var(--ink2); font-size:12px }
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


def render(line: list[InLine], runs: list[Run], now: datetime, refresh_s: int | None = None,
           names: dict[str, str] | None = None) -> str:
    refresh = f" data-refresh={refresh_s}" if refresh_s else ""
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width, initial-scale=1'>"
            f"<title>{APP_NAME}</title><style>{STYLE}</style></head><body{refresh}><main>"
            f"{_charts_with_their_legend_and_periods(runs, line, now, names or {})}</main>"
            f"<div id=tip></div><script>{SCRIPT}</script></body></html>")


SAME_RUN = timedelta(minutes=1)


def _record_time(record: dict, key: str) -> datetime | None:
    return datetime.fromisoformat(record[key]) if record.get(key) else None


def line_records(records_dir: Path, alive: Callable[[int], bool] = psutil.pid_exists) -> list[Run]:
    runs = []
    for path in records_dir.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        joined, got, ended = (_record_time(record, key) for key in ("joined", "got_the_machine", "ended"))
        if ended is None and not alive(record.get("pid", 0)):
            continue
        who = "main verifier" if record.get("checkout") == "verifying-main" else who_of(record.get("branch", ""))
        runs.append(Run(who, describe(record.get("what", [])), joined, ended, exit_code=record.get("exit_code"),
                        got_the_machine=got, checkout=record.get("checkout", ""), session=record.get("session", "")))
    return sorted(runs, key=lambda run: run.joined)


def merged_runs(recorded: list[Run], read_elsewhere: list[Run]) -> list[Run]:
    def recorded_already(run: Run) -> bool:
        return any(run.who == mine.who and abs(run.joined - mine.joined) <= SAME_RUN for mine in recorded)

    return [*recorded, *(run for run in read_elsewhere if not recorded_already(run))]


def timeline_runs(runs: list[Run], line: list[InLine]) -> list[Run]:
    unmatched = list(line)
    kept = []
    for run in sorted(runs, key=lambda run: run.joined):
        same = [entry for entry in unmatched if entry.who == run.who and abs(entry.since - run.joined) <= SAME_RUN]
        match = min(same, key=lambda entry: abs(entry.since - run.joined), default=None)
        if match is not None and run.verdict in ("going", "no result"):
            unmatched.remove(match)
            kept.append(replace(run, ended=None, got_the_machine=run.got_the_machine or match.got_the_machine))
        elif run.ended is not None:
            kept.append(run)
    kept.extend(entry.as_run() for entry in unmatched)
    return sorted(kept, key=lambda run: run.joined)


FUN_TIME = Path.home() / "workspace" / "haglio" / "fun_time"
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


def write_the_page(page: Path, days: int, refresh_s: int | None) -> None:
    now = datetime.now().astimezone()
    line = the_line_now(processes_now(), branch_of)
    since = now - timedelta(days=days)
    recorded = [run for run in line_records(FUN_TIME / "state" / "integration_runs") if (run.ended or now) >= since]
    verified = [run for run in verifier_runs(FUN_TIME / "state" / "main_verifier_runs") if (run.ended or now) >= since]
    runs = timeline_runs(merged_runs(recorded, verified), line)
    page.parent.mkdir(parents=True, exist_ok=True)
    unfinished = page.with_suffix(".partial")
    names = session_names(PROJECTS, FUN_TIME / ".claude" / "worktrees", [run.checkout for run in runs],
                          [run.session for run in runs])
    unfinished.write_text(render(line, runs, now, refresh_s, names), encoding="utf-8")
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
