"""Packaging invariants.

These guard the things that are painful to fix after a release, since a
version number on PyPI can never be reused.
"""

from pathlib import Path

import pytest

import sbirgrantsearch

try:  # tomllib landed in 3.11; the package itself supports 3.10.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - depends on interpreter
    tomllib = None

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


@pytest.fixture(scope="module")
def project() -> dict:
    """Parsed [project] table.

    Skips rather than fails where it cannot be read -- on 3.10 (no tomllib)
    and when running against an installed package with no source tree. The
    tests that do not need it still run everywhere.
    """
    if tomllib is None:
        pytest.skip("tomllib requires Python 3.11+")
    if not PYPROJECT.exists():
        pytest.skip("pyproject.toml not present (installed package)")
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]


def test_version_matches_package(project):
    """__version__ and the packaged version must not drift apart."""
    assert project["version"] == sbirgrantsearch.__version__


def test_declared_license_has_a_file(project):
    assert project["license"] == "MIT"
    for pattern in project["license-files"]:
        assert (PYPROJECT.parent / pattern).exists(), f"missing {pattern}"


def test_typed_claim_is_backed_by_marker(project):
    """The Typing :: Typed classifier is a promise about py.typed."""
    if "Typing :: Typed" in project["classifiers"]:
        assert (PYPROJECT.parent / "sbirgrantsearch" / "py.typed").exists()


def test_core_has_no_runtime_dependencies(project):
    """Stdlib-only is a feature; extras are where dependencies belong."""
    assert project["dependencies"] == []


def test_console_script_target_is_importable(project):
    module, _, attr = project["scripts"]["sbirgrantsearch"].partition(":")
    imported = __import__(module, fromlist=[attr])
    assert callable(getattr(imported, attr))


def test_public_api_is_importable():
    """Everything in __all__ must actually resolve."""
    for name in sbirgrantsearch.__all__:
        assert hasattr(sbirgrantsearch, name), f"__all__ exports missing {name}"
