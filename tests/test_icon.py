from shared_ui.app_icon import assert_follows_the_family_spec

from line import line_tray


def test_lines_icon_is_the_familys_pink_l():
    assert_follows_the_family_spec(line_tray.ICON, "L")
