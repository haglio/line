import json
import os
import re
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from line import line_page

T0 = datetime(2026, 10, 10, 20, 0, tzinfo=UTC)


def minute(n: float) -> datetime:
    return T0 + timedelta(minutes=n)


def test_every_run_a_command_asks_for_is_read_with_its_arguments():
    command = ('P=x; "$P/python.exe" -m tests.integration.hidden_desktop -k test_crown_integration > out.txt 2>&1; '
               'grep -q ok out.txt && "$P/python.exe" -m tests.integration.hidden_desktop --changed\n'
               "python -c \"import runpy, sys; sys.argv = ['hidden_desktop', '--repeat-changed', 'origin/main']; "
               "runpy.run_module('tests.integration.hidden_desktop', run_name='__main__')\"")

    assert line_page.runner_invocations(command) == [
        ["-k", "test_crown_integration"], ["--changed"], ["--repeat-changed", "origin/main"]]


def test_what_a_run_asked_for_is_said_in_words():
    assert line_page.describe([]) == "the whole suite"
    assert line_page.describe(["--changed"]) == "what its change needs"
    assert line_page.describe(["--repeat-changed", "origin/main"]) == "its new tests, over and over"
    assert line_page.describe(["-k", "test_branch_session_integration"]) == "the branch session tests"
    assert line_page.describe(["-k", "test_a_player_dies_at_startup_integration"]) == (
        'the "a player dies at startup" tests')
    assert line_page.describe(["-k", "test_quit_integration or test_crown_integration"]) == "tests from 2 files"
    assert line_page.describe(["tests/integration/test_crown_integration.py",
                                   "tests/integration/test_quit_integration.py::test_a_quit"]) == "tests from 2 files"


def test_a_run_is_named_for_the_branch_that_asked_for_it():
    assert line_page.who_of("claude/example-corner-positioning-84fbd2") == "example corner positioning"
    assert line_page.who_of("claude/list-the-examples-without-opening-them") == (
        "list the examples without opening them")
    assert line_page.who_of("HEAD") == "a session on no branch"
    assert line_page.who_of("") == "a session on no branch"


WAITING = ("[integration] another integration run holds 'Global\\\\fun_time_integration_run'; "
           "this short run waits for it, ahead of any full run ({}s elapsed)\n")
TESTS_START = "============================= test session starts =============================\n"


def test_a_runs_output_says_how_long_it_waited_and_how_it_ended():
    text = (WAITING.format(2) + WAITING.format(512) + "collected 123 items\n"
            "============ 1 failed, 122 passed, 1 warning in 1370.34s (0:22:50) ============\n")

    outcome = line_page.read_output(text)

    assert outcome == line_page.Outcome(waited_s=512, ran_s=1370.34, passed=122, failed=1)
    assert outcome.verdict == "failed"


def test_a_run_that_never_printed_its_summary_ended_without_a_result():
    assert line_page.read_output(WAITING.format(30) + "collected 123 items\n").verdict == "no result"
    assert line_page.read_output("== 3 passed in 9.10s ==\n").verdict == "passed"
    assert line_page.read_output("== 1 passed, 2 errors in 9.10s ==\n").verdict == "failed"


def test_the_main_verifiers_runs_are_read_from_its_own_run_files(tmp_path):
    whole = tmp_path / "20261010-133310-9ccb4e8c54.txt"
    whole.write_text(WAITING.format(40) + TESTS_START + "collected 123 items\n== 1 failed, 122 passed in 1418.58s ==\n",
                     encoding="utf-8")
    again = tmp_path / "20261010-160602-9ccb4e8c54.txt"
    again.write_text(WAITING.format(9000) + TESTS_START + "collected 1 item\n== 1 passed in 26.00s ==\n", encoding="utf-8")
    stuck = tmp_path / "20261010-114926-f6af154f73.txt"
    stuck.write_text(TESTS_START + "collected 123 items\n+++ Timeout +++\n", encoding="utf-8")
    never_started = tmp_path / "20261010-170000-9ccb4e8c54.txt"
    never_started.write_text(WAITING.format(2) + WAITING.format(7198), encoding="utf-8")
    finished = datetime(2026, 10, 10, 19, 0).astimezone()
    for path in (whole, again, stuck, never_started):
        os.utime(path, (finished.timestamp(), finished.timestamp()))

    runs = line_page.verifier_runs(tmp_path)

    def local(*when: int) -> datetime:
        return datetime(2026, 10, 10, *when).astimezone()

    assert [(run.what, run.joined, run.got_the_machine, run.ended, run.verdict) for run in runs] == [
        ("the whole suite", local(11, 49, 26), local(11, 49, 26), finished, "no result"),
        ("the whole suite", local(13, 33, 10), local(13, 33, 50), finished, "failed"),
        ("1 test", local(16, 6, 2), local(18, 36, 2), finished, "passed"),
        ("some tests", local(17, 0, 0), None, finished, "no result"),
    ]
    assert {run.who for run in runs} == {"main verifier"}


