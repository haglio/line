import tomllib
from pathlib import Path

import pytest
from app_support.dependencies import (
    assert_every_dependency_is_bounded,
    assert_every_import_is_declared,
    assert_every_sibling_is_declared,
    assert_every_sibling_is_pinned,
    assert_the_declared_floor_is_the_one_the_gate_runs,
)
from packaging.requirements import Requirement

pytestmark = pytest.mark.repo_check

ROOT = Path(__file__).resolve().parent.parent
TREES = [ROOT / "line", ROOT / "tests", ROOT / "tools", ROOT / "vulture_whitelist.py"]


def _declared() -> dict[str, Requirement]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {requirement.name: requirement
            for requirement in map(Requirement, pyproject["project"]["dependencies"])}


def _minors_admitted(requirement: Requirement) -> set[str]:
    return {f"6.{minor}" for minor in range(100)
            if any(f"6.{minor}.{patch}" in requirement.specifier for patch in (0, 99))}


def test_every_third_party_import_is_declared():
    assert_every_import_is_declared(ROOT, [ROOT / "line"], ROOT / "pyproject.toml", local=("line",))


def test_every_requirement_has_an_upper_bound():
    assert_every_dependency_is_bounded(ROOT / "pyproject.toml")


def test_every_sibling_this_repo_needs_is_declared():
    assert_every_sibling_is_declared(ROOT, TREES, ROOT / "pyproject.toml")


def test_every_sibling_is_named_at_a_tag():
    assert_every_sibling_is_pinned(ROOT / "pyproject.toml")


def test_the_declared_floor_is_the_one_the_gate_runs():
    assert_the_declared_floor_is_the_one_the_gate_runs(
        ROOT / "pyproject.toml", ROOT / ".github" / "workflows" / "merge-gate.yml")


def test_pyqt6_and_its_webengine_are_held_to_one_and_the_same_minor_so_pip_cannot_pair_two():
    declared = _declared()
    pyqt6 = _minors_admitted(declared["PyQt6"])

    assert len(pyqt6) == 1, pyqt6
    assert _minors_admitted(declared["PyQt6-WebEngine"]) == pyqt6
