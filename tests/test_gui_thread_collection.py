from __future__ import annotations

from pathlib import Path

import pytest
from shared_ui.gui_thread_guard import assert_every_application_collects_on_its_gui_thread

_CHECKOUT = Path(__file__).resolve().parents[1]


@pytest.mark.repo_check
def test_every_application_line_makes_collects_cycles_on_its_gui_thread():
    assert_every_application_collects_on_its_gui_thread(_CHECKOUT)
