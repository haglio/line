import json
import os
import re
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app_support.integration_runs import RunRecord, read_runs, write_run
from app_support.win32 import process_started
from shared_ui.icons_svg import svg_icon

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
    some = line_page.NAMED
    assert line_page.describe(["-x"], line_page.ALL) == "the whole suite"
    assert line_page.describe(["--repeat-changed", "origin/main"], line_page.NO_FLAKES) == (
        "nondeterministic test prevention")
    assert line_page.describe(["--changed", "tests/integration/test_crown_integration.py"], line_page.CHANGED) == (
        "only all relevant tests")
    assert line_page.describe(["-k", "test_branch_session_integration"], some) == "the branch session tests"
    assert line_page.describe(["-k", "test_a_player_dies_at_startup_integration"], some) == (
        'the "a player dies at startup" tests')
    assert line_page.describe(["-k", "test_quit_integration or test_crown_integration"], some) == "tests from 2 files"
    assert line_page.describe(["tests/integration/test_crown_integration.py",
                               "tests/integration/test_quit_integration.py::test_a_quit"], some) == "tests from 2 files"


def test_a_short_run_whose_words_name_no_test_file_is_some_tests_never_the_whole_suite():
    assert line_page.describe(["-x", "-k", "hud"], line_page.NAMED) == "some tests"


def test_each_run_is_of_the_kind_its_record_says_it_ran():
    def kind(branch: str = "claude/crown-colors-1a2b3c", **changes) -> str:
        [run] = line_page.runs_from([record(branch, joined=0, **changes)], still_going=all_going)
        return run.kind

    verifier = {"branch": "HEAD", "checkout": "verifying-main", "short": True}
    assert kind(what=["-x"]) == line_page.ALL
    assert kind(what=["-x", "-k", "hud"], short=True) == line_page.NAMED
    assert kind(what=["-x", "tests/integration/test_crown_integration.py"], short=True) == line_page.NAMED
    assert kind(what=["--changed", "tests/integration/test_crown_integration.py"], short=True) == line_page.CHANGED
    assert kind(what=["--changed"]) == line_page.ALL
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
                        got_the_machine=minute(5), short=True, kind=line_page.NAMED)

    chart = _chart(line_page.render([], [run], now=minute(60)), hours=4)

    [(bar_x, bar_y, bar_height)] = _bars(chart)
    [(kind, icon_x, icon_y)] = _icons(chart)
    assert (kind, icon_x) == ("named", bar_x)
    assert icon_y >= bar_y + bar_height


def _icon(chart: str, kind: str) -> str:
    return re.search(rf"<g class='icon {kind} [\w-]+' [^>]*>(.*?)</g>", chart, re.DOTALL)[1]


def _one_run_of(kind: str) -> str:
    run = line_page.Run("crown colors", "the crown tests", minute(0), minute(20), exit_code=0,
                        got_the_machine=minute(5), kind=kind)
    return _chart(line_page.render([], [run], now=minute(60)), hours=4)


@pytest.mark.parametrize(("kind", "mark"), [
    (line_page.ALL, "grid"), (line_page.CHANGED, "cells_in_circle"), (line_page.NAMED, "pair_in_circle"),
    (line_page.NO_FLAKES, "snowflake_struck_out"), (line_page.AGAIN, "warning"),
    (line_page.MEASURING, "jigsaw"),
])
def test_each_kind_of_run_wears_the_familys_mark_for_it(kind, mark):
    assert svg_icon(mark, size=line_page.ICON_H) in _icon(_one_run_of(kind), kind.replace(" ", "-"))


