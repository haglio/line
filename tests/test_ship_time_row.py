import json
from datetime import UTC, datetime

from line.page_words import NO_VALUE
from line.ship_time import ShipTimeRecord
from line.ship_time_row import (
    ALREADY_ASKED_TIP,
    MEASURING_WORDS,
    TAKEN_BACK_HEADING,
    WORK_ON_THIS,
    AroundTheChart,
    read_around_the_chart,
    ship_time_row,
)

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)
WEEK_BEFORE = {"week": "2026-W43", "deliveries": 9, "median_minutes": 84, "slowest_tenth_minutes": 304,
               "bug_reports": 13, "crashes": 1}
LAST_WEEK = {"week": "2026-W44", "deliveries": 8, "median_minutes": 144, "slowest_tenth_minutes": 304,
             "bug_reports": 10, "crashes": 1}
THIS_WEEK = {"week": "2026-W45", "deliveries": 2, "median_minutes": 36, "slowest_tenth_minutes": 58,
             "bug_reports": 6, "crashes": 0}


def row(*weeks, taken_back=()):
    return ship_time_row(ShipTimeRecord(list(weeks), list(taken_back)), NOW)


def test_the_row_shows_the_last_finished_week_never_the_one_still_going():
    shown = row(WEEK_BEFORE, LAST_WEEK, THIS_WEEK)

    assert "week of Oct 26" in shown
    assert "2.4 h" in shown
    assert "36 min" not in shown


def test_each_number_says_whether_it_got_worse_or_better_than_the_week_before():
    shown = row(WEEK_BEFORE, LAST_WEEK)

    assert "worse</span> than the week before (84 min)" in shown
    assert "better</span> than the week before (13)" in shown
    assert "Same as the week before" in shown


def test_two_hours_and_more_read_in_hours():
    assert "5.1 h" in row(WEEK_BEFORE, LAST_WEEK)


def test_a_week_with_nothing_shipped_shows_a_dash_and_compares_with_nothing():
    quiet = {**LAST_WEEK, "median_minutes": None}

    shown = row(WEEK_BEFORE, quiet)

    assert NO_VALUE in shown
    assert "No earlier week to compare" in shown


def test_the_first_finished_week_has_nothing_to_compare_with():
    assert "No earlier week to compare" in row(LAST_WEEK)


def test_no_finished_week_yet_says_so():
    assert "no finished week yet" in row(THIS_WEEK)


def test_before_the_first_measure_the_row_says_it_is_measuring():
    assert MEASURING_WORDS in ship_time_row(None, NOW)


def test_a_change_taken_back_is_listed_with_its_number_title_and_day():
    shown = row(LAST_WEEK, taken_back=[{"pull_request": 426, "title": "A made-up <change>",
                                        "taken_back": "2026-10-11T20:05:44"}])

    assert TAKEN_BACK_HEADING in shown
    assert "#426 A made-up &lt;change&gt;, taken back Oct 11" in shown


def test_with_nothing_taken_back_the_list_does_not_appear():
    assert TAKEN_BACK_HEADING not in row(LAST_WEEK)


def test_only_a_number_that_got_worse_has_a_button_and_it_names_the_number_and_the_week():
    shown = row(WEEK_BEFORE, LAST_WEEK)

    assert shown.count(WORK_ON_THIS) == 1
    assert "href='https://line-work.invalid/median_minutes/2026-W44'" in shown


def test_a_number_a_session_was_started_on_this_week_grays_its_button():
    shown = ship_time_row(ShipTimeRecord([WEEK_BEFORE, LAST_WEEK], []), NOW, started={"2026-W44 median_minutes"})

    assert shown.count(WORK_ON_THIS) == 1
    assert "https://line-work.invalid/" not in shown
    assert ALREADY_ASKED_TIP in shown


def test_pointing_at_a_number_says_how_it_is_counted():
    assert "Bugs you reported|Sessions you opened yourself" in row(WEEK_BEFORE, LAST_WEEK).replace("&#x27;", "'")


def test_the_charts_sit_below_the_chart_of_runs_once_weeks_are_known():
    around = AroundTheChart(ship_time=ShipTimeRecord([WEEK_BEFORE, LAST_WEEK], []))

    assert "Every week" in around.below_the_chart()
    assert AroundTheChart().below_the_chart() == ""


def test_what_surrounds_the_chart_is_read_from_line_s_state(tmp_path):
    (tmp_path / "ship_time.json").write_text(json.dumps({"weeks": {"2026-W44": LAST_WEEK}, "taken_back": []}),
                                             encoding="utf-8")

    around = read_around_the_chart(tmp_path, 30)

    assert around.refresh_s == 30
    assert around.ship_time.weeks == [LAST_WEEK]
    assert around.sessions_started == {}
