import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from line import to_production
from line.github import GitHubDidNotAnswer

T0 = datetime(2026, 11, 19, 22, 59, tzinfo=UTC)
MAIN = ["a", "b", "c", "d"]


def hours(n):
    return timedelta(hours=n)


def test_a_change_waits_until_production_first_takes_a_version_that_holds_it():
    production = [(T0 - hours(1), "a"), (T0 + hours(2), "b"), (T0 + hours(7), "d")]

    assert to_production.waits({"c": T0}, MAIN, production) == [(T0, hours(7))]


def test_a_change_production_has_not_taken_yet_has_no_wait_so_far():
    assert to_production.waits({"d": T0}, MAIN, [(T0 + hours(1), "c")]) == []


def test_a_change_missing_from_main_s_history_is_passed_over():
    assert to_production.waits({"z": T0}, MAIN, [(T0 + hours(1), "d")]) == []


class FakeFunTime:
    def __init__(self, *, github_up=True):
        self.github_up, self.asked = github_up, []

    def __call__(self, command, **_):
        self.asked.append(command)
        if command[:2] == ["gh", "pr"]:
            if not self.github_up:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="no route")
            merged = [{"mergedAt": "2026-11-19T22:59:00Z", "mergeCommit": {"oid": "c"}},
                      {"mergedAt": "2026-11-19T20:00:00Z", "mergeCommit": None}]
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(merged), stderr="")
        if "reflog" in command:
            reflog = ("d\tHEAD@{2026-11-20T05:59:00+00:00}\n"
                      "b\tHEAD@{2026-11-20T00:59:00+00:00}\n"
                      "a\tHEAD@{2026-11-19T21:59:00+00:00}\n")
            return subprocess.CompletedProcess(command, 0, stdout=reflog, stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="a\nb\nc\nd\n", stderr="")


def test_the_waits_are_read_from_github_main_s_history_and_production_s_own_record():
    run = FakeFunTime()

    found = to_production.measure(Path("made-up"), since=T0 - hours(48), run=run)

    assert found == [(T0, hours(7))]
    assert run.asked[0][:2] == ["gh", "pr"]
    assert "merged:>=2026-11-17" in run.asked[0]
    assert len(run.asked) == 3


def test_github_not_answering_is_said_so():
    with pytest.raises(GitHubDidNotAnswer):
        to_production.measure(Path("made-up"), since=T0, run=FakeFunTime(github_up=False))


def test_a_change_shipped_before_production_s_record_begins_is_passed_over():
    assert to_production.waits({"a": T0 - hours(30)}, MAIN, [(T0, "b")]) == []