def test_icons_of_runs_that_start_close_together_in_one_row_spread_apart_instead_of_overlapping():
    whole = line_page.Run("crown colors", "the whole suite", minute(0), minute(1), exit_code=0,
                          got_the_machine=minute(0))
    repeat = line_page.Run("crown colors", "its new tests, over and over", minute(1), minute(2), exit_code=0,
                           got_the_machine=minute(1), short=True, kind=line_page.NO_FLAKES)

    chart = _chart(line_page.render([], [whole, repeat], now=minute(60)), hours=4)

    [(_, first_x, first_y), (_, second_x, second_y)] = sorted(_icons(chart), key=lambda icon: icon[1])
    assert first_y == second_y
    assert second_x >= first_x + line_page.ICON_H


def test_the_icon_of_a_run_still_waiting_at_the_right_edge_stays_on_the_chart():
    waiting = line_page.Run("crown colors", "the whole suite", minute(50))

    chart = _chart(line_page.render([waiting], [waiting], now=minute(60)), hours=4)

    [(_, icon_x, _)] = _icons(chart)
    chart_width = float(re.search(r"viewBox='0 0 ([\d.]+) ", chart)[1])
    assert icon_x + line_page.ICON_H <= chart_width


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


def test_the_icon_and_line_of_a_run_still_waiting_in_line_wear_the_gray_of_its_waiting_line():
    waiting = line_page.Run("crown colors", "the whole suite", minute(50))

    chart = _chart(line_page.render([waiting], [waiting], now=minute(60)), hours=4)

    assert "<g class='icon all waiting' " in chart
    assert "<polygon class='stem waiting' " in chart
    assert ".icon.waiting, .stem.waiting { color:var(--waiting) }" in line_page.STYLE


def test_the_legend_shows_each_kinds_icon_beside_what_it_stands_for():
    page = line_page.render([], [], now=T0)

    keys = re.findall(r"<span class=key[^>]*><span class='icon ([\w-]+) [^']*'><svg .*?</svg></span>([^<]+)</span>",
                      re.search(r"<div class=kinds>(.*?)</div>", page)[1])
    assert keys == [("all", "The whole suite"), ("measuring", "Update relevance map"),
                    ("changed", "Only all relevant tests"), ("named", "Specific tests"),
                    ("no-flakes", "Nondeterministic test prevention"), ("again", "Rerun failed tests")]


def test_the_relevance_map_and_its_relevant_tests_share_a_tooltip_and_the_ten_runs_moved_into_one():
    page = line_page.render([], [], now=T0)

    tips = {kind: tip for tip, kind in
            re.findall(r"<span class=key data-tip='([^']*)'><span class='icon ([\w-]+) ", page)}
    assert tips["measuring"] == tips["changed"]
    assert "relevance map" in tips["changed"].lower()
    assert "10" in tips["no-flakes"]
    assert "[data-tip]" in line_page.SCRIPT

    def layer(selector: str) -> int:
        return int(re.search(rf"{re.escape(selector)} \{{[^}}]*z-index:(\d+)", line_page.STYLE)[1])

    assert layer("#tip") > layer(".frozen")


def _session_lines(chart: str) -> list[tuple[float, float]]:
    return [(float(x), float(x) + float(width)) for x, width in
            re.findall(r"<rect class=session x=([\d.]+) y=[\d.]+ width=([\d.]+) ", chart)]


def test_a_rows_session_is_a_dotted_line_from_when_it_was_spawned_to_when_it_was_archived():
    run = line_page.Run("crown colors", "the whole suite", minute(10), minute(20), exit_code=0,
                        got_the_machine=minute(12), session="5e55-10n")
    spans = {"5e55-10n": line_page.Lifetime(spawned=minute(0), archived=minute(30))}

    page = line_page.render([], [run], now=minute(60), sessions=line_page.Sessions(lifetimes=spans))

    whole_hour = _chart(page, hours=1)
    [(start, end)] = _session_lines(whole_hour)
    [(bar_x, _, _)] = _bars(whole_hour)
    per_minute = line_page.CHART_W / 60
    assert start == line_page.LABEL_W
    assert end == pytest.approx(line_page.LABEL_W + 30 * per_minute, abs=0.1)
    assert bar_x == pytest.approx(line_page.LABEL_W + 12 * per_minute, abs=0.1)


