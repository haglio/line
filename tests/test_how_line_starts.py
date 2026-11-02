import sys
from pathlib import Path

import pytest
from app_support.launcher import dry_run

REPO_ROOT = Path(__file__).resolve().parents[1]

on_windows = pytest.mark.skipif(sys.platform != "win32", reason="the Windows script host")


@on_windows
def test_the_launch_link_opens_lines_window():
    assert dry_run(REPO_ROOT / "launch_line.vbs").value("arguments") == "-m line --open"


@on_windows
def test_at_sign_in_line_starts_in_the_tray_without_opening_its_window():
    assert dry_run(REPO_ROOT / "launch_line_in_the_tray.vbs").value("arguments") == "-m line"


@on_windows
def test_a_preview_opens_lines_window():
    assert dry_run(REPO_ROOT / "launch_preview_branch.vbs").value("arguments") == "-m line --open"
