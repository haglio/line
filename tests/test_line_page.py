import json
import math
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
    some = line_page.SOME
    assert line_page.describe(["-x"], line_page.ALL) == "the whole suite"
    assert line_page.describe(["--repeat-changed", "origin/main"], line_page.NO_FLAKES) == (
        "its new tests, over and over")
    assert line_page.describe(["-k", "test_branch_session_integration"], some) == "the branch session tests"
    assert line_page.describe(["-k", "test_a_player_dies_at_startup_integration"], some) == (
        'the "a player dies at startup" tests')
    assert line_page.describe(["-k", "test_quit_integration or test_crown_integration"], some) == "tests from 2 files"
    assert line_page.describe(["tests/integration/test_crown_integration.py",
                               "tests/integration/test_quit_integration.py::test_a_quit"], some) == "tests from 2 files"


def test_a_short_run_whose_words_name_no_test_file_is_some_tests_never_the_whole_suite():
    assert line_page.describe(["-x", "-k", "hud"], line_page.SOME) == "some tests"


def test_each_run_is_of_the_kind_its_record_says_it_ran():
    def kind(branch: str = "claude/crown-colors-1a2b3c", **changes) -> str:
        [run] = line_page.runs_from([record(branch, joined=0, **changes)], still_going=all_going)
        return run.kind

    verifier = {"branch": "HEAD", "checkout": "verifying-main", "short": True}
    assert kind(what=["-x"]) == line_page.ALL
    assert kind(what=["-x", "-k", "hud"], short=True) == line_page.SOME
    assert kind(what=["tests/integration/test_crown_integration.py"], short=True) == line_page.SOME
    assert kind(what=["--repeat-changed", "origin/main"], short=True) == line_page.NO_FLAKES
    assert kind("HEAD", what=[], checkout="verifying-main") == line_page.ALL
    assert kind(what=["tests/integration/test_crown_integration.py::test_a_crown"], **verifier) == line_page.AGAIN
    assert kind(what=["tests/integration/test_crown_integration.py", "--no-cov"], **verifier) == line_page.MEASURING


def _bars(chart: str) -> list[tuple[float, float, float]]:
    return [(float(x), float(y), float(height)) for x, y, height in
            re.findall(r"<rect class=ran x=([\d.]+) y=([\d.]+) width=[\d.]+ height=([\d.]+)", chart)]


def _icons(chart: str) -> list[tuple[str, float, float]]:
    return [(kind, float(x), float(y)) for kind, x, y in
            re.findall(r"<g class='icon ([\w-]+) [\w-]+' transform='translate\(([\d.]+),([\d.]+)\)'", chart)]


def test_a_runs_icon_sits_in_the_gutter_under_its_rows_bars_where_its_bar_starts():
    run = line_page.Run("crown colors", "the crown tests", minute(0), minute(20), exit_code=0,
                        got_the_machine=minute(5), short=True, kind=line_page.SOME)

    chart = _chart(line_page.render([], [run], now=minute(60)), hours=4)

    [(bar_x, bar_y, bar_height)] = _bars(chart)
    [(kind, icon_x, icon_y)] = _icons(chart)
    assert (kind, icon_x) == ("some", bar_x)
    assert icon_y >= bar_y + bar_height


def _icon(chart: str, kind: str) -> str:
    return re.search(rf"<g class='icon {kind} [\w-]+' [^>]*>(.*?)</g>", chart, re.DOTALL)[1]


def _one_run_of(kind: str) -> str:
    run = line_page.Run("crown colors", "the crown tests", minute(0), minute(20), exit_code=0,
                        got_the_machine=minute(5), kind=kind)
    return _chart(line_page.render([], [run], now=minute(60)), hours=4)


def test_the_whole_suites_icon_is_the_word_all_in_a_box():
    icon = _icon(_one_run_of(line_page.ALL), "all")

    assert "class=box" in icon
    assert re.search(r"<text [^>]*>ALL</text>", icon)


def test_the_new_tests_ten_times_icon_is_a_snowflake_in_a_slashed_circle_saying_no_flakes():
    shapes = line_page.ICONS[line_page.NO_FLAKES].shapes

    [ring] = [shape for shape in shapes if isinstance(shape, line_page.Ring)]
    sticks = [shape for shape in shapes if isinstance(shape, line_page.Stick)]
    [slash] = [stick for stick in sticks if stick.name == "slash"]
    arms = [stick for stick in sticks if stick.name == "flake"]
    (middle_x, middle_y) = ring.middle
    assert slash.start[0] < middle_x < slash.end[0]
    assert slash.start[1] < middle_y < slash.end[1]
    a_snowflakes_arms = 6
    assert len(arms) >= a_snowflakes_arms
    assert all(math.dist(end, ring.middle) < ring.radius for arm in arms for end in (arm.start, arm.end))
    assert "class=ring" in _icon(_one_run_of(line_page.NO_FLAKES), "no-flakes")