def _css_units(selector: str, attribute: str) -> float:
    rule = re.search(rf"{re.escape(selector)} \{{([^}}]*)\}}", line_page.STYLE)[1]
    return float(re.search(rf"(?:^|[ ;]){attribute}:calc\(([\d.]+) \* var\(--unit\)\)", rule)[1])


def test_the_legend_names_the_dotted_line_session_shows_three_dots_or_more_and_says_spawned_to_archived_on_hover():
    run = line_page.Run("crown colors", "the whole suite", minute(10), minute(20), exit_code=0,
                        got_the_machine=minute(12), session="5e55-10n")
    spans = {"5e55-10n": line_page.Lifetime(spawned=minute(0), archived=minute(30))}

    page = line_page.render([], [run], now=minute(60), sessions=line_page.Sessions(lifetimes=spans))

    tip = re.search(r"<span class=key data-tip='([^']*)'><span class='swatch session-key'></span>Session</span>", page)[1]
    assert "spawned" in tip
    assert "archived" in tip
    dots = _css_units(".swatch.session-key", "width") / line_page.DOT_EVERY
    assert dots >= len("...")
    when = re.search(r"<rect class=session [^>]*><title>([^<]*)</title></rect>", _chart(page, hours=1))[1]
    assert when == f"Session spawned {line_page._clock(minute(0))}, archived {line_page._clock(minute(30))}"


def test_the_legends_bars_dots_and_icons_are_drawn_the_size_the_chart_draws_them():
    assert _css_units(".swatch", "height") == line_page.RAN_H
    assert _css_units(".swatch.waited-key", "height") == line_page.WAITED_H
    assert _css_units(".swatch.session-key", "height") == line_page.SESSION_H
    assert _css_units(".kinds svg", "height") == line_page.ICON_H
    assert "--unit" in line_page.SCRIPT
    assert "viewBox.baseVal.width" in line_page.SCRIPT


def test_a_sessions_dots_pass_under_its_runs_bars_waiting_or_running_without_showing_through():
    run = line_page.Run("crown colors", "the whole suite", minute(10), minute(20), exit_code=0,
                        got_the_machine=minute(12), session="5e55-10n")
    spans = {"5e55-10n": line_page.Lifetime(spawned=minute(0), archived=minute(30))}

    chart = _chart(line_page.render([], [run], now=minute(60), sessions=line_page.Sessions(lifetimes=spans)), hours=1)

    assert chart.index("<rect class=session ") < chart.index("<rect class=waited ") < chart.index("<rect class=ran ")
    see_through = re.findall(r"\.run \.waited \{[^}]*\}|\.run \.ran \{[^}]*\}", line_page.STYLE)
    assert see_through
    assert not any("opacity" in rule for rule in see_through)


def test_a_second_run_waiting_while_the_first_runs_never_draws_its_gray_over_the_first_runs_bar():
    running = line_page.Run("crown colors", "the whole suite", minute(0), minute(20), exit_code=0,
                            got_the_machine=minute(5), session="5e55-10n")
    waiting = line_page.Run("crown colors", "the whole suite", minute(10), minute(40), exit_code=0,
                            got_the_machine=minute(20), session="5e55-10n")

    chart = _chart(line_page.render([], [running, waiting], now=minute(60)), hours=1)

    assert chart.rindex("<rect class=waited ") < chart.index("<rect class=ran ")


def test_an_open_sessions_dotted_line_runs_on_to_now():
    run = line_page.Run("crown colors", "the whole suite", minute(10), session="5e55-10n")
    spans = {"5e55-10n": line_page.Lifetime(spawned=minute(0), archived=None)}

    whole_hour = _chart(line_page.render([run], [run], now=minute(60), sessions=line_page.Sessions(lifetimes=spans)), hours=1)

    [(_, end)] = _session_lines(whole_hour)
    assert end == pytest.approx(line_page.LABEL_W + line_page.CHART_W, abs=0.1)


