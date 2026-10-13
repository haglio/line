import json
import os
import re
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app_support.integration_runs import RunRecord, read_runs, write_run
from app_support.win32 import process_started

from line import line_page

T0 = datetime(2026, 10, 10, 20, 0, tzinfo=UTC)


def minute(n: float) -> datetime:
    return T0 + timedelta(minutes=n)


def record(branch: str, joined: float, **changes) -> RunRecord:
    fields = {"checkout": branch.removeprefix("claude/"), "branch": branch, "what": [], "short": False, "pid": 11,
              "joined": minute(joined), "process_started": 1}
    return RunRecord(**{**fields, **changes})


def all_going(_record: RunRecord) -> bool:
    return True


def none_going(_record: RunRecord) -> bool:
    return False


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


def _chart(page: str, hours: int) -> str:
    return re.search(rf"<figure class=period data-hours={hours}[^>]*>(.*?)</figure>", page, re.DOTALL)[1]


def _rows(chart: str) -> list[str]:
    return re.findall(r"<text class=lane [^>]*>([^<]*)", chart)


def _places(chart: str) -> list[str]:
    return re.findall(r"<text class=place [^>]*>(\d+)</text>", chart)


def test_the_run_holding_the_machine_is_first_in_line_from_what_it_wrote_down_alone():
    waiting = [record(f"claude/waiting-{name}", joined=n, short=n % 2 == 0)
               for n, name in enumerate(["one", "two", "three", "four", "five", "six"], start=1)]
    holder = record("claude/started-through-a-script", joined=0, got_the_machine=minute(40))
    runs = line_page.runs_from([*waiting, holder], still_going=all_going)

    chart = _chart(line_page.render(line_page.the_line(runs), runs, now=minute(60)), hours=4)

    in_line = [str(place) for place in range(1, len(waiting) + 2)]
    assert _rows(chart)[0] == "started through a script"
    assert _places(chart) == in_line
    assert chart.count("class='run going'") == len(in_line)
    assert len(re.findall(r"class='run going'[^>]*>(?:(?!</g>).)*class=ran", chart, re.DOTALL)) == 1


def test_the_line_is_the_holder_first_then_short_runs_then_full_ones_each_in_the_order_they_joined():
    runs = line_page.runs_from([
        record("claude/crown-colors-1a2b3c", joined=0, what=["--changed"]),
        record("claude/quit-faster", joined=5, what=["-k", "test_quit_integration"], short=True),
        record("HEAD", joined=7, checkout="verifying-main", got_the_machine=minute(9)),
        record("claude/hud-corner", joined=12, what=["-k", "test_hud_integration"], short=True),
        record("claude/finished-long-ago", joined=1, got_the_machine=minute(2), ended=minute(4), exit_code=0),
    ], still_going=lambda run: run.ended is None)

    line = line_page.the_line(runs)

    assert [(each.who, each.what, each.holding) for each in line] == [
        ("main verifier", "the whole suite", True),
        ("quit faster", "the quit tests", False),
        ("hud corner", "the hud tests", False),
        ("crown colors", "what its change needs", False),
    ]


def test_a_finished_runs_record_says_how_long_it_waited_and_ran_and_how_it_ended():
    [run] = line_page.runs_from([record("claude/crown-colors-1a2b3c", joined=0, what=["-k", "test_crown"],
                                        short=True, got_the_machine=minute(40), ended=minute(55), exit_code=1)],
                                still_going=none_going)

    now = T0 + timedelta(hours=2)
    assert (run.who, run.what, run.verdict, run.waited_s(now), run.ran_s(now)) == (
        "crown colors", "the crown tests", "failed", 2400.0, 900.0)


def test_a_run_still_going_has_waited_until_it_got_the_machine_and_run_since():
    [run] = line_page.runs_from([record("HEAD", joined=10, checkout="verifying-main", got_the_machine=minute(60))],
                                still_going=all_going)

    now = T0 + timedelta(hours=2)
    assert (run.who, run.verdict, run.waited_s(now), run.ran_s(now)) == ("main verifier", "going", 3000.0, 3600.0)


def test_a_run_that_stopped_without_writing_down_how_ended_when_it_was_last_seen_with_no_result():
    stopped = record("claude/quit-faster", joined=20, got_the_machine=minute(25), seen=minute(31))

    [run] = line_page.runs_from([stopped], still_going=none_going)

    assert (run.ended, run.verdict) == (minute(31), "no result")


def test_a_run_from_before_runs_were_seen_that_stopped_unfinished_is_left_out_having_no_true_end():
    unfinished = record("claude/quit-faster", joined=20, got_the_machine=minute(25), process_started=None)

    assert line_page.runs_from([unfinished], still_going=none_going) == []


def test_a_runs_record_says_which_claude_session_asked_for_it():
    [run] = line_page.runs_from([record("HEAD", joined=0, session="5e55-10n", ended=minute(0), exit_code=0)],
                                still_going=none_going)

    assert run.session == "5e55-10n"


def test_a_run_is_still_going_while_the_process_that_wrote_it_runs(tmp_path):
    here = record("claude/this-test", joined=0, pid=os.getpid(), process_started=process_started(os.getpid()))
    write_run(tmp_path, here)

    [run] = line_page.runs_from(read_runs(tmp_path))

    assert run.verdict == "going"


def test_the_rows_put_the_runs_in_line_on_top_in_their_order_in_line():
    runs = [line_page.Run("hud corner", "the hud tests", minute(0), minute(20), exit_code=0,
                          got_the_machine=minute(5)),
            line_page.Run("main verifier", "1 test", minute(10), minute(25), exit_code=0,
                          got_the_machine=minute(12)),
            line_page.Run("quit faster", "the quit tests", minute(30), short=True, got_the_machine=minute(40)),
            line_page.Run("crown colors", "the crown tests", minute(50), short=True)]

    chart = _chart(line_page.render(line_page.the_line(runs), runs, now=minute(60)), hours=4)

    assert _rows(chart) == ["quit faster", "crown colors", "MAIN VERIFIER", "hud corner"]
    assert _places(chart) == ["1", "2"]


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
    run = line_page.Run("fairer line", "the whole suite", T0, checkout="fairer-line", session="5e55-10n")
    names = {"5e55-10n": "steadier example runs", "fairer-line": "some other session"}

    chart = _chart(line_page.render([run], [run], now=T0 + timedelta(minutes=5), names=names), hours=4)

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
    run = line_page.Run("crown colors", "the whole suite", T0, session="5e55-10n")

    chart = _chart(line_page.render([run], [run], now=T0 + timedelta(minutes=5),
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