def _chart(page: str, hours: int) -> str:
    return re.search(rf"<figure class=period data-hours={hours}[^>]*>(.*?)</figure>", page, re.DOTALL)[1]


def _rows(chart: str) -> list[str]:
    return re.findall(r"<text class=lane [^>]*>([^<]*)", chart)


def test_the_rows_put_the_runs_in_line_on_top_in_their_order_in_line():
    line =[line_page.InLine("quit faster", "the quit tests", minute(30), short=True, got_the_machine=minute(40)),
            line_page.InLine("crown colors", "the crown tests", minute(50), short=True)]
    runs = [line_page.Run("hud corner", "the hud tests", minute(0), minute(20), exit_code=0,
                              got_the_machine=minute(5)),
            line_page.Run("main verifier", "1 test", minute(10), minute(25), exit_code=0,
                              got_the_machine=minute(12)),
            *(entry.as_run() for entry in line)]

    chart = _chart(line_page.render(line, runs, now=minute(60)), hours=4)

    assert _rows(chart) == ["quit faster", "crown colors", "MAIN VERIFIER", "hud corner"]
    assert re.findall(r"<text class=place [^>]*>(\d+)</text>", chart) == ["1", "2"]


def test_the_chart_offers_one_four_twelve_and_twenty_four_hours_and_shows_four_first():
    page = line_page.render([], [], now=T0)

    periods = re.findall(r"<figure class=period data-hours=(\d+)( hidden)?>", page)
    assert periods == [("1", " hidden"), ("4", ""), ("12", " hidden"), ("24", " hidden")]
    assert re.findall(r"<button type=button data-hours=(\d+)", page) == ["1", "4", "12", "24"]


def test_the_main_verifiers_row_says_it_runs_the_whole_suite_on_each_change_that_ships():
    verifier = line_page.Run("main verifier", "the whole suite", T0, T0 + timedelta(minutes=20), exit_code=0,
                                 got_the_machine=T0)

    chart = _chart(line_page.render([], [verifier], now=T0 + timedelta(hours=1)), hours=4)

    row = re.search(r"<text class=lane [^>]*>MAIN VERIFIER<title>([^<]*)</title></text>", chart)
    assert row
    assert "whole integration suite" in row[1]


def test_a_row_is_named_for_the_claude_session_that_ran_it():
    named = line_page.Run("adoring kirch", "the whole suite", T0, T0 + timedelta(minutes=20), exit_code=0,
                              got_the_machine=T0, checkout="chart-crash-1f2e3d")
    unnamed = line_page.Run("quit faster", "the quit tests", T0, T0 + timedelta(minutes=5), exit_code=0,
                                got_the_machine=T0 + timedelta(minutes=1), checkout="quit-faster-1a2b3c")
    names = {"chart-crash-1f2e3d": "(agent) find why the chart redraws slowly"}

    chart = _chart(line_page.render([], [named, unnamed], now=T0 + timedelta(hours=1), names=names), hours=4)

    assert sorted(_rows(chart)) == ["(agent) find why the chart redraws slowly", "quit faster"]


def test_a_worktrees_session_name_is_the_last_one_its_newest_transcript_gave(tmp_path):
    worktrees = tmp_path / "fun_time" / ".claude" / "worktrees"
    project = tmp_path / "projects" / line_page.project_dir_name(worktrees / "crown-colors-1a2b3c")
    project.mkdir(parents=True)

    def titled(*names: str) -> str:
        return "".join(json.dumps({"type": "custom-title", "customTitle": name, "sessionId": "x"},
                                  separators=(",", ":")) + "\n" for name in names)

    older = project / "older.jsonl"
    older.write_text(titled("an old session"), encoding="utf-8")
    (project / "newer.jsonl").write_text(titled("crown first draft", "crown colors"), encoding="utf-8")
    os.utime(older, (1, 1))

    names = line_page.session_names(tmp_path / "projects", worktrees, ["crown-colors-1a2b3c", "quit-faster"], [])

    assert names == {"crown-colors-1a2b3c": "crown colors"}


