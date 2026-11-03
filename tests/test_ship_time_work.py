import json
from concurrent.futures import Future
from pathlib import Path

from app_support.claude_sessions import CommandFailed

from line import ship_time_work
from line.ship_time import RECORD_NAME
from line.ship_time_work import SESSIONS_NAME, FinishedSession, StartHands, WatchForFinishedSessions

BEFORE = {"week": "2026-W43", "median_minutes": 84}
LAST = {"week": "2026-W44", "median_minutes": 144}
WORKSPACE = Path("C:/made/up/workspace")


def state_with_weeks(place):
    (place / RECORD_NAME).write_text(json.dumps({"weeks": {row["week"]: row for row in (BEFORE, LAST)},
                                                 "taken_back": []}), encoding="utf-8")
    return place


class FakeStart:
    def __init__(self):
        self.started = []

    def __call__(self, claude, session):
        self.started.append((claude, session.title, session.brief, session.cwd))
        return "812181fb"


def test_a_click_is_read_off_the_link_the_page_drew():
    assert ship_time_work.asked_for("https://line-work.invalid/median_minutes/2026-W44") == ("median_minutes", "2026-W44")


def test_any_other_link_is_no_click():
    assert ship_time_work.asked_for("file:///made/up/line.html") is None
    assert ship_time_work.asked_for("https://line-work.invalid/not_a_number/2026-W44") is None
    assert ship_time_work.asked_for("https://line-work.invalid/median_minutes") is None


def test_the_click_starts_a_session_named_for_the_number_and_told_both_weeks(tmp_path):
    start = FakeStart()

    ship_time_work.start_the_work(state_with_weeks(tmp_path), "median_minutes", "2026-W44",
                                  StartHands("claude", WORKSPACE, start))

    [(claude, title, brief, cwd)] = start.started
    assert (claude, title, cwd) == ("claude", "Work on median ship time, 2026-W44", WORKSPACE)
    assert "was 144, worse than the 84 of the week before" in brief
    assert ship_time_work.WHERE_THE_TIME_GOES in brief
    assert json.loads((tmp_path / SESSIONS_NAME).read_text(encoding="utf-8")) == {
        "2026-W44 median_minutes": {"short_id": "812181fb", "title": title, "cwd": str(WORKSPACE), "told": False}}


def test_a_second_click_starts_no_second_session(tmp_path):
    start = FakeStart()
    hands = StartHands("claude", WORKSPACE, start)

    ship_time_work.start_the_work(state_with_weeks(tmp_path), "median_minutes", "2026-W44", hands)
    ship_time_work.start_the_work(tmp_path, "median_minutes", "2026-W44", hands)

    assert len(start.started) == 1


def test_a_week_with_nothing_before_it_starts_nothing(tmp_path):
    start = FakeStart()

    ship_time_work.start_the_work(state_with_weeks(tmp_path), "median_minutes", "2026-W43",
                                  StartHands("claude", WORKSPACE, start))

    assert start.started == []


def test_no_record_starts_nothing(tmp_path):
    start = FakeStart()

    ship_time_work.start_the_work(tmp_path, "median_minutes", "2026-W44", StartHands("claude", WORKSPACE, start))

    assert start.started == []


def kept(told=False):
    return {"2026-W44 crashes": {"short_id": "812181fb", "title": "Work on app crashes, 2026-W44", "cwd": "C:/made/up",
                                 "told": told}}


def test_a_session_that_finished_is_found_with_what_to_say():
    [found] = ship_time_work.finished_sessions(kept(), [{"id": "812181fb", "sessionId": "whole-id", "state": "done"}])

    assert found == FinishedSession("2026-W44 crashes", "812181fb", "whole-id", "C:/made/up",
                                    "Work on app crashes, 2026-W44",
                                    "Click to open it in Claude Desktop and read what it did.")


