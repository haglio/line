from line import ship_time_charts
from line.page_words import minutes_words
from line.ship_time_charts import (
    EVERY_WEEK_HEADING,
    GOT_THROUGH_HEADING,
    SHIP_TIME_HEADING,
    Frame,
    every_week_table,
    got_through_chart,
    nice_top,
    ship_time_chart,
    weeks_section,
)

ONE = {"week": "2026-W43", "deliveries": 9, "median_minutes": 84, "slowest_tenth_minutes": 304, "bug_reports": 13,
       "crashes": 1}
TWO = {"week": "2026-W44", "deliveries": 8, "median_minutes": 144, "slowest_tenth_minutes": 894, "bug_reports": 0,
       "crashes": 2}
QUIET = {"week": "2026-W45", "deliveries": 0, "median_minutes": None, "slowest_tenth_minutes": None, "bug_reports": 1,
         "crashes": 0}


def test_the_top_of_a_chart_is_a_round_number_at_or_above_the_highest_value():
    assert nice_top(0) == 1
    assert nice_top(13) == 20
    assert nice_top(894) == 1000
    assert nice_top(220) == 250


def test_a_higher_value_is_drawn_higher_up_and_a_later_week_further_right():
    frame = Frame(weeks=2, top_value=100)

    assert frame.y(100) < frame.y(0)
    assert frame.x(1) > frame.x(0)


def test_each_week_of_the_ship_time_chart_says_its_numbers_when_pointed_at():
    chart = ship_time_chart([ONE, TWO])

    assert "Week of Oct 26|Shipped: 8|Median: 2.4 h|Slowest tenth: 14.9 h" in chart


def test_a_week_nothing_shipped_leaves_a_gap_in_the_lines_and_says_so():
    chart = ship_time_chart([ONE, QUIET, TWO])

    assert chart.count("<circle class=median") == 2
    assert "Median: \N{EN DASH}" in chart


def test_the_line_s_last_point_carries_its_name_and_value():
    assert "Median 2.4 h" in ship_time_chart([ONE, TWO])


def test_what_got_through_draws_a_bar_only_for_a_count_above_zero():
    chart = got_through_chart([ONE, TWO])

    assert chart.count("<rect class=bugs") == 1
    assert chart.count("<rect class=crashes") == 2
    assert "Bugs you reported: 13|App crashes: 1" in chart


def test_the_table_lists_every_week_newest_first():
    table = every_week_table([ONE, TWO])

    assert table.index("Oct 26") < table.index("Oct 19")
    assert minutes_words(144) in table


def test_a_long_history_labels_every_other_week():
    rows = [{**ONE, "week": f"2026-W{number}"} for number in range(10, 10 + ship_time_charts.LABEL_EVERY_OTHER_WEEK_FROM)]

    assert ship_time_chart(rows).count("class=tick") == 7


def test_the_section_has_both_charts_and_the_table_under_their_headings():
    section = weeks_section([ONE, TWO])

    for heading in (SHIP_TIME_HEADING, GOT_THROUGH_HEADING, EVERY_WEEK_HEADING):
        assert f"<h2>{heading}</h2>" in section


def test_with_no_weeks_the_section_is_not_drawn():
    assert weeks_section([]) == ""


def test_the_ship_time_axis_reads_in_hours_when_the_numbers_are_large_and_in_minutes_when_small():
    assert ">4 h</text>" in ship_time_chart([{**TWO, "slowest_tenth_minutes": 900}])
    assert ">25m</text>" in ship_time_chart([{**ONE, "slowest_tenth_minutes": 100, "median_minutes": 10}])


def test_weeks_with_nothing_shipped_draw_no_line():
    assert "<path" not in ship_time_chart([QUIET])
