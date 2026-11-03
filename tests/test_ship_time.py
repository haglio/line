import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from line import ship_time

T0 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def his_message(text, at):
    return json.dumps({"type": "user", "timestamp": at.isoformat(), "message": {"content": text}})


def test_a_go_ahead_counts_only_in_his_own_words_not_in_a_reply_he_quoted():
    lines = [his_message(">> shall I ship it?\n\nyes, ship it", T0),
             his_message(">> ready to ship it?\n> it is\n\nnot yet", T0 + timedelta(minutes=5))]

    assert ship_time.read_session(lines).accepted == [T0]


def test_a_go_ahead_in_text_blocks_counts_and_text_the_harness_injects_does_not():
    lines = [json.dumps({"type": "user", "timestamp": T0.isoformat(), "message": {"content": [
                 {"type": "text", "text": "ship it"}]}}),
             his_message("<task-notification>ship it</task-notification>", T0 + timedelta(minutes=1)),
             json.dumps({"type": "user", "isMeta": True, "timestamp": T0.isoformat(),
                         "message": {"content": "ship it"}})]

    assert ship_time.read_session(lines).accepted == [T0]


REMINDERS = ("<system-reminder>\nA Claude agent may ship it once the crash is fixed.\n</system-reminder>\n"
             "<system-reminder>\nToday's date is 2026-10-05.\n</system-reminder>\n")


def test_a_go_ahead_counts_when_the_harness_put_reminders_in_front_of_his_words():
    assert ship_time.read_session([his_message(REMINDERS + "ship it", T0)]).accepted == [T0]


def opened_with(text):
    return ship_time.read_session([his_message(text, T0), his_message("it crashed again", T0 + minutes(9))])


def test_a_session_he_opens_to_say_something_broke_is_a_bug_report():
    assert opened_with("the main player crashed when I hit next").reports_a_bug
    assert opened_with("Origenerator's slideshow doesn't work after a lock").reports_a_bug
    assert not opened_with("add a button that loops the row").reports_a_bug


def test_a_program_that_stops_responding_is_a_bug_report_in_any_tense():
    assert opened_with("the main player hangs on next").reports_a_bug
    assert opened_with("the main player h" + "ung on next").reports_a_bug


def test_a_session_another_agent_opened_with_a_brief_is_not_his_bug_report():
    brief = ("A Claude agent spawned this task; the user has not read this brief. "
             "The crash in the satellite is fixed by ...")

    assert not opened_with(brief).reports_a_bug


def crash_record(when, source, application):
    return (f"Event[0]\n  Log Name: Application\n  Source: {source}\n  Date: {when}\n  Event ID: 1000\n"
            f"Description:\nFaulting application name: {application}, version: 3.14.3150.1013\n"
            f"Faulting module name: Qt6Gui.dll, version: 6.10.0.0\n")


def test_the_crashes_counted_are_the_family_apps_and_nothing_else_on_the_machine():
    log = "".join([crash_record("2026-10-05T19:42:03.1670000Z", "Application Error", "pythonw.exe"),
                   crash_record("2026-10-05T20:00:00.0000000Z", "Application Error", "FunTime-MainPlayer.exe"),
                   crash_record("2026-10-05T21:00:00.0000000Z", "Application Error", "vrserver.exe"),
                   crash_record("2026-10-05T22:00:00.0000000Z", "Some.Other.Service", "pythonw.exe")])

    assert ship_time.family_crashes(log) == [datetime(2026, 10, 5, 19, 42, 3, tzinfo=UTC),
                                                datetime(2026, 10, 5, 20, 0, 0, tzinfo=UTC)]


def test_each_week_has_its_median_and_slowest_tenth_beside_what_got_through():
    monday = datetime(2026, 10, 5, 12, tzinfo=UTC)
    deliveries = [(monday + timedelta(hours=i), minutes(m)) for i, m in enumerate([10, 20, 30, 40, 200])]

    rows = ship_time.weekly(deliveries, bug_reports=[monday], crashes=[monday, monday + timedelta(days=7)],
                               zone=UTC)

    assert rows == [
        {"week": "2026-W41", "deliveries": 5, "median_minutes": 30, "slowest_tenth_minutes": 200,
         "bug_reports": 1, "crashes": 1},
        {"week": "2026-W42", "deliveries": 0, "median_minutes": None, "slowest_tenth_minutes": None,
         "bug_reports": 0, "crashes": 1},
    ]


def test_a_line_cut_short_when_a_session_died_is_passed_over():
    lines = ['{"type": "user", "timestamp": "2026-10', his_message("ship it", T0),
             json.dumps({"type": "user", "timestamp": T0.isoformat(), "message": {"content": ["a stray"]}})]

    assert ship_time.read_session(lines).accepted == [T0]