def _app_session(folder: Path, app_id: str, **fields) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{app_id}.json").write_text(json.dumps({"sessionId": app_id, **fields}), encoding="utf-8")


def _ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def test_the_claude_apps_records_say_when_each_session_was_spawned_and_whether_it_is_archived(tmp_path):
    account = tmp_path / "Claude_x" / "LocalCache" / "Roaming" / "Claude" / "claude-code-sessions" / "org" / "me"
    _app_session(account, "local_one", cliSessionId="5e55-10n", priorCliSessionIds=["5e55-0ld"],
                 createdAt=_ms(minute(0)), lastActivityAt=_ms(minute(40)), isArchived=True)
    _app_session(account, "local_two", cliSessionId="0pen-5e5", createdAt=_ms(minute(5)),
                 lastActivityAt=_ms(minute(50)), isArchived=False)
    (account / "local_torn.json").write_text("{", encoding="utf-8")

    sessions = sorted(line_page.app_sessions([(tmp_path, line_page.CLAUDE_APP_SESSIONS[0][1])]),
                      key=lambda session: session.app_id)

    assert sessions == [
        line_page.AppSession("local_one", ("5e55-10n", "5e55-0ld"), minute(0), True, minute(40)),
        line_page.AppSession("local_two", ("0pen-5e5",), minute(5), False, minute(50)),
    ]


def test_a_session_kept_in_two_places_is_read_from_the_first_place_the_app_keeps_sessions(tmp_path):
    packaged, plain = tmp_path / "packaged" / "org" / "me", tmp_path / "plain" / "org" / "me"
    _app_session(packaged, "local_one", cliSessionId="5e55-10n", createdAt=_ms(minute(0)),
                 lastActivityAt=_ms(minute(40)), isArchived=True)
    _app_session(plain, "local_one", cliSessionId="5e55-10n", createdAt=_ms(minute(0)),
                 lastActivityAt=_ms(minute(10)), isArchived=False)

    [session] = line_page.app_sessions([(tmp_path / "packaged", "*/*/local_*.json"),
                                        (tmp_path / "plain", "*/*/local_*.json")])

    assert (session.archived, session.last_active) == (True, minute(40))


def test_line_writes_down_the_moment_it_first_sees_an_open_session_archived_and_keeps_that_moment(tmp_path):
    seen = tmp_path / "sessions_seen.json"
    still_open = line_page.AppSession("local_one", ("5e55-10n",), minute(0), False, minute(20))
    archived = replace(still_open, archived=True, last_active=minute(25))

    assert line_page.archive_moments(seen, [still_open], now=minute(30)) == {}
    assert line_page.archive_moments(seen, [archived], now=minute(40)) == {"local_one": minute(40)}
    assert line_page.archive_moments(seen, [archived], now=minute(50)) == {"local_one": minute(40)}


def test_a_session_ends_where_line_saw_it_archived_or_at_its_last_activity_when_line_never_saw_it_open():
    seen_archived = line_page.AppSession("local_one", ("5e55-10n", "5e55-0ld"), minute(0), True, minute(25))
    archived_unseen = line_page.AppSession("local_two", ("0ld-5e55",), minute(5), True, minute(15))
    still_open = line_page.AppSession("local_three", ("0pen-5e5",), minute(10), False, minute(55))

    spans = line_page.lifetimes([seen_archived, archived_unseen, still_open], {"local_one": minute(40)})

    assert spans == {"5e55-10n": line_page.Lifetime(minute(0), minute(40)),
                     "5e55-0ld": line_page.Lifetime(minute(0), minute(40)),
                     "0ld-5e55": line_page.Lifetime(minute(5), minute(15)),
                     "0pen-5e5": line_page.Lifetime(minute(10), None)}


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


