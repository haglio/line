from pathlib import Path

import pytest
from app_support.coverage_gate import assert_config_is_the_familys

pytestmark = pytest.mark.repo_check

ROOT = Path(__file__).resolve().parent.parent

NOT_UNIT_TESTED = ()


def test_the_coverage_config_is_the_familys():
    assert_config_is_the_familys(ROOT / ".coveragerc", NOT_UNIT_TESTED)
