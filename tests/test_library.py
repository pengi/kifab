import io
import json
import shlex
import sys
from pathlib import Path

import pytest

from kifab.cli.main import main
from kifab.core.check import run_check
from kifab.core.config import Config, ConfigError
from kifab.core.init import init_project
from kifab.core.issues import Code
from kifab.core.library import (
    fix_attributes,
    rewrite_models,
    sync_parts,
    vendor_part,
)
from kifab.core.lockfile import load_lockfile
from kifab.core.project import KicadOpen, Project
from kifab.core.reporter import RecordingReporter
from kifab.fabs.base import FetchError
from kifab.fabs.jlcpcb.fetch import Easyeda2kicad, FixturePartSource
from kifab.kicad.board import read_footprint_file
from kifab.kicad.symbol_lib import read_symbol_library
from tests.conftest import STUBS, FIXTURES
from tests.helpers import schematic, symbol

#: every part with captured (synthetic) easyeda2kicad output
ALL = ["C900001", "C900002", "C900003", "C900004", "C900005", "C900006"]


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))


@pytest.fixture
def stub_e2k(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KIFAB_OFFLINE", raising=False)
    monkeypatch.setenv(
        "KIFAB_EASYEDA2KICAD",
        f"{shlex.quote(sys.executable)} {shlex.quote(str(STUBS / 'easyeda2kicad.py'))}",
    )


def new_project(tmp_path: Path) -> Project:
    return init_project(tmp_path / "p", "p", "jlcpcb").project


def test_post_processing() -> None:
    smd = "(module x (attr through_hole) (pad 1 smd rect))"
    assert fix_attributes(smd) == "(module x (attr smd) (pad 1 smd rect))"
    tht = "(module x (attr through_hole) (pad 1 thru_hole circle (drill 1)))"
    assert fix_attributes(tht) == tht
    text = '(model "/home/x/jlcpcb.3dshapes/R0603.wrl" (offset (xyz 0 0 0)))'
    assert rewrite_models(text, "${KIPRJMOD}/lib/j/") == (
        '(model "${KIPRJMOD}/lib/j/R0603.wrl" (offset (xyz 0 0 0)))'
    )


def test_vendor_all_parts_self_contained(offline: None, tmp_path: Path) -> None:
    project = new_project(tmp_path)
    for part in ALL:
        result = vendor_part(project, part)
        assert result.source == "easyeda2kicad 1.0.1"
    lib = project.directory / "lib/jlcpcb"
    symbols = read_symbol_library(lib / "jlcpcb.kicad_sym")
    assert len(symbols.symbols) == 6
    assert symbols.version == "20211014"  # the oldest syntax present
    names = [s.name for s in symbols.symbols]
    assert names == sorted(names)
    c0805 = read_footprint_file(lib / "jlcpcb.pretty/C0805.kicad_mod")
    assert c0805.smd and not c0805.through_hole  # the easyeda2kicad bug fixed
    assert c0805.models == ("${KIPRJMOD}/lib/jlcpcb/jlcpcb.3dshapes/C0805.wrl",)
    header = read_footprint_file(lib / "jlcpcb.pretty/HDR-TH_8P.kicad_mod")
    assert header.through_hole
    lock = load_lockfile(project.lock_path)
    assert lock is not None and sorted(p.id for p in lock.parts) == sorted(ALL)
    shared = [
        p for p in lock.parts if "lib/jlcpcb/jlcpcb.pretty/R0603.kicad_mod" in p.files
    ]
    assert sorted(p.id for p in shared) == ["C900001", "C900002"]
    # everything is locked, models are project-relative and exist
    assert run_check(project).issues == []


def test_shared_footprint_kept_then_refreshed(offline: None, tmp_path: Path) -> None:
    project = new_project(tmp_path)
    vendor_part(project, "C900001")
    r0603 = project.directory / "lib/jlcpcb/jlcpcb.pretty/R0603.kicad_mod"
    first = r0603.read_text()
    second = vendor_part(project, "C900002")  # its R0603 differs in descr
    assert second.kept == ["lib/jlcpcb/jlcpcb.pretty/R0603.kicad_mod"]
    assert [i.code for i in second.issues] == [Code.PART_KEPT]
    assert r0603.read_text() == first
    refreshed = vendor_part(project, "C900002", refresh=True)
    assert "lib/jlcpcb/jlcpcb.pretty/R0603.kicad_mod" in refreshed.written
    assert "4k7" in r0603.read_text()
    assert run_check(project).issues == []  # the lock follows the file
    again = vendor_part(project, "C900002")
    assert again.written == [] and again.kept == []  # idempotent


def test_registers_missing_library_tables(offline: None, demo_copy: Path) -> None:
    Config(fab="jlcpcb", project="demo-board").save(demo_copy)
    project = Project.open(demo_copy)
    result = vendor_part(project, "C900001")
    assert {i.code for i in result.issues} == {Code.LIB_REGISTERED}
    fp = (demo_copy / "fp-lib-table").read_text()
    assert '(name "jlcpcb")(type "KiCad")(uri "${KIPRJMOD}/jlcpcb-lib' in fp  # kept
    sym = (demo_copy / "sym-lib-table").read_text()
    assert "${KIPRJMOD}/lib/jlcpcb/jlcpcb.kicad_sym" in sym
    (demo_copy / "~demo-board.kicad_sch.lck").write_text("")
    (demo_copy / "sym-lib-table").unlink()
    with pytest.raises(KicadOpen):
        vendor_part(project, "C900002")


def test_errors(offline: None, tmp_path: Path, demo_copy: Path) -> None:
    project = new_project(tmp_path)
    with pytest.raises(FetchError, match="not an LCSC part number"):
        vendor_part(project, "R0603")
    with pytest.raises(FetchError, match="no captured"):
        vendor_part(project, "C1")
    with pytest.raises(ConfigError):
        vendor_part(Project.open(demo_copy), "C900001")


def test_subprocess_path(
    stub_e2k: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "calls.log"
    monkeypatch.setenv("STUB_E2K_LOG", str(log))
    project = new_project(tmp_path)
    result = vendor_part(project, "C900005")
    assert result.footprints == ["jlcpcb:MOD-SMD_KF1"]
    call = log.read_text()
    assert "--lcsc_id=C900005 --full --overwrite --output" in call
    with pytest.raises(FetchError, match="Failed to fetch"):
        Easyeda2kicad().fetch("C1", tmp_path / "w")


def test_missing_tool(tmp_path: Path) -> None:
    with pytest.raises(FetchError, match="cannot run easyeda2kicad"):
        Easyeda2kicad(command=("/nonexistent/easyeda2kicad",)).fetch("C1", tmp_path)


def test_sync(offline: None, tmp_path: Path) -> None:
    project = new_project(tmp_path)
    project.kicad.sch.write_text(
        schematic(
            symbol("R1", "C900001", project="p"),
            symbol("R2", "C900001", project="p"),
            symbol("U1", "C900005", project="p"),
            symbol("X1", "C99", project="p"),  # not in the fixtures: fails
            symbol("J1", "", project="p"),
        )
    )
    reporter = RecordingReporter()
    sleeps: list[float] = []
    result = sync_parts(project, delay=0.5, reporter=reporter, sleep=sleeps.append)
    assert [r.part_id for r in result.vendored] == ["C900001", "C900005"]
    assert result.failed == ["C99"]
    assert sleeps == [0.5, 0.5]
    codes = {i.code for i in result.issues}
    assert codes == {Code.NO_PART_NUMBER, Code.FETCH_FAILED}
    again = sync_parts(project, only=["c900001", "C900005"])
    assert again.skipped == ["C900001", "C900005"] and not again.vendored


def test_cli(
    offline: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = new_project(tmp_path)
    d = str(project.directory)
    project.kicad.sch.write_text(
        schematic(
            symbol("R1", "C900001", project="p"), symbol("U1", "C900005", project="p")
        )
    )
    assert main(["component", "add", "C900004", "--project", d, "--delay", "0"]) == 0
    out = capsys.readouterr().out
    assert "jlcpcb:C0805" in out and "reopening" in out
    assert main(["component", "add", "C1", "--project", d]) == 1
    assert main(["component", "list", d]) == 0
    out = capsys.readouterr().out
    assert "C900004" in out and "(unused)" in out and "not vendored: C900001" in out
    assert main(["component", "sync", d, "--delay", "0"]) == 0
    assert "2 vendored, 0 already vendored, 0 failed" in capsys.readouterr().out
    assert main(["component", "list", d, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["not_vendored"] == []
    assert {p["id"]: p["used_by"] for p in payload["parts"]}["C900001"] == ["R1"]
    # search, then pick result 1 interactively
    monkeypatch.chdir(project.directory)
    monkeypatch.setattr("sys.stdin", io.StringIO("1\n"))
    assert main(["component", "search", "330R", "0603", "--basic", "--add"]) == 0
    out = capsys.readouterr().out
    assert "C900001" in out and "jlcpcb:R0603" in out


def test_fixture_source_has_a_log(tmp_path: Path) -> None:
    fetched = FixturePartSource(FIXTURES).fetch("C900001", tmp_path)
    assert fetched.symbol_library is not None and fetched.footprints
