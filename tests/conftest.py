"""Shared fixtures: the synthetic test data (tests/fixtures) and the stand-ins."""

from __future__ import annotations

import shlex
import shutil
import sys
from pathlib import Path

import pytest

from kifab.kicad.cli import ENV_KICAD_CLI

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEMO_NAME = "demo-board"
DEMO = FIXTURES / DEMO_NAME
EXPECTED = FIXTURES / "expected"
STUBS = Path(__file__).resolve().parent / "stubs"
KICAD_CLI_STUB = STUBS / "kicad_cli.py"


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    """A writable copy of the synthetic demo project."""
    dest = tmp_path / DEMO_NAME
    shutil.copytree(DEMO, dest)
    return dest


@pytest.fixture
def kicad_cli_stub(monkeypatch: pytest.MonkeyPatch) -> str:
    """Make ``kicad-cli`` resolve to the stand-in; returns the command line."""
    for var in ("STUB_DRC_FAIL", "STUB_DRC_EXCLUDED", "STUB_FAIL"):
        monkeypatch.delenv(var, raising=False)
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(KICAD_CLI_STUB))}"
    monkeypatch.setenv(ENV_KICAD_CLI, command)
    return command


@pytest.fixture(autouse=True)
def hermetic_git(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every test sees the same git: no user/system config, a fixed identity.

    CI runners have no git identity (``git tag -a`` and ``git commit`` fail
    there), developer machines have one; this makes both behave alike.
    """
    empty = tmp_path_factory.getbasetemp() / "empty-gitconfig"
    empty.touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "kifab tests")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "tests@kifab.invalid")


@pytest.fixture(autouse=True)
def gui_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """kifab-gui keeps its settings and cache here, never in the user's home."""
    home = tmp_path_factory.mktemp("gui-home")
    monkeypatch.setenv("KIFAB_GUI_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def kifab_cache(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """kifab's cache of fetched parts, empty for every test."""
    cache = tmp_path_factory.mktemp("kifab-cache")
    monkeypatch.setenv("KIFAB_CACHE", str(cache))
    return cache
