from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app_support import haglio_settings, main_verifier_record, siblings
from app_support.file_channel import write_whole

LONGEST_DELIVERY = timedelta(hours=24)
ONE_DELIVERY_SPREAD = timedelta(hours=6)
PULL_REQUESTS_PER_QUERY = 40
GO_AHEAD = re.compile(
    r"\b(ship|land|merge)\s+(it|this|them|that|both|all)\b|^\W*(ok(ay)?\W+|yes\W+|good\W+)*ship\W*$",
    re.IGNORECASE | re.MULTILINE)
PULL_REQUEST = re.compile(r"https://github\.com/haglio/([\w.-]+)/pull/(\d+)")
SOMETHING_BROKE = re.compile(
    r"\b(crash\w*|broke\w*|bug\w*|froze|freez\w*|stuck|h[au]ngs?|errors?|fail\w*|doesn'?t work|does not work"
    r"|not working|isn'?t working|stopped working|won'?t (start|open|play|load))\b", re.IGNORECASE)
AGENT_BRIEF = re.compile(r"\b(agent spawned|spawned this task|this brief|claude agent)\b", re.IGNORECASE)
SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)


@dataclass
class Session:
    accepted: list[datetime] = field(default_factory=list)
    opened: set[tuple[str, int]] = field(default_factory=set)
    first_words: str | None = None
    started: datetime | None = None

    @property
    def reports_a_bug(self) -> bool:
        return (self.first_words is not None and not AGENT_BRIEF.search(self.first_words)
                and bool(SOMETHING_BROKE.search(own_words(self.first_words))))


def _when(record: dict) -> datetime:
    return datetime.fromisoformat(record["timestamp"])


def own_words(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))


def deliveries(accepted: Iterable[datetime], merges: Iterable[datetime]) -> list[tuple[datetime, timedelta]]:
    merges = sorted(merges)
    pending = sorted(accepted)
    found = []
    while pending:
        start = pending[0]
        after = [merge for merge in merges if start < merge <= start + LONGEST_DELIVERY]
        if not after:
            pending = pending[1:]
            continue
        first_merge = after[0]
        pending = [go_ahead for go_ahead in pending if go_ahead > first_merge]
        same_delivery_until = min([first_merge + ONE_DELIVERY_SPREAD, *pending[:1]])
        found.append((start, max(merge for merge in after if merge <= same_delivery_until) - start))
    return found


def _week(when: datetime, zone) -> str:
    year, week, _ = when.astimezone(zone).isocalendar()
    return f"{year}-W{week:02d}"


def _slowest_tenth(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(0.9 * len(ordered)) - 1]


def weekly(deliveries: Iterable[tuple[datetime, timedelta]], *, bug_reports: Iterable[datetime],
           crashes: Iterable[datetime], zone=None) -> list[dict]:
    took = defaultdict(list)
    for accepted, duration in deliveries:
        took[_week(accepted, zone)].append(duration.total_seconds() / 60)
    reported = Counter(_week(when, zone) for when in bug_reports)
    crashed = Counter(_week(when, zone) for when in crashes)
    rows = []
    for week in sorted(set(took) | set(reported) | set(crashed)):
        minutes = took.get(week, [])
        rows.append({"week": week, "deliveries": len(minutes),
                     "median_minutes": round(statistics.median(minutes)) if minutes else None,
                     "slowest_tenth_minutes": round(_slowest_tenth(minutes)) if minutes else None,
                     "bug_reports": reported[week], "crashes": crashed[week]})
    return rows


def family_crashes(windows_log: str) -> list[datetime]:
    crashes = []
    for event in windows_log.split("Event[")[1:]:
        source = re.search(r"Source: (.+)", event)
        date = re.search(r"Date: (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", event)
        application = re.search(r"Faulting application name: ([^,\r\n]+)", event)
        if not (source and date and application) or source.group(1).rstrip() != "Application Error":
            continue
        name = application.group(1).rstrip()
        if name == "pythonw.exe" or name.startswith("FunTime-"):
            crashes.append(datetime.fromisoformat(date.group(1)).replace(tzinfo=UTC))
    return crashes