def test_a_run_in_line_is_named_for_the_session_that_started_it_whichever_worktree_it_ran_in():
    entry = line_page.InLine("fairer line", "the whole suite", T0, short=False, checkout="fairer-line",
                                 session="5e55-10n")
    names = {"5e55-10n": "steadier example runs", "fairer-line": "some other session"}

    chart = _chart(line_page.render([entry], [entry.as_run()], now=T0 + timedelta(minutes=5), names=names),
                   hours=4)

    assert _rows(chart) == ["steadier example runs"]


def test_a_sessions_name_is_read_from_its_own_transcript(tmp_path):
    project = tmp_path / "projects" / "C--anywhere"
    project.mkdir(parents=True)
    (project / "5e55-10n.jsonl").write_text(
        json.dumps({"type": "custom-title", "customTitle": "steadier example runs", "sessionId": "5e55-10n"},
                   separators=(",", ":")) + "\n", encoding="utf-8")

    names = line_page.session_names(tmp_path / "projects", tmp_path / "worktrees", [], ["5e55-10n", "gone"])

    assert names == {"5e55-10n": "steadier example runs"}


def test_a_long_row_name_is_shortened_to_fit_beside_its_number_and_shown_whole_when_pointed_at():
    long_name = "(agent) find why the example window takes a whole minute to open on a busy evening"
    entry = line_page.InLine("crown colors", "the whole suite", T0, short=False, session="5e55-10n")

    chart = _chart(line_page.render([entry], [entry.as_run()], now=T0 + timedelta(minutes=5),
                                        names={"5e55-10n": long_name}), hours=4)

    shown, whole = re.search(r"<text class=lane [^>]*>([^<]*)<title>([^<]*)</title>", chart).groups()
    assert shown.endswith("…")
    assert len(shown) <= line_page.LABEL_CHARS
    assert whole == long_name


def test_only_the_window_title_says_line_and_the_chart_sits_on_the_page_with_no_box_or_info_mark():
    head, body = line_page.render([], [], now=T0).split("</head>")

    assert "<title>Line</title>" in head
    assert not re.search(r"\bLine\b|<h\d", body)
    assert not re.search(r"&#9432;|ⓘ|title=", body)
    painted_lighter = re.findall(r"([^{}]+)\{[^}]*background:var\(--surface\)", line_page.STYLE)
    assert [selector.strip() for selector in painted_lighter] == ["#tip"]


def test_the_legend_and_the_period_buttons_share_one_row_above_the_chart_legend_first():
    page = line_page.render([], [], now=T0)

    assert re.search(r"<div class=chart-head><div class=legend>.*?</div><div class=periods>.*?</div></div><figure ", page)


def test_the_page_reloads_itself_by_its_own_clock_rather_than_by_a_refresh_tag():
    page = line_page.render([], [], now=T0, refresh_s=30)

    assert "<body data-refresh=30>" in page
    assert "http-equiv=refresh" not in page


def test_the_page_is_dark_whatever_the_system_prefers():
    page = line_page.render([], [], now=T0)

    assert "prefers-color-scheme" not in page
    assert f"--bg:{line_page.DARK_BACKGROUND}" in page


def test_the_legend_starts_with_still_going():
    page = line_page.render([], [], now=T0)

    assert re.findall(r"<span class=key><span class='swatch ([^']+)'>", page)[0] == "going"


def test_only_a_run_that_got_the_machine_draws_a_running_bar():
    now = T0 + timedelta(hours=2)
    gave_up_waiting = line_page.Run("quit faster", "the whole suite", T0, T0 + timedelta(minutes=30), exit_code=1)
    ran = replace(gave_up_waiting, got_the_machine=T0 + timedelta(minutes=20))

    gave_up_page = _chart(line_page.render([], [gave_up_waiting], now), hours=4)
    ran_page = _chart(line_page.render([], [ran], now), hours=4)

    assert (gave_up_page.count("class=waited"), gave_up_page.count("class=ran")) == (1, 0)
    assert gave_up_waiting.verdict == "no result"
    assert (ran_page.count("class=waited"), ran_page.count("class=ran")) == (1, 1)
    assert ran.verdict == "failed"