def test_a_run_that_joins_while_another_of_its_sessions_runs_is_going_gets_a_timeline_of_its_own():
    running = line_page.Run("crown colors", "the whole suite", minute(0), minute(20), exit_code=0,
                            got_the_machine=minute(0))
    joined_meanwhile = line_page.Run("crown colors", "the crown tests", minute(10), minute(40), exit_code=0,
                                     got_the_machine=minute(20))
    afterwards = line_page.Run("crown colors", "the whole suite", minute(45), minute(50), exit_code=0,
                               got_the_machine=minute(45))

    chart = _chart(line_page.render([], [running, joined_meanwhile, afterwards], now=minute(60)), hours=1)

    [first, second, third] = sorted(_bars(chart))
    assert second[1] - first[1] == line_page.LANE_H
    assert third[1] == first[1]
    assert _rows(chart) == ["crown colors"]


def _two_runs_of_one_session_at_once_and_another_sessions_run() -> list[line_page.Run]:
    return [line_page.Run("crown colors", "the whole suite", minute(0), minute(20), exit_code=0,
                          got_the_machine=minute(0)),
            line_page.Run("crown colors", "the crown tests", minute(10), minute(40), exit_code=0,
                          got_the_machine=minute(20)),
            line_page.Run("quit faster", "the whole suite", minute(41), minute(50), exit_code=0,
                          got_the_machine=minute(41))]


def test_a_sessions_timelines_sit_closer_together_than_two_sessions_rows():
    chart = _chart(line_page.render([], _two_runs_of_one_session_at_once_and_another_sessions_run(), now=minute(60)),
                   hours=1)

    running_y, meanwhile_y, other_sessions_y = (y for _, y, _ in sorted(_bars(chart)))
    assert abs(meanwhile_y - running_y) < min(abs(other_sessions_y - running_y), abs(other_sessions_y - meanwhile_y))


def test_a_bracket_joins_a_sessions_timelines_to_its_name_and_a_session_with_one_timeline_has_none():
    chart = _chart(line_page.render([], _two_runs_of_one_session_at_once_and_another_sessions_run(), now=minute(60)),
                   hours=1)

    [bracket] = re.findall(r"<g class=bracket>(.*?)</g>", chart)
    (bar_x, bar_y, bar_w, bar_h), *ticks = [
        tuple(map(float, sizes)) for sizes in
        re.findall(r"<rect x=([\d.]+) y=([\d.]+) width=([\d.]+) height=([\d.]+)></rect>", bracket)]
    middles = [y + height / 2 for _, y, height in sorted(_bars(chart))[:2]]
    assert (bar_y + bar_w / 2, bar_y + bar_h - bar_w / 2) == pytest.approx((min(middles), max(middles)))
    assert sorted(tick_y + tick_h / 2 for _, tick_y, _, tick_h in ticks) == pytest.approx(sorted(middles))
    assert all(bar_x == tick_x and tick_x + tick_w <= line_page.LABEL_W for tick_x, _, tick_w, _ in ticks)
    assert bar_x > line_page.LABEL_W - 10


def test_a_sessions_name_and_place_in_line_sit_at_the_middle_of_its_timelines():
    running = line_page.Run("crown colors", "the whole suite", minute(30), got_the_machine=minute(30))
    waiting = line_page.Run("crown colors", "the crown tests", minute(40))
    alone = line_page.Run("quit faster", "the whole suite", minute(0), minute(20), exit_code=0,
                          got_the_machine=minute(0))

    chart = _chart(line_page.render([running, waiting], [running, waiting, alone], now=minute(60)), hours=1)

    label_y = dict(zip(_rows(chart), (float(y) for y in re.findall(r"<text class=lane x=[\d.]+ y=([\d.]+)>", chart)),
                       strict=True))
    [place_y] = [float(y) for y in re.findall(r"<text class=place x=[\d.]+ y=([\d.]+)>", chart)]
    joining_y, joining_height = map(float, re.search(r"<g class=bracket><rect x=[\d.]+ y=([\d.]+) width=[\d.]+ "
                                                     r"height=([\d.]+)>", chart).groups())
    (_, alone_y, alone_height), _ = sorted(_bars(chart))
    below_the_middle = label_y["quit faster"] - (alone_y + alone_height / 2)
    assert label_y["crown colors"] == place_y == pytest.approx(joining_y + joining_height / 2 + below_the_middle)


