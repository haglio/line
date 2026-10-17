import json
import math
import os
import re
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
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


def _shapes(kind: str, shape: type) -> list:
    return [each for each in line_page.ICONS[kind].shapes if isinstance(each, shape)]


def _rows_of(cells: list) -> list[int]:
    by_row: dict[float, int] = {}
    for cell in cells:
        by_row[cell.top] = by_row.get(cell.top, 0) + 1
    return [by_row[top] for top in sorted(by_row)]


def test_the_whole_suites_icon_is_a_square_box_packed_with_a_grid_of_little_squares():
    [box] = _shapes(line_page.ALL, line_page.Box)
    cells = _shapes(line_page.ALL, line_page.Cell)

    assert box.width == line_page.ICON_H
    rows = _rows_of(cells)
    packed = 3
    assert len(rows) >= packed
    assert rows == [len(rows)] * len(rows)
    assert all(box.thickness < cell.left and cell.left + cell.size < box.width - box.thickness for cell in cells)
    assert "class=cell" in _icon(_one_run_of(line_page.ALL), "all")


def _inside_the_ring(cell, ring) -> bool:
    corners = [(cell.left + dx, cell.top + dy) for dx in (0, cell.size) for dy in (0, cell.size)]
    return all(math.dist(corner, ring.middle) < ring.radius - ring.thickness / 2 for corner in corners)


def test_the_tests_a_change_needs_wear_a_puzzle_piece_of_little_squares_in_a_circle_as_big_as_no_flakes():
    [ring] = _shapes(line_page.CHANGED, line_page.Ring)
    [no_flakes_ring] = _shapes(line_page.NO_FLAKES, line_page.Ring)
    cells = _shapes(line_page.CHANGED, line_page.Cell)

    assert (ring.middle, ring.radius) == (no_flakes_ring.middle, no_flakes_ring.radius)
    assert _rows_of(cells) == [2, 5, 2]
    assert all(_inside_the_ring(cell, ring) for cell in cells)


def test_tests_by_name_wear_two_little_squares_side_by_side_in_that_circle():
    [ring] = _shapes(line_page.NAMED, line_page.Ring)
    [no_flakes_ring] = _shapes(line_page.NO_FLAKES, line_page.Ring)
    cells = _shapes(line_page.NAMED, line_page.Cell)

    assert (ring.middle, ring.radius) == (no_flakes_ring.middle, no_flakes_ring.radius)
    assert _rows_of(cells) == [2]
    assert all(_inside_the_ring(cell, ring) for cell in cells)
    assert cells[1].left - cells[0].left > cells[0].size


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


def _outline(drawing: str) -> list[tuple[float, float]]:
    numbers = [float(number) for number in re.findall(r"-?\d+(?:\.\d+)?", re.search(r" d='([^']*)'", drawing)[1])]
    here, points = (numbers[0], numbers[1]), []
    for at in range(2, len(numbers), 6):
        handles = [(numbers[at + n], numbers[at + n + 1]) for n in (0, 2, 4)]
        points += [tuple((1 - s) ** 3 * p0 + 3 * (1 - s) ** 2 * s * p1 + 3 * (1 - s) * s ** 2 * p2 + s ** 3 * p3
                         for p0, p1, p2, p3 in zip(here, *handles, strict=True))
                   for s in (step / 200 for step in range(201))]
        here = handles[-1]
    return points


def _height_across(points: list[tuple[float, float]], x: float, half_a_slice: float = 0.1) -> float:
    near = [py for px, py in points if abs(px - x) < half_a_slice]
    return max(near) - min(near)