def test_each_refresh_draws_the_page_with_the_tool_as_it_is_on_disk_then(tmp_path):
    page = tmp_path / "line.html"
    tool = str(Path(line_page.__file__).resolve())

    assert line_page.refresh_command(page, 1, 30) == [
        sys.executable, tool, "--out", str(page), "--days", "1", "--refresh", "30"]
    assert line_page.refresh_command(page, 1, None) == [sys.executable, tool, "--out", str(page), "--days", "1"]


def test_a_short_runs_bar_never_reaches_into_the_next_runs_bar():
    now = T0 + timedelta(hours=2)
    quick = line_page.Run("main verifier", "1 test", T0, T0 + timedelta(seconds=10), exit_code=0, got_the_machine=T0)
    after = line_page.Run("crown colors", "the crown tests", T0 - timedelta(minutes=5),
                              T0 + timedelta(minutes=10), exit_code=0, got_the_machine=T0 + timedelta(seconds=20))

    page = _chart(line_page.render([], [quick, after], now), hours=4)

    bars = sorted((float(left), float(width)) for left, width in
                  re.findall(r"<rect class=ran x=([\d.]+) y=[\d.]+ width=([\d.]+)", page))
    (quick_left, quick_width), (after_left, _) = bars
    assert quick_left + quick_width <= after_left + 0.05


def test_a_run_shows_as_still_going_only_while_its_runner_is_in_line():
    run, in_line =line_page.Run, line_page.InLine
    recorded = [run("crown colors", "the crown tests", minute(0), got_the_machine=minute(1)),
                run("quit faster", "the quit tests", minute(1)),
                run("hud corner", "the hud tests", minute(2), minute(3), exit_code=0, got_the_machine=minute(2))]
    verifier = [run("main verifier", "the whole suite", minute(-30), minute(-20)),
                run("main verifier", "1 test", minute(4), minute(5), line_page.Outcome(ran_s=20, passed=1),
                    got_the_machine=minute(4)),
                run("main verifier", "the whole suite", minute(5), minute(7), got_the_machine=minute(6))]
    line = [in_line("crown colors", "the crown tests", minute(0) - timedelta(seconds=2), True, minute(1)),
            in_line("main verifier", "the whole suite", minute(5) + timedelta(seconds=1), False, minute(6)),
            in_line("night owl", "the whole suite", minute(9), False)]

    runs = line_page.timeline_runs(recorded + verifier, line)

    assert [(each.who, each.what, each.verdict, each.ended) for each in runs] == [
        ("main verifier", "the whole suite", "no result", minute(-20)),
        ("crown colors", "the crown tests", "going", None),
        ("hud corner", "the hud tests", "passed", minute(3)),
        ("main verifier", "1 test", "passed", minute(5)),
        ("main verifier", "the whole suite", "going", None),
        ("night owl", "the whole suite", "going", None),
    ]


def test_a_runs_own_record_says_when_it_waited_and_ran_and_one_that_died_unfinished_is_left_out(tmp_path):
    def stamped(n: int) -> str:
        return minute(n).isoformat(timespec="seconds")

    still_running = 22
    records = [
        {"checkout": "crown-colors-1a2b3c", "branch": "claude/crown-colors-1a2b3c", "what": ["-k", "test_crown"],
         "short": True, "pid": 11, "joined": stamped(0), "got_the_machine": stamped(40), "ended": stamped(55),
         "exit_code": 1},
        {"checkout": "verifying-main", "branch": "HEAD", "what": [], "short": False, "pid": still_running,
         "joined": stamped(10), "got_the_machine": stamped(60)},
        {"checkout": "quit-faster", "branch": "claude/quit-faster", "what": [], "short": False, "pid": 33,
         "joined": stamped(20)},
    ]
    for number, record in enumerate(records):
        (tmp_path / f"{number}.json").write_text(json.dumps(record), encoding="utf-8")

    runs = line_page.line_records(tmp_path, alive=lambda pid: pid == still_running)

    assert [run.checkout for run in runs] == ["crown-colors-1a2b3c", "verifying-main"]

    now = T0 + timedelta(hours=2)
    assert [(run.who, run.what, run.verdict, run.waited_s(now), run.ran_s(now)) for run in runs] == [
        ("crown colors", "the crown tests", "failed", 2400.0, 900.0),
        ("main verifier", "the whole suite", "going", 3000.0, 3600.0),
    ]


