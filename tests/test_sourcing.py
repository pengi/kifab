"""Fab, supplier and library source as separate, configurable roles."""

import dataclasses
import json
from pathlib import Path

import pytest

from kifab.cli.main import main
from kifab.core.config import Config, ConfigError, PartsConfig, parse_config
from kifab.core.init import init_project
from kifab.core.inventory import library_contents
from kifab.core.library import vendor_part
from kifab.core.lockfile import LOCK_VERSION, load_lockfile, parse_lockfile
from kifab.core.project import Project
from kifab.core.sourcing import sourcing_for
from kifab.fabs.base import get_fab
from kifab.libraries.base import available_library_sources
from kifab.suppliers.base import available_suppliers
from tests.conftest import FIXTURES


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))


def _with_parts(project: Project, parts: PartsConfig) -> Project:
    assert project.config is not None
    config = dataclasses.replace(project.config, parts=parts)
    config.save(project.directory)
    return Project.open(project.directory)


def test_registries() -> None:
    assert "jlcpcb" in available_suppliers()
    assert "easyeda" in available_library_sources()


def test_fab_defaults() -> None:
    sourcing = sourcing_for(get_fab("jlcpcb"))
    assert [s.name for s in sourcing.suppliers] == ["jlcpcb"]
    assert sourcing.library.name == "easyeda"
    assert sourcing.library_name == "jlcpcb"
    supplier = sourcing.library_supplier()
    assert supplier is not None and supplier.name == "jlcpcb"


def test_parts_config_overrides_and_round_trip() -> None:
    parts = PartsConfig(
        suppliers=("jlcpcb",), library_source="easyeda", library="parts"
    )
    config = Config(fab="jlcpcb", project="demo", parts=parts)
    text = config.dumps()
    assert "[parts]" in text
    assert parse_config(text) == config
    assert sourcing_for(get_fab("jlcpcb"), parts).library_name == "parts"
    assert config.rule_options().library == "parts"


def test_default_parts_are_not_written() -> None:
    assert "[parts]" not in Config(fab="jlcpcb", project="demo").dumps()


@pytest.mark.parametrize(
    "parts, message",
    [
        (PartsConfig(suppliers=("nosuch",)), "unknown supplier 'nosuch'"),
        (PartsConfig(library_source="nosuch"), "unknown library source 'nosuch'"),
    ],
)
def test_unknown_names(parts: PartsConfig, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        sourcing_for(get_fab("jlcpcb"), parts)


def test_bad_suppliers_value() -> None:
    with pytest.raises(ConfigError, match="parts.suppliers"):
        parse_config('fab = "jlcpcb"\nproject = "x"\n[parts]\nsuppliers = [1]\n')


def test_lock_v1_is_read_as_easyeda() -> None:
    lock = parse_lockfile(
        'version = 1\n[[part]]\nid = "C900001"\nfab = "jlcpcb"\n'
        'source = "easyeda2kicad 1.0.1"\n'
    )
    assert lock.parts[0].library_source == "easyeda"
    assert f"version = {LOCK_VERSION}" in lock.dumps()
    assert 'library_source = "easyeda"' in lock.dumps()


def test_vendor_into_a_named_library(tmp_path: Path, offline: None) -> None:
    project = init_project(tmp_path / "p", "p", "jlcpcb").project
    project = _with_parts(project, PartsConfig(library="parts"))
    result = vendor_part(project, "C900001")
    assert result.symbols and all(s.startswith("parts:") for s in result.symbols)
    assert (project.directory / "lib/parts/parts.kicad_sym").is_file()
    assert '(name "parts")' in (project.directory / "sym-lib-table").read_text()
    lock = load_lockfile(project.lock_path)
    assert lock is not None and lock.parts[0].library_source == "easyeda"
    assert [p.id for p in library_contents(project).parts] == ["C900001"]
    rules = project.design_rules()
    assert rules is not None and rules.options.library == "parts"


def test_search_supplier_option(
    tmp_path: Path, offline: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["component", "search", "C900002", "--supplier", "jlcpcb", "--json"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["supplier"] == "jlcpcb" and out["fab"] == "jlcpcb"
    assert [h["id"] for h in out["hits"]] == ["C900002"]
    assert main(["component", "search", "x", "--supplier", "nosuch"]) == 2