def test_a_sessions_dotted_line_runs_along_each_of_its_timelines():
    *crown, other = _two_runs_of_one_session_at_once_and_another_sessions_run()
    crown = [replace(run, session="5e55-10n") for run in crown]
    spans = {"5e55-10n": line_page.Lifetime(spawned=minute(0), archived=minute(55))}

    chart = _chart(line_page.render([], [*crown, other], now=minute(60), sessions=line_page.Sessions(lifetimes=spans)),
                   hours=1)

    dots = sorted(float(y) + float(height) / 2 for y, height in
                  re.findall(r"<rect class=session x=[\d.]+ y=([\d.]+) width=[\d.]+ height=([\d.]+)", chart))
    assert dots == pytest.approx(sorted(y + height / 2 for _, y, height in sorted(_bars(chart))[:2]))


def test_each_timelines_icons_sit_in_the_gutter_under_its_own_bars():
    chart = _chart(line_page.render([], _two_runs_of_one_session_at_once_and_another_sessions_run(), now=minute(60)),
                   hours=1)

    by_start = zip(sorted(_bars(chart)), sorted(_icons(chart), key=lambda icon: icon[1]), strict=True)
    assert all(bar_y + bar_height <= icon_y < bar_y + line_page.LANE_H
               for (_, bar_y, bar_height), (_, _, icon_y) in by_start)


def test_hours_with_no_runs_draw_an_empty_chart_with_no_negative_sizes():
    chart = _chart(line_page.render([], [], now=T0), hours=1)

    assert re.search(r"viewBox='0 0 [\d.]+ ([\d.]+)'", chart)[1] == str(line_page.CHART_TOP + 6)
    assert "=-" not in chart


def test_the_run_holding_the_machine_is_first_in_line_from_what_it_wrote_down_alone():
    waiting = [record(f"claude/waiting-{name}", joined=n, short=n % 2 == 0)
               for n, name in enumerate(["one", "two", "three", "four", "five", "six"], start=1)]
    holder = record("claude/started-through-a-script", joined=0, got_the_machine=minute(40))
    runs = line_page.runs_from([*waiting, holder], still_going=all_going)

    chart = _chart(line_page.render(line_page.the_line(runs), runs, now=minute(60)), hours=4)

    in_line = [str(place) for place in range(1, len(waiting) + 2)]
    assert _rows(chart)[0] == "started through a script"
    assert _places(chart) == in_line
    assert len(re.findall(r"<g class='run going' [^>]*><rect class=hit ", chart)) == len(in_line)
    assert len(re.findall(r"<g class='run going' [^>]*><rect class=ran ", chart)) == 1


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


def test_a_run_stopped_before_its_turn_never_ran_so_it_is_left_off_however_it_ended():
    left_the_line = record("claude/quit-faster", joined=20, ended=minute(31))
    vanished_while_waiting = record("claude/hud-corner", joined=20, seen=minute(31))

    assert line_page.runs_from([left_the_line, vanished_while_waiting], still_going=none_going) == []


def test_a_runs_record_says_which_claude_session_asked_for_it():
    [run] = line_page.runs_from([record("HEAD", joined=0, session="5e55-10n", got_the_machine=minute(0),
                                        ended=minute(0), exit_code=0)], still_going=none_going)

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

    chart = _chart(line_page.render([], [named, unnamed], now=T0 + timedelta(hours=1), sessions=line_page.Sessions(names=names)), hours=4)

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

    chart = _chart(line_page.render([run], [run], now=T0 + timedelta(minutes=5), sessions=line_page.Sessions(names=names)), hours=4)

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
                                    sessions=line_page.Sessions(names={"5e55-10n": long_name})), hours=4)

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
                     r"(?:(?!</div>).)*</div><div class=kinds>(?:(?!</div>).)*</div></div>", page)