def test_the_failed_tests_again_wear_an_exclamation_mark_and_measuring_a_percent_sign():
    assert re.search(r"<text [^>]*>!</text>", _icon(_one_run_of(line_page.AGAIN), "again"))
    assert re.search(r"<text [^>]*>%</text>", _icon(_one_run_of(line_page.MEASURING), "measuring"))


def test_icons_of_runs_that_start_close_together_in_one_row_spread_apart_instead_of_overlapping():
    whole = line_page.Run("crown colors", "the whole suite", minute(0), minute(1), exit_code=0,
                          got_the_machine=minute(0))
    repeat = line_page.Run("crown colors", "its new tests, over and over", minute(1), minute(2), exit_code=0,
                           got_the_machine=minute(1), short=True, kind=line_page.NO_FLAKES)

    chart = _chart(line_page.render([], [whole, repeat], now=minute(60)), hours=4)

    [(_, first_x, first_y), (_, second_x, second_y)] = sorted(_icons(chart), key=lambda icon: icon[1])
    assert first_y == second_y
    assert second_x >= first_x + line_page.icon_width(line_page.ALL)


def test_the_icon_of_a_run_still_waiting_at_the_right_edge_stays_on_the_chart():
    waiting = line_page.Run("crown colors", "the whole suite", minute(50))

    chart = _chart(line_page.render([waiting], [waiting], now=minute(60)), hours=4)

    [(_, icon_x, _)] = _icons(chart)
    chart_width = float(re.search(r"viewBox='0 0 ([\d.]+) ", chart)[1])
    assert icon_x + line_page.icon_width(line_page.ALL) <= chart_width


def test_runs_of_one_kind_piled_up_in_a_row_share_one_icon():
    measuring = [line_page.Run("main verifier", "the crown tests", minute(n / 5), minute(n / 5 + 0.2), exit_code=0,
                               got_the_machine=minute(n / 5), short=True, kind=line_page.MEASURING) for n in range(3)]
    after_a_while = line_page.Run("main verifier", "the whole suite", minute(30), minute(45), exit_code=0,
                                  got_the_machine=minute(30))

    chart = _chart(line_page.render([], [*measuring, after_a_while], now=minute(60)), hours=4)

    assert [kind for kind, _, _ in sorted(_icons(chart), key=lambda icon: icon[1])] == ["measuring", "all"]


def test_a_line_ties_each_icon_moved_aside_to_the_start_of_its_bar():
    whole = line_page.Run("crown colors", "the whole suite", minute(0), minute(1), exit_code=0,
                          got_the_machine=minute(0))
    repeat = line_page.Run("crown colors", "its new tests, over and over", minute(1), minute(2), exit_code=0,
                           got_the_machine=minute(1), short=True, kind=line_page.NO_FLAKES)

    chart = _chart(line_page.render([], [whole, repeat], now=minute(60)), hours=4)

    def ends(points: str) -> tuple[float, float]:
        (top_left, top_right, bottom_right, bottom_left) = (float(point.split(",")[0]) for point in points.split())
        return round((top_left + top_right) / 2, 1), round((bottom_left + bottom_right) / 2, 1)

    stems = sorted(ends(points) for points in re.findall(r"<polygon class='stem [\w-]+' points='([^']+)'>", chart))
    assert [start for start, _ in stems] == sorted(bar_x for bar_x, _, _ in _bars(chart))
    assert [end for _, end in stems] == sorted(icon_x for _, icon_x, _ in _icons(chart))


def test_an_icon_and_its_line_wear_the_color_of_how_its_run_went():
    failed = line_page.Run("crown colors", "the whole suite", minute(0), minute(20), exit_code=1,
                           got_the_machine=minute(5))

    chart = _chart(line_page.render([], [failed], now=minute(60)), hours=4)

    assert "<g class='icon all failed' " in chart
    assert "<polygon class='stem failed' " in chart


def test_the_legend_shows_each_kinds_icon_beside_what_it_stands_for():
    page = line_page.render([], [], now=T0)

    keys = re.findall(r"<span class=key><svg [^>]*><g class='icon ([\w-]+) [^']*'.*?</svg>([^<]+)</span>",
                      re.search(r"<div class=kinds>(.*?)</div>", page)[1])
    assert [kind for kind, _ in keys] == ["all", "some", "no-flakes", "again", "measuring"]
    assert all(words.strip() for _, words in keys)


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
        record("claude/crown-colors-1a2b3c", joined=0, what=["-x"]),
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
        ("crown colors", "the whole suite", False),
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

    assert re.search(r"<div class=chart-head><div class=legend>(?:(?!</div>).)*</div><div class=periods>"
                     r"(?:(?!</div>).)*</div><div class=kinds>(?:(?!</div>).)*</div></div><figure ", page)


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