class GitHubDidNotAnswer(Exception):
    pass


def _ask_github(fields: str, run) -> dict:
    answer = run(["gh", "api", "graphql", "-f", f"query={{{fields}}}"], capture_output=True, text=True)
    if answer.returncode:
        raise GitHubDidNotAnswer(answer.stderr)
    return json.loads(answer.stdout or "{}").get("data") or {}


def merge_times(pull_requests: Iterable[tuple[str, int]], run=subprocess.run) -> dict[tuple[str, int], datetime]:
    asked = sorted(pull_requests)
    merged = {}
    for start in range(0, len(asked), PULL_REQUESTS_PER_QUERY):
        batch = asked[start:start + PULL_REQUESTS_PER_QUERY]
        fields = " ".join(f'p{i}: repository(owner:"haglio",name:"{repo}"){{pullRequest(number:{number}){{mergedAt}}}}'
                          for i, (repo, number) in enumerate(batch))
        data = _ask_github(fields, run)
        for i, pull_request in enumerate(batch):
            merged_at = ((data.get(f"p{i}") or {}).get("pullRequest") or {}).get("mergedAt")
            if merged_at:
                merged[pull_request] = datetime.fromisoformat(merged_at)
    return merged


def _blocks(record: dict) -> list[dict] | None:
    content = (record.get("message") or {}).get("content")
    return [block for block in content if isinstance(block, dict)] if isinstance(content, list) else None


def his_text(record: dict) -> str | None:
    if record.get("type") != "user" or record.get("isMeta") or record.get("isCompactSummary"):
        return None
    content = (record.get("message") or {}).get("content")
    blocks = _blocks(record)
    if blocks is not None:
        if any(block.get("type") == "tool_result" for block in blocks):
            return None
        content = "\n".join(block.get("text", "") for block in blocks if block.get("type") == "text")
    words = SYSTEM_REMINDER.sub("", content) if isinstance(content, str) else ""
    opening = words.lstrip()
    if not opening or opening.startswith("<"):
        return None
    return words


def _records(lines: Iterable[str]) -> Iterable[dict]:
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("timestamp"):
            yield record


def read_session(lines: Iterable[str]) -> Session:
    session = Session()
    pr_creations = set()
    for record in _records(lines):
        blocks = _blocks(record) or []
        if record.get("type") == "assistant":
            pr_creations |= {block.get("id") for block in blocks
                             if block.get("type") == "tool_use"
                             and "gh pr create" in str((block.get("input") or {}).get("command", ""))}
            continue
        for block in blocks:
            if block.get("type") == "tool_result" and block.get("tool_use_id") in pr_creations:
                session.opened |= {(repo, int(number))
                                   for repo, number in PULL_REQUEST.findall(json.dumps(block.get("content")))}
        text = his_text(record)
        if text is None:
            continue
        if session.first_words is None:
            session.first_words, session.started = text, _when(record)
        if GO_AHEAD.search(own_words(text)):
            session.accepted.append(_when(record))
    return session


def _sessions_since(since: datetime, projects: Path) -> list[Session]:
    sessions = []
    for path in projects.glob("*haglio*/*.jsonl"):
        if datetime.fromtimestamp(path.stat().st_mtime, UTC) >= since:
            with path.open(encoding="utf-8", errors="replace") as handle:
                sessions.append(read_session(handle))
    return sessions


def _windows_crashes(since: datetime, run, now: datetime) -> list[datetime]:
    window_ms = int((now - since).total_seconds() * 1000)
    query = f"*[System[(EventID=1000) and TimeCreated[timediff(@SystemTime) <= {window_ms}]]]"
    log = run(["wevtutil", "qe", "Application", f"/q:{query}", "/f:text"], capture_output=True, text=True,
              errors="replace").stdout
    return [crash for crash in family_crashes(log) if crash >= since]


def _start_of_week(weeks_back: int, now: datetime) -> datetime:
    today = now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=today.weekday(), weeks=weeks_back - 1)


