from __future__ import annotations

import bisect
import itertools
import json
import subprocess
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path

from line.github import answered

PULL_REQUESTS_AT_MOST = 5000


def waits(merged: dict[str, datetime], main: list[str],
          production: Iterable[tuple[datetime, str]]) -> list[tuple[datetime, timedelta]]:
    place = {commit: index for index, commit in enumerate(main)}
    taken = sorted((when, place[commit]) for when, commit in production if commit in place)
    furthest = list(itertools.accumulate((index for _when, index in taken), max))
    found = []
    for commit, merged_at in merged.items():
        if commit not in place or not taken or merged_at < taken[0][0]:
            continue
        first = bisect.bisect_left(furthest, place[commit])
        if first < len(taken):
            found.append((merged_at, taken[first][0] - merged_at))
    return sorted(found)


def _merged_since(since: datetime, run) -> dict[str, datetime]:
    listed = answered(run(["gh", "pr", "list", "-R", "haglio/fun_time", "--state", "merged", "--search",
                           f"merged:>={since.date().isoformat()}", "--json", "mergedAt,mergeCommit",
                           "--limit", str(PULL_REQUESTS_AT_MOST)], capture_output=True, text=True))
    return {pull_request["mergeCommit"]["oid"]: datetime.fromisoformat(pull_request["mergedAt"])
            for pull_request in json.loads(listed or "[]") if pull_request.get("mergeCommit")}


def _git(fun_time: Path, run, *arguments: str) -> str:
    return run(["git", "-C", str(fun_time), *arguments], capture_output=True, text=True).stdout


def _production_moves(fun_time: Path, run) -> list[tuple[datetime, str]]:
    moves = []
    for line in _git(fun_time, run, "reflog", "--date=iso-strict", "--format=%H%x09%gd").splitlines():
        commit, _, where = line.partition("\t")
        if where.startswith("HEAD@{") and where.endswith("}"):
            moves.append((datetime.fromisoformat(where[len("HEAD@{"):-1]), commit))
    return moves


def measure(fun_time: Path, *, since: datetime, run=subprocess.run) -> list[tuple[datetime, timedelta]]:
    merged = {commit: when for commit, when in _merged_since(since, run).items() if when >= since}
    main = _git(fun_time, run, "rev-list", "--first-parent", "--reverse", "origin/main").split()
    return waits(merged, main, _production_moves(fun_time, run))
