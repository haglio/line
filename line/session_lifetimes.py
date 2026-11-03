from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app_support.file_channel import write_whole


@dataclass(frozen=True)
class Lifetime:
    spawned: datetime
    archived: datetime | None


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