def test_a_runs_record_says_which_claude_session_asked_for_it(tmp_path):
    record = {"checkout": "hover-check", "branch": "HEAD", "session": "5e55-10n", "what": [], "short": False,
              "pid": 11, "joined": T0.isoformat(timespec="seconds"), "ended": T0.isoformat(timespec="seconds"),
              "exit_code": 0}
    (tmp_path / "0.json").write_text(json.dumps(record), encoding="utf-8")

    [run] = line_page.line_records(tmp_path, alive=lambda pid: False)

    assert run.session == "5e55-10n"


def test_a_recorded_run_takes_the_place_of_the_same_run_read_from_the_verifiers_log():
    recorded = line_page.Run("crown colors", "the crown tests", T0, T0 + timedelta(minutes=55),
                                 got_the_machine=T0 + timedelta(minutes=40), exit_code=1)
    read_elsewhere = [line_page.Run("crown colors", "the crown tests", T0 - timedelta(seconds=3),
                                        T0 + timedelta(minutes=56), exit_code=1),
                      line_page.Run("quit faster", "the quit tests", T0, T0 + timedelta(minutes=5), exit_code=0)]

    assert line_page.merged_runs([recorded], read_elsewhere) == [recorded, read_elsewhere[1]]


def process(pid, parent, command, started_minute, cwd):
    return line_page.Process(pid, parent, command, (T0 + timedelta(minutes=started_minute)).timestamp(), cwd)


VENV = "C:\\fun_time\\.venv\\Scripts\\python.exe"


def test_the_line_now_is_read_from_what_is_running_holder_first_then_short_runs_then_full_ones():
    processes = [
        process(10, 1, f"{VENV} -m tests.integration.hidden_desktop --changed", 0, "C:\\worktrees\\crown"),
        process(11, 10, f"{VENV} -m tests.integration.hidden_desktop --changed", 0, "C:\\worktrees\\crown"),
        process(20, 1, f"{VENV} -m tests.integration.hidden_desktop -k test_quit_integration", 5, "C:\\worktrees\\quit"),
        process(21, 20, f"{VENV} -m tests.integration.hidden_desktop -k test_quit_integration", 5, "C:\\worktrees\\quit"),
        process(30, 1, f"{VENV} -m tests.integration.hidden_desktop", 7, "C:\\fun_time\\.claude\\worktrees\\verifying-main"),
        replace(process(31, 30, f"{VENV} -m tests.integration.hidden_desktop", 7,
                        "C:\\fun_time\\.claude\\worktrees\\verifying-main"), session="5e55-10n"),
        process(32, 31, "C:\\Python314\\python.exe -m pytest tests/integration", 9, "C:\\fun_time\\.claude\\worktrees\\verifying-main"),
        process(40, 1, f"{VENV} -m tests.integration.hidden_desktop -k test_hud_integration", 12, "C:\\worktrees\\hud"),
    ]
    branches = {"C:\\worktrees\\crown": "claude/crown-colors-1a2b3c", "C:\\worktrees\\quit": "claude/quit-faster",
                "C:\\worktrees\\hud": "claude/hud-corner"}

    line = line_page.the_line_now(processes, branch_of=branches.get)

    assert [(entry.who, entry.what, entry.holding, entry.since) for entry in line] == [
        ("main verifier", "the whole suite", True, T0 + timedelta(minutes=7)),
        ("quit faster", "the quit tests", False, T0 + timedelta(minutes=5)),
        ("hud corner", "the hud tests", False, T0 + timedelta(minutes=12)),
        ("crown colors", "what its change needs", False, T0),
    ]
    assert [entry.got_the_machine for entry in line] == [T0 + timedelta(minutes=9), None, None, None]
    assert [entry.checkout for entry in line] == ["verifying-main", "quit", "hud", "crown"]
    assert [entry.session for entry in line] == ["5e55-10n", "", "", ""]