def test_update_relevance_map_wears_a_jigsaw_piece_tabbed_left_and_right_and_blanked_top_and_bottom_in_a_square_box():
    [box] = _shapes(line_page.MEASURING, line_page.Box)
    [piece] = _shapes(line_page.MEASURING, line_page.JigsawPiece)
    outline = _outline(piece.svg())
    xs, ys = [x for x, _ in outline], [y for _, y in outline]
    middle, half = line_page.ICON_H / 2, piece.side / 2

    assert box == _shapes(line_page.ALL, line_page.Box)[0]
    assert not _shapes(line_page.MEASURING, line_page.Cell)
    assert box.thickness < min(xs) < max(xs) < box.width - box.thickness
    assert box.thickness < min(ys) < max(ys) < line_page.ICON_H - box.thickness
    for tip, side in ((min(outline), -1), (max(outline), 1)):
        assert abs(tip[1] - middle) < piece.side / 10
        assert abs(tip[0] - middle) - half > piece.side / 4
        tab = [(x, y) for x, y in outline if side * (x - middle) > half]
        neck = _height_across(tab, middle + side * (half + piece.side / 40))
        head = max(_height_across(tab, middle + side * (half + piece.side * n / 40)) for n in range(2, 10))
        assert head > neck * 1.3
    across_the_middle = [y for x, y in outline if abs(x - middle) < piece.side / 20]
    assert min(across_the_middle) - (middle - half) > piece.side / 4
    assert (middle + half) - max(across_the_middle) > piece.side / 4
    assert "class=jigsaw" in _icon(_one_run_of(line_page.MEASURING), "measuring")


def test_the_failed_tests_again_wear_an_exclamation_mark_in_a_warning_triangle():
    [triangle] = _shapes(line_page.AGAIN, line_page.Triangle)
    [bar] = _shapes(line_page.AGAIN, line_page.Stick)
    [dot] = _shapes(line_page.AGAIN, line_page.Dot)

    (point, left, right) = triangle.corners
    assert point[1] < left[1] == right[1]
    assert bar.start[0] == bar.end[0] == dot.middle[0] == point[0]
    assert point[1] < bar.start[1] < bar.end[1] < dot.middle[1] < left[1]
    assert "class=triangle" in _icon(_one_run_of(line_page.AGAIN), "again")


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


def test_the_icon_and_line_of_a_run_still_waiting_in_line_wear_the_gray_of_its_waiting_line():
    waiting = line_page.Run("crown colors", "the whole suite", minute(50))

    chart = _chart(line_page.render([waiting], [waiting], now=minute(60)), hours=4)

    assert "<g class='icon all waiting' " in chart
    assert "<polygon class='stem waiting' " in chart
    assert ".icon.waiting, .stem.waiting { color:var(--waiting) }" in line_page.STYLE


def test_the_legend_shows_each_kinds_icon_beside_what_it_stands_for():
    page = line_page.render([], [], now=T0)

    keys = re.findall(r"<span class=key[^>]*><svg [^>]*><g class='icon ([\w-]+) [^']*'.*?</svg>([^<]+)</span>",
                      re.search(r"<div class=kinds>(.*?)</div>", page)[1])
    assert keys == [("all", "The whole suite"), ("measuring", "Update relevance map"),
                    ("changed", "Only all relevant tests"), ("named", "Specific tests"),
                    ("no-flakes", "Nondeterministic test prevention"), ("again", "Rerun failed tests")]


def test_the_relevance_map_and_its_relevant_tests_share_a_tooltip_and_the_ten_runs_moved_into_one():
    page = line_page.render([], [], now=T0)

    tips = {kind: tip for tip, kind in
            re.findall(r"<span class=key data-tip='([^']*)'><svg [^>]*><g class='icon ([\w-]+) ", page)}
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

    assert re.findall(r"<span class=key><span class='swatch ([^']+)'></span>([^<]*)</span>", page)[0] == (
        "going", "● Currently running")


def test_a_run_not_yet_ended_says_currently_running_once_it_has_the_machine_and_waiting_in_line_before():
    running = line_page.Run("crown colors", "the whole suite", T0, got_the_machine=T0 + timedelta(minutes=5))
    waiting = line_page.Run("quit faster", "the crown tests", T0 + timedelta(minutes=10))

    chart = _chart(line_page.render([], [running, waiting], now=T0 + timedelta(minutes=20)), hours=1)

    heads = {tip.split("|")[0]: tip.split("|")[1] for tip in re.findall(r"<g class='run going' data-tip='([^']*)'>", chart)}
    assert heads == {"Currently running": "crown colors", "Waiting in line": "quit faster"}


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