class FakeGitHub:
    def __init__(self, merged):
        self.merged, self.queries = merged, []

    def __call__(self, command, **_):
        query = command[-1]
        self.queries.append(query)
        answer = {}
        for alias, repo, number in re.findall(r'(p\d+): repository\(owner:"haglio",name:"([^"]+)"\)'
                                               r'\{pullRequest\(number:(\d+)\)', query):
            merged_at = self.merged.get((repo, int(number)))
            answer[alias] = {"pullRequest": {"mergedAt": merged_at}}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"data": answer}), stderr="")


def test_merge_times_come_from_github_in_batches_and_skip_what_never_merged():
    github = FakeGitHub({("fun_time", n): "2026-10-05T12:00:00Z" for n in range(1, 60)})
    asked = {("fun_time", n) for n in range(1, 61)}

    merged = ship_time.merge_times(asked, run=github)

    assert len(github.queries) == 2
    assert set(merged) == {("fun_time", n) for n in range(1, 60)}
    assert merged[("fun_time", 1)] == datetime(2026, 10, 5, 12, tzinfo=UTC)


def minutes(n):
    return timedelta(minutes=n)


def test_a_delivery_runs_from_the_first_go_ahead_to_the_last_merge_it_covered():
    accepted = [T0, T0 + minutes(3)]
    merges = [T0 - minutes(30), T0 + minutes(20), T0 + minutes(50)]

    assert ship_time.deliveries(accepted, merges) == [(T0, minutes(50))]


def test_a_go_ahead_after_a_merge_starts_the_next_delivery():
    accepted = [T0, T0 + minutes(60)]
    merges = [T0 + minutes(20), T0 + minutes(90)]

    assert ship_time.deliveries(accepted, merges) == [(T0, minutes(20)), (T0 + minutes(60), minutes(30))]


def a_command(call_id, command, at):
    return json.dumps({"type": "assistant", "timestamp": at.isoformat(), "message": {"content": [
        {"type": "tool_use", "id": call_id, "name": "Bash", "input": {"command": command}}]}})


def its_output(call_id, text, at):
    return json.dumps({"type": "user", "timestamp": at.isoformat(), "message": {"content": [
        {"type": "tool_result", "tool_use_id": call_id, "content": text}]}})


def test_the_pull_requests_a_session_opened_are_the_ones_gh_pr_create_printed():
    lines = [a_command("c1", "gh pr list --repo haglio/fun_time", T0),
             its_output("c1", "https://github.com/haglio/fun_time/pull/7", T0),
             a_command("c2", "gh pr create --draft --title x", T0),
             its_output("c2", "https://github.com/haglio/origenerator/pull/12\n", T0)]

    assert ship_time.read_session(lines).opened == {("origenerator", 12)}


class FakeMachine(FakeGitHub):
    def __init__(self, merged, crash_log=""):
        super().__init__(merged)
        self.crash_log = crash_log

    def __call__(self, command, **kwargs):
        if command[0] == "wevtutil":
            return subprocess.CompletedProcess(command, 0, stdout=self.crash_log, stderr="")
        return super().__call__(command, **kwargs)


def test_measure_counts_a_week_from_the_transcripts_github_and_the_crash_log(tmp_path):
    project = tmp_path / "C--made-up-haglio-app"
    project.mkdir()
    (project / "shipped.jsonl").write_text("\n".join([
        a_command("c1", "gh pr create --draft", T0 - minutes(5)),
        its_output("c1", "https://github.com/haglio/fun_time/pull/7", T0 - minutes(5)),
        his_message("the main player crashed", T0 - minutes(1)),
        his_message("ship it", T0)]), encoding="utf-8")
    machine = FakeMachine({("fun_time", 7): (T0 + minutes(30)).isoformat()},
                          crash_record("2026-10-05T19:42:03.1670000Z", "Application Error", "pythonw.exe"))

    rows = ship_time.measure(1, projects=tmp_path, run=machine, now=T0 + timedelta(days=1))

    assert rows == [{"week": "2026-W41", "deliveries": 1, "median_minutes": 30, "slowest_tenth_minutes": 30,
                     "bug_reports": 1, "crashes": 1}]


LATER = T0 + timedelta(days=1)
INCIDENT = {"at": "2026-10-11T20:05:44", "kind": "broke main", "commit": "abc123", "tests": ["t"],
            "taken_back_by": 428, "pull_request": 426}


class FakeFunTime:
    def __init__(self, *, merged_take_back=True, title_says_shipped_again=False, reverts=None, github_up=True):
        self.merged_take_back = merged_take_back
        self.title_says_shipped_again = title_says_shipped_again
        self.reverts = reverts or {}
        self.github_up = github_up

    def _answer(self, query):
        answer = {}
        for alias in re.findall(r"(\w\d+): ", query):
            if alias[0] == "p":
                answer[alias] = {"pullRequest": {"title": "A made-up change", "mergedAt": None}}
            elif alias[0] == "b":
                answer[alias] = {"pullRequest": {"mergedAt": "2026-10-12T03:24:15Z" if self.merged_take_back else None}}
            else:
                answer[alias] = {"issueCount": 1 if self.title_says_shipped_again else 0}
        return {"data": answer}

    def __call__(self, command, **_):
        if command[0] == "git":
            reverted = command[-1].removeprefix(ship_time.REVERTS)
            return subprocess.CompletedProcess(command, 0, stdout=self.reverts.get(reverted, ""), stderr="")
        if command[0] == "wevtutil":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if not self.github_up:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="no route")
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(self._answer(command[-1])), stderr="")


