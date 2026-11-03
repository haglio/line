from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from app_support import claude_sessions, haglio_settings, siblings
from app_support.claude_sessions import CommandFailed, NewSession
from app_support.file_channel import write_whole

from line.ship_time import RECORD_NAME, read_the_record

SESSIONS_NAME = "ship_time_sessions.json"
ASKED_AT = "https://line-work.invalid/"
FINISHED_STATES = frozenset({"blocked", "done", "failed"})

WHERE_THE_TIME_GOES = (
    "Find where the time after his go-ahead went for that week's deliveries. The usual places are GitHub's checks and "
    "merge queue, integration test runs and the machine's line for them, sessions waiting on a reply from him, and "
    "sessions idle between steps. Session transcripts are under ~/.claude/projects; read timestamps, commands and his "
    "go-ahead lines only, never the contents of his media or prompts.")
WHAT_GOT_THROUGH = ("Find which shipped changes let them through and which check would have caught them before "
                    "shipping, and add that check.")
WHAT_TO_FIND = {"median_minutes": WHERE_THE_TIME_GOES, "slowest_tenth_minutes": WHERE_THE_TIME_GOES,
                "bug_reports": WHAT_GOT_THROUGH, "crashes": WHAT_GOT_THROUGH}
NUMBER_WORDS = {"median_minutes": "median ship time, in minutes", "slowest_tenth_minutes": "slowest tenth, in minutes",
                "bug_reports": "bugs he reported", "crashes": "app crashes"}


def work_key(number: str, week: str) -> str:
    return f"{week} {number}"


def asked_for(url: str) -> tuple[str, str] | None:
    if not url.startswith(ASKED_AT):
        return None
    number, _, week = url.removeprefix(ASKED_AT).partition("/")
    return (number, week) if number in WHAT_TO_FIND and week else None


def read_sessions(path: Path) -> dict[str, dict]:
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return kept if isinstance(kept, dict) else {}


def session_brief(number: str, week: str, value: object, before: object) -> str:
    return "\n".join([
        ("You were started by Line, his tray app, when he clicked Work on this beside a number in its Ship Time row. "
         "Ship Time is the time from his \"ship it\" to the change being delivered, measured beside the bugs he "
         "reports and the app crashes; he wants both faster delivery and fewer bugs and crashes."),
        "",
        f"For the week {week} the {NUMBER_WORDS[number]} was {value}, worse than the {before} of the week before.",
        "",
        WHAT_TO_FIND[number],
        ("Fix what is in your power within the laws: a hook, a law, a tool, a script. A change to GitHub settings or "
         "anything outward-facing needs his yes first. Size anything too big to finish in this session with the "
         "delegating-work skill, and propose it to him rather than starting it."),
        "",
        ("Also read Fun Time's main verifier record, state/main_verifier_incidents.jsonl in its production checkout, "
         "for the week. A test it recorded as \"flaky\" a second time goes to him as the running-tests skill says."),
        "",
        ("Report to him in one short message in his reply format, the no-quote report shape: the number against the "
         "week before, what you changed, and anything that needs his yes. A change \"ships\"; never say \"land\" to "
         "him. Follow every law in ~/.claude/CLAUDE.md and ~/.claude/engineering.md as any session does."),
    ])


@dataclass(frozen=True)
class StartHands:
    claude: str
    workspace: Path
    start: Callable[..., str] = claude_sessions.start_in_background


def start_the_work(state: Path, number: str, week: str, hands: StartHands) -> None:
    sessions_path = state / SESSIONS_NAME
    sessions = read_sessions(sessions_path)
    record = read_the_record(state / RECORD_NAME)
    if work_key(number, week) in sessions or record is None:
        return
    try:
        before, last = [row for row in record.weeks if row["week"] <= week][-2:]
    except ValueError:
        return
    brief = session_brief(number, week, last.get(number), before.get(number))
    title = f"Work on {NUMBER_WORDS[number].split(',')[0]}, {week}"
    short_id = hands.start(hands.claude, NewSession(title, brief, hands.workspace))
    sessions[work_key(number, week)] = {"short_id": short_id, "title": title, "cwd": str(hands.workspace),
                                        "told": False}
    write_whole(sessions_path, json.dumps(sessions, indent=1))


@dataclass(frozen=True)
class FinishedSession:
    key: str
    short_id: str
    session_id: str
    cwd: str
    title: str
    body: str


def _words_for(state: str) -> str:
    if state == "done":
        return "Click to open it in Claude Desktop and read what it did."
    if state == "blocked":
        return "It is waiting for you. Click to open it in Claude Desktop."
    return "It stopped on an error. Click to open it in Claude Desktop and tell it to carry on."


def finished_sessions(sessions: dict[str, dict], background: list[dict]) -> list[FinishedSession]:
    by_id = {session.get("id"): session for session in background}
    found = []
    for key, kept in sessions.items():
        session = by_id.get(kept["short_id"])
        if not kept.get("told") and session and session.get("state") in FINISHED_STATES:
            found.append(FinishedSession(key, kept["short_id"], session.get("sessionId", kept["short_id"]),
                                         kept["cwd"], kept["title"], _words_for(session["state"])))
    return found


def _is_done(asking: Future) -> bool:
    return asking.done()


def _answer_of(asking: Future) -> list[dict]:
    return [] if asking.exception() is not None else asking.result()


class WatchForFinishedSessions:
    def __init__(self, sessions_path: Path, *, background: Callable[[], list[dict]],
                 tell: Callable[[FinishedSession], object], pool: ThreadPoolExecutor | None = None):
        self._path = sessions_path
        self._background = background
        self._tell = tell
        self._ask = (pool or ThreadPoolExecutor(max_workers=1)).submit
        self._asking: Future | None = None

    def _someone_to_wait_for(self, sessions: dict[str, dict]) -> bool:
        return any(not kept.get("told") for kept in sessions.values())

    def tick(self) -> None:
        sessions = read_sessions(self._path)
        if self._asking is None:
            if self._someone_to_wait_for(sessions):
                self._asking = self._ask(self._background)
            return
        if not _is_done(self._asking):
            return
        asking, self._asking = self._asking, None
        for finished in finished_sessions(sessions, _answer_of(asking)):
            sessions[finished.key]["told"] = True
            write_whole(self._path, json.dumps(sessions, indent=1))
            self._tell(finished)


def _the_machine_s_hands() -> StartHands:
    return StartHands(claude_sessions.claude_command(),
                      siblings.project_dir("fun_time", haglio_settings.project_roots()).parent)


def main(argv: list[str] | None = None, hands: Callable[[], StartHands] = _the_machine_s_hands) -> int:
    parser = argparse.ArgumentParser(prog="ship_time_work")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--number", required=True)
    parser.add_argument("--week", required=True)
    args = parser.parse_args(argv)
    try:
        start_the_work(args.state, args.number, args.week, hands())
    except CommandFailed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
