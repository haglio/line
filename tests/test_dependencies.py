import tomllib
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent


def _declared() -> dict[str, Requirement]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {requirement.name: requirement
            for requirement in map(Requirement, pyproject["project"]["dependencies"])}


def _minors_admitted(requirement: Requirement) -> set[str]:
    return {f"6.{minor}" for minor in range(100)
            if any(f"6.{minor}.{patch}" in requirement.specifier for patch in (0, 99))}


def test_pyqt6_and_its_webengine_are_held_to_one_and_the_same_minor_so_pip_cannot_pair_two():
    declared = _declared()
    pyqt6 = _minors_admitted(declared["PyQt6"])

    assert len(pyqt6) == 1, pyqt6
    assert _minors_admitted(declared["PyQt6-WebEngine"]) == pyqt6