def test_the_legend_buttons_and_times_stay_at_the_top_of_the_window_while_the_rows_scroll_under_them():
    run = line_page.Run("crown colors", "the whole suite", minute(0), minute(20), exit_code=0, got_the_machine=minute(5))
    page = line_page.render([], [run], now=minute(60))

    frozen = re.search(r"<div class=frozen>(.*?)</div><figure ", page, re.DOTALL)[1]
    assert "<div class=chart-head>" in frozen
    times = re.findall(r"<div class='times period' data-hours=(\d+)( hidden)?><svg [^>]*>(.*?)</svg></div>", frozen)
    assert [(hours, hidden) for hours, hidden, _ in times] == [("1", " hidden"), ("4", ""), ("12", " hidden"),
                                                                ("24", " hidden")]
    assert all("class=tick" in ticks for _, _, ticks in times if _ != " hidden")
    assert "class=tick" not in _chart(page, hours=4)
    assert re.search(r"\.frozen \{[^}]*position:sticky; top:0", line_page.STYLE)


def test_the_page_reloads_itself_by_its_own_clock_rather_than_by_a_refresh_tag():
    page = line_page.render([], [], now=T0, refresh_s=30)

    assert "<body data-refresh=30>" in page
    assert "http-equiv=refresh" not in page


def test_the_page_is_dark_whatever_the_system_prefers():
    page = line_page.render([], [], now=T0)

    assert "prefers-color-scheme" not in page
    assert f"--bg:{line_page.DARK_BACKGROUND}" in page


def test_the_legend_starts_with_currently_running():
    page = line_page.render([], [], now=T0)

    swatches = re.findall(r"<span class=key><span class='swatch ([^']+)'></span>", page)
    going = re.search(r"<span class='swatch going'></span><span class='icon going'>(<svg .*?</svg>)</span> ([^<]*)</span>",
                      page)
    assert (swatches[0], *going.groups()) == (
        "going", svg_icon("disc", size=line_page.VERDICT_MARK_H), "Currently running")


def test_a_run_not_yet_ended_says_currently_running_once_it_has_the_machine_and_waiting_in_line_before():
    running = line_page.Run("crown colors", "the whole suite", T0, got_the_machine=T0 + timedelta(minutes=5))
    waiting = line_page.Run("quit faster", "the crown tests", T0 + timedelta(minutes=10))

    chart = _chart(line_page.render([], [running, waiting], now=T0 + timedelta(minutes=20)), hours=1)

    heads = {tip.split("|")[0]: tip.split("|")[1] for tip in re.findall(r"<g class='run going' data-tip='([^']*)'>", chart)}
    assert heads == {"Currently running": "crown colors", "Waiting in line": "quit faster"}


def test_a_run_that_waited_and_then_ran_draws_its_gray_wait_and_then_its_running_bar():
    ran = line_page.Run("quit faster", "the whole suite", T0, T0 + timedelta(minutes=30), exit_code=1,
                        got_the_machine=T0 + timedelta(minutes=20))

    ran_page = _chart(line_page.render([], [ran], T0 + timedelta(hours=2)), hours=4)

    assert (ran_page.count("class=waited"), ran_page.count("class=ran"), ran.verdict) == (1, 1, "failed")


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


def test_a_run_is_in_view_from_its_end_or_while_it_goes_on():
    now = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    since = now - timedelta(hours=4)
    ended_before = line_page.Run("a", "b", joined=since - timedelta(hours=2), ended=since - timedelta(minutes=1))
    ended_after = line_page.Run("a", "b", joined=since - timedelta(hours=2), ended=since + timedelta(minutes=1))
    still_going = line_page.Run("a", "b", joined=since - timedelta(hours=9))

    assert [run.in_view(since, now) for run in (ended_before, ended_after, still_going)] == [False, True, True]