def test_a_session_waiting_or_stopped_says_so():
    waiting = ship_time_work.finished_sessions(kept(), [{"id": "812181fb", "state": "blocked"}])
    failed = ship_time_work.finished_sessions(kept(), [{"id": "812181fb", "state": "failed"}])

    assert "waiting for you" in waiting[0].body
    assert "stopped on an error" in failed[0].body


def test_a_session_still_working_or_already_told_about_is_not_found():
    assert ship_time_work.finished_sessions(kept(), [{"id": "812181fb", "state": "working"}]) == []
    assert ship_time_work.finished_sessions(kept(told=True), [{"id": "812181fb", "state": "done"}]) == []


class ImmediatePool:
    def submit(self, work):
        future = Future()
        try:
            future.set_result(work())
        except OSError as why:
            future.set_exception(why)
        return future


def watch(tmp_path, background, told):
    path = tmp_path / SESSIONS_NAME
    return path, WatchForFinishedSessions(path, background=background, tell=told.append, pool=ImmediatePool())


def test_the_watch_tells_once_when_a_session_finishes_and_remembers_it_told(tmp_path):
    told = []
    path, watching = watch(tmp_path, lambda: [{"id": "812181fb", "sessionId": "whole-id", "state": "done"}], told)
    path.write_text(json.dumps(kept()), encoding="utf-8")

    for _ in range(4):
        watching.tick()

    assert [finished.short_id for finished in told] == ["812181fb"]
    assert json.loads(path.read_text(encoding="utf-8"))["2026-W44 crashes"]["told"] is True


def test_the_watch_asks_nothing_while_no_session_is_going(tmp_path):
    asked = []
    _path, watching = watch(tmp_path, lambda: asked.append(1) or [], [])

    watching.tick()
    watching.tick()

    assert asked == []


def test_a_question_that_failed_tells_nothing_and_is_asked_again(tmp_path):
    told, asked = [], []

    def refuses():
        asked.append(1)
        raise OSError("no")

    path, watching = watch(tmp_path, refuses, told)
    path.write_text(json.dumps(kept()), encoding="utf-8")

    for _ in range(3):
        watching.tick()

    assert told == []
    assert len(asked) == 2


def test_a_record_cut_short_reads_as_no_sessions(tmp_path):
    (tmp_path / SESSIONS_NAME).write_text('{"cut', encoding="utf-8")

    assert ship_time_work.read_sessions(tmp_path / SESSIONS_NAME) == {}


def test_each_number_s_brief_says_what_to_find():
    assert ship_time_work.WHAT_GOT_THROUGH in ship_time_work.session_brief("bug_reports", "2026-W44", 9, 3)


def run_main(state, start):
    return ship_time_work.main(["--state", str(state), "--number", "median_minutes", "--week", "2026-W44"],
                               hands=lambda: StartHands("claude", WORKSPACE, start))


def test_the_command_starts_the_work_it_is_given(tmp_path):
    start = FakeStart()

    assert run_main(state_with_weeks(tmp_path), start) == 0
    assert len(start.started) == 1


def test_a_start_that_fails_ends_the_command_with_a_failure_and_remembers_nothing(tmp_path):
    def refuses(_claude, _session):
        raise CommandFailed("not signed in")

    assert run_main(state_with_weeks(tmp_path), refuses) == 1
    assert not (tmp_path / SESSIONS_NAME).exists()


def test_the_machine_s_session_starts_in_the_folder_the_checkouts_sit_in():
    hands = ship_time_work._the_machine_s_hands()

    assert hands.claude
    assert hands.workspace.name != "fun_time"


def test_a_question_still_being_answered_is_waited_for_not_asked_again(tmp_path):
    asked = []

    class NeverAnswers:
        def submit(self, _work):
            asked.append(1)
            return Future()

    path = tmp_path / SESSIONS_NAME
    path.write_text(json.dumps(kept()), encoding="utf-8")
    watching = WatchForFinishedSessions(path, background=list, tell=print, pool=NeverAnswers())

    watching.tick()
    watching.tick()

    assert len(asked) == 1