def missing_given(fun_time, incidents=(INCIDENT,)):
    return ship_time.not_shipped_again(list(incidents), Path("made-up"), run=fun_time)


def test_a_change_taken_back_and_never_brought_back_is_listed_with_its_title():
    assert missing_given(FakeFunTime()) == [
        {"pull_request": 426, "title": "A made-up change", "taken_back": "2026-10-11T20:05:44"}]


def test_a_take_back_that_never_merged_left_the_change_where_it_was():
    assert missing_given(FakeFunTime(merged_take_back=False)) == []


def test_a_pull_request_titled_ship_it_again_brought_the_change_back():
    assert missing_given(FakeFunTime(title_says_shipped_again=True)) == []


def test_a_revert_of_the_take_back_brought_the_change_back():
    assert missing_given(FakeFunTime(reverts={"abc123": "takeback1\n", "takeback1": "again1\n"})) == []


def test_the_verifier_shipping_it_again_itself_brought_the_change_back():
    assert missing_given(FakeFunTime(), [INCIDENT, {"pull_request": 426, "shipped_again_by": 500}]) == []


def test_a_change_the_verifier_could_not_take_back_lists_nothing():
    assert missing_given(FakeFunTime(), [{**INCIDENT, "taken_back_by": None}]) == []


def fun_time_folder(place, *incidents):
    (place / "state").mkdir(parents=True)
    (place / "state" / "main_verifier_incidents.jsonl").write_text(
        "\n".join(json.dumps(incident) for incident in incidents), encoding="utf-8")
    return place


def test_the_record_keeps_older_weeks_and_measures_only_the_latest_two_after_the_first_time(tmp_path):
    record = tmp_path / ship_time.RECORD_NAME
    record.write_text(json.dumps({"weeks": {"2026-W30": {"week": "2026-W30", "crashes": 9}}, "taken_back": []}),
                      encoding="utf-8")
    projects = tmp_path / "projects" / "C--made-up-haglio-app"
    projects.mkdir(parents=True)
    (projects / "bug.jsonl").write_text(his_message("the main player crashed", T0), encoding="utf-8")

    ship_time.update_the_record(record, projects=tmp_path / "projects", fun_time=fun_time_folder(tmp_path / "ft"),
                                run=FakeFunTime(), now=LATER)

    kept = ship_time.read_the_record(record)
    assert [row["week"] for row in kept.weeks] == ["2026-W30", "2026-W41"]
    assert kept.weeks[0]["crashes"] == 9
    assert kept.weeks[1]["bug_reports"] == 1
    assert kept.weeks[1]["measured_at"] == LATER.isoformat(timespec="seconds")


def test_github_not_answering_leaves_the_record_as_it_was(tmp_path):
    record = tmp_path / ship_time.RECORD_NAME
    before = json.dumps({"weeks": {"2026-W30": {"week": "2026-W30"}}, "taken_back": []})
    record.write_text(before, encoding="utf-8")

    with pytest.raises(ship_time.GitHubDidNotAnswer):
        ship_time.update_the_record(record, projects=tmp_path, fun_time=fun_time_folder(tmp_path / "ft", INCIDENT),
                                    run=FakeFunTime(github_up=False), now=LATER)

    assert record.read_text(encoding="utf-8") == before


def test_no_record_yet_reads_as_none(tmp_path):
    assert ship_time.read_the_record(tmp_path / "absent.json") is None


def test_a_go_ahead_no_merge_followed_counts_for_nothing():
    assert ship_time.deliveries([T0], [T0 - minutes(5), T0 + timedelta(days=2)]) == []


def test_a_record_cut_short_starts_again_from_the_full_history(tmp_path):
    record = tmp_path / ship_time.RECORD_NAME
    record.write_text('{"weeks": {"2026-W', encoding="utf-8")

    ship_time.update_the_record(record, projects=tmp_path / "no-projects", fun_time=fun_time_folder(tmp_path / "ft"),
                                run=FakeFunTime(), now=LATER)

    assert ship_time.read_the_record(record) == ship_time.ShipTimeRecord([], [])


def test_the_command_says_when_github_did_not_answer(tmp_path):
    def refuses(*_args, **_kwargs):
        raise ship_time.GitHubDidNotAnswer("no route")

    assert ship_time.main(["--out", str(tmp_path / "out.json")], update=refuses) == 1
    assert ship_time.main(["--out", str(tmp_path / "out.json")], update=lambda *_a, **_k: None) == 0