def measure(weeks: int, *, projects: Path, run=subprocess.run, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(UTC)
    since = _start_of_week(weeks, now)
    sessions = _sessions_since(since, projects)
    merged = merge_times({pull_request for session in sessions for pull_request in session.opened}, run)
    found = [delivery for session in sessions
             for delivery in deliveries(session.accepted, [merged[pr] for pr in session.opened if pr in merged])
             if delivery[0] >= since]
    bug_reports = [session.started for session in sessions if session.reports_a_bug and session.started >= since]
    return weekly(found, bug_reports=bug_reports, crashes=_windows_crashes(since, run, now))


FUN_TIME_ON_GITHUB = 'repository(owner:"haglio",name:"fun_time")'
REVERTS = "This reverts commit "


def _commit_reverting(commit: str, fun_time: Path, run) -> str | None:
    answer = run(["git", "-C", str(fun_time), "log", "origin/main", "--format=%H", "--grep", f"{REVERTS}{commit}"],
                 capture_output=True, text=True)
    found = answer.stdout.split()
    return found[-1] if found else None


def _brought_back_by_a_revert(commit: str, fun_time: Path, run) -> bool:
    take_back = _commit_reverting(commit, fun_time, run)
    return take_back is not None and _commit_reverting(take_back, fun_time, run) is not None


def not_shipped_again(incidents: list[dict], fun_time: Path, run=subprocess.run) -> list[dict]:
    already_back = main_verifier_record.shipped_again(incidents)
    taken_back = {incident["pull_request"]: incident for incident in incidents
                  if incident.get("taken_back_by") and incident.get("pull_request") not in already_back}
    if not taken_back:
        return []
    fields = " ".join(
        f"p{number}: {FUN_TIME_ON_GITHUB}{{pullRequest(number:{number}){{title}}}} "
        f"b{number}: {FUN_TIME_ON_GITHUB}{{pullRequest(number:{incident['taken_back_by']}){{mergedAt}}}} "
        f'a{number}: search(query:"repo:haglio/fun_time is:pr is:merged in:title \\"Ship #{number} again\\"",'
        f"type:ISSUE,first:1){{issueCount}}"
        for number, incident in taken_back.items())
    data = _ask_github(fields, run)
    return [{"pull_request": number, "title": data[f"p{number}"]["pullRequest"]["title"], "taken_back": incident["at"]}
            for number, incident in taken_back.items()
            if data[f"b{number}"]["pullRequest"]["mergedAt"] and not data[f"a{number}"]["issueCount"]
            and not _brought_back_by_a_revert(incident["commit"], fun_time, run)]


RECORD_NAME = "ship_time.json"
WEEKS_MEASURED_THE_FIRST_TIME = 26
WEEKS_MEASURED_AFTER_THAT = 2


@dataclass(frozen=True)
class ShipTimeRecord:
    weeks: list[dict]
    taken_back: list[dict]


def read_the_record(path: Path) -> ShipTimeRecord | None:
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
        return ShipTimeRecord(sorted(kept["weeks"].values(), key=lambda row: row["week"]), kept["taken_back"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def update_the_record(path: Path, *, projects: Path, fun_time: Path, run=subprocess.run,
                      now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    try:
        weeks = json.loads(path.read_text(encoding="utf-8"))["weeks"]
    except (OSError, ValueError, KeyError, TypeError):
        weeks = {}
    how_many = WEEKS_MEASURED_AFTER_THAT if weeks else WEEKS_MEASURED_THE_FIRST_TIME
    for row in measure(how_many, projects=projects, run=run, now=now):
        weeks[row["week"]] = {**row, "measured_at": now.isoformat(timespec="seconds")}
    incidents = main_verifier_record.read_incidents(fun_time / "state")
    write_whole(path, json.dumps({"weeks": weeks, "taken_back": not_shipped_again(incidents, fun_time, run)}, indent=1))


def main(argv: list[str] | None = None, update: Callable[..., None] = update_the_record) -> int:
    parser = argparse.ArgumentParser(prog="ship_time")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        update(args.out, projects=Path.home() / ".claude" / "projects",
               fun_time=siblings.project_dir("fun_time", haglio_settings.project_roots()))
    except GitHubDidNotAnswer:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
