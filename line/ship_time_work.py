from __future__ import annotations

from pathlib import Path

from app_support.claude_sessions import new_session_link as claude_desktop_link

from line.ship_time import RECORD_NAME, read_the_record

ASKED_AT = "https://line-work.invalid/"

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


def asked_for(url: str) -> tuple[str, str] | None:
    if not url.startswith(ASKED_AT):
        return None
    number, _, week = url.removeprefix(ASKED_AT).partition("/")
    return (number, week) if number in WHAT_TO_FIND and week else None


def session_brief(number: str, week: str, value: object, before: object) -> str:
    return "\n".join([
        ("He opened this session from the Work on this button beside a number in Line's Ship Time row. Ship Time is "
         "the time from his \"ship it\" to the change being delivered, measured beside the bugs he reports and the app "
         "crashes; he wants both faster delivery and fewer bugs and crashes."),
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
        ("Report to him in one short message: the number against the week before, what you changed, and anything that "
         "needs his yes. Follow every law in ~/.claude/CLAUDE.md and ~/.claude/engineering.md as any session does."),
    ])


def new_session_link(state: Path, number: str, week: str, folder: Path) -> str | None:
    record = read_the_record(state / RECORD_NAME)
    if record is None:
        return None
    try:
        before, last = [row for row in record.weeks if row["week"] <= week][-2:]
    except ValueError:
        return None
    brief = session_brief(number, week, last.get(number), before.get(number))
    return claude_desktop_link(folder, brief)
