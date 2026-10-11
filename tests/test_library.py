import dataclasses
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
from kifab.libraries.base import FetchError
from kifab.libraries.easyeda.fetch import Easyeda2kicad, FixturePartSource
from kifab.kicad import sexpr
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
    assert "--lcsc_id=C900005 --full --overwrite --debug --output" in call
    sleeps: list[float] = []
    with pytest.raises(FetchError, match="Failed to fetch"):
        Easyeda2kicad(sleep=sleeps.append).fetch("C1", tmp_path / "w")
    assert sleeps == [3.0, 8.0]  # retried twice: it could have been throttling


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


def test_description_from_the_catalogue(offline: None, tmp_path: Path) -> None:
    from kifab.core.inventory import library_contents
    from kifab.suppliers.base import get_supplier
    from kifab.kicad.symbol_lib import parse_symbol_library

    project = new_project(tmp_path)
    lib_path = project.directory / "lib/jlcpcb/jlcpcb.kicad_sym"
    vendor_part(project, "C900001")  # no catalogue: no description
    symbol = read_symbol_library(lib_path).get("KF-R330-0603")
    assert symbol is not None and symbol.description == ""
    # vendoring again with the catalogue fills it in, without a "kept" note
    catalog = get_supplier("jlcpcb").catalog()
    again = vendor_part(project, "C900001", catalog=catalog)
    assert again.issues == [] and again.written == ["lib/jlcpcb/jlcpcb.kicad_sym"]
    text = "330R 0603 Chip Resistor (synthetic test part)"
    symbol = read_symbol_library(lib_path).get("KF-R330-0603")
    assert symbol is not None and symbol.description == text
    assert '(property "ki_description" "' + text in lib_path.read_text()
    assert run_check(project).issues == []  # the lock follows
    # an explicit description (from the selected search hit) wins over a lookup
    hit = catalog.lookup("C900005")
    assert hit is not None
    vendor_part(
        project,
        "C900005",
        hit=dataclasses.replace(hit, description="Radio module, 2.4 GHz"),
    )
    contents = library_contents(project)
    assert [(p.id, p.description) for p in contents.parts] == [
        ("C900001", text),
        ("C900005", "Radio module, 2.4 GHz"),
    ]
    # stable: nothing changes on later adds, refresh keeps the description
    assert vendor_part(project, "C900001", catalog=catalog).written == []
    assert vendor_part(project, "C900001").written == []
    vendor_part(project, "C900001", refresh=True)
    symbol = read_symbol_library(lib_path).get("KF-R330-0603")
    assert symbol is not None and symbol.description == text
    assert run_check(project).issues == []
    # KiCad 8+ libraries get a Description field instead
    lib = parse_symbol_library(
        '(kicad_symbol_lib (version 20241209) (generator "t") (symbol "X"'
        ' (property "Reference" "U" (at 0 0 0)) (symbol "X_0_1")))'
    )
    new = lib.symbols[0].with_description("d", lib.version)
    dumped = sexpr.dumps(new.node)
    assert new.description == "d"
    assert '(property "Description" "d"' in dumped and "(hide yes)" in dumped
    assert "(id " not in dumped


def test_cli_add_and_list_show_the_description(
    offline: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = new_project(tmp_path)
    d = str(project.directory)
    assert main(["component", "add", "C900004", "--project", d, "--delay", "0"]) == 0
    capsys.readouterr()
    assert main(["component", "list", d]) == 0
    assert "4.7uF 0805 Capacitor (synthetic test part)" in capsys.readouterr().out
    assert main(["component", "list", d, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["parts"][0]["description"].startswith("4.7uF 0805")


@pytest.mark.parametrize(
    "fail, part, expected",
    [
        ("network", "C900001", "easyeda.com could not be reached"),
        ("tls", "C900001", "install certifi"),
        ("", "C1", "EasyEDA has no symbol/footprint for C1"),
    ],
)
def test_fetch_failures_are_explained(
    stub_e2k: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail: str,
    part: str,
    expected: str,
) -> None:
    monkeypatch.setenv("STUB_E2K_FAIL", fail)
    sleeps: list[float] = []
    with pytest.raises(FetchError) as info:
        Easyeda2kicad(sleep=sleeps.append).fetch(part, tmp_path / "w")
    # DNS and certificate failures are not retried
    assert sleeps == ([] if fail else [3.0, 8.0])
    message = str(info.value)
    assert message.startswith(f"{part}: easyeda2kicad failed: ") and expected in message
    if fail == "network":
        assert "Name or service not known" in message  # the real cause, not the summary
        assert "Failed to fetch data" not in message


def test_unknown_failure_points_at_the_log() -> None:
    from kifab.libraries.easyeda.fetch import explain_failure

    message = explain_failure("C5", "-- easyeda2kicad.py v1.0.1 --\nTraceback ...", 1)
    assert message.startswith("C5: easyeda2kicad failed: Traceback ...")
    assert "--lcsc_id=C5 --full --debug" in message


def test_https_uses_certifi() -> None:
    import certifi

    from kifab.core.net import ssl_context

    context = ssl_context()
    assert context.verify_mode.name == "CERT_REQUIRED"
    assert certifi.where()  # a dependency, so the bundle is there


def test_throttled_fetch_is_retried(
    stub_e2k: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = tmp_path / "calls"
    monkeypatch.setenv("STUB_E2K_THROTTLE", f"{calls}:2")
    sleeps: list[float] = []
    fetched = Easyeda2kicad(sleep=sleeps.append).fetch("C900001", tmp_path / "w")
    assert fetched.footprints and sleeps == [3.0, 8.0] and calls.read_text() == "3"
    calls.unlink()
    monkeypatch.setenv("STUB_E2K_THROTTLE", f"{calls}:5")
    with pytest.raises(FetchError, match="limiting requests"):
        Easyeda2kicad(sleep=sleeps.append).fetch("C900001", tmp_path / "w2")


def test_fetched_parts_are_cached(
    stub_e2k: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kifab_cache: Path,
) -> None:
    from kifab.libraries.base import get_library_source
    from kifab.libraries.easyeda.fetch import CachedPartSource

    log = tmp_path / "calls.log"
    monkeypatch.setenv("STUB_E2K_LOG", str(log))

    def calls() -> int:
        return len(log.read_text().splitlines()) if log.is_file() else 0

    source = get_library_source("easyeda").part_source()
    assert isinstance(source, CachedPartSource)
    assert source.root == kifab_cache / "easyeda2kicad"
    first = source.fetch("C900001", tmp_path / "a")
    again = source.fetch("c900001", tmp_path / "b")
    assert calls() == 1  # the second came from the cache
    assert [p.name for p in again.footprints] == [p.name for p in first.footprints]
    assert again.source == first.source == "easyeda2kicad 1.0.1"
    assert again.footprints[0].read_text() == first.footprints[0].read_text()
    # old entries are fetched again
    later = CachedPartSource(source.inner, source.root, now=lambda: 1e12)
    later.fetch("C900001", tmp_path / "c")
    assert calls() == 2
    # failures are not cached
    monkeypatch.setenv("STUB_E2K_FAIL", "network")
    with pytest.raises(FetchError):
        source.fetch("C900005", tmp_path / "d")
    monkeypatch.delenv("STUB_E2K_FAIL")
    assert not (source.root / "C900005").exists()
    # vendoring: add reuses the cache, --refresh fetches fresh
    project = new_project(tmp_path)
    vendor_part(project, "C900001")
    assert calls() == 3
    vendor_part(project, "C900001", refresh=True)
    assert calls() == 4
    vendor_part(project, "C900001", refresh=True, fresh=False)  # just fetched
    assert calls() == 4
    # entries in the layout before the rename (jlcpcb.*) are fetched again
    entry = source.root / "C900001"
    for item in list(entry.iterdir()):
        if item.name.startswith("easyeda."):
            item.rename(entry / item.name.replace("easyeda.", "jlcpcb.", 1))
    renamed = source.fetch("C900001", tmp_path / "old-layout")
    assert calls() == 5 and renamed.footprints


def test_redundant_pin_names_are_dropped(offline: None, tmp_path: Path) -> None:
    from kifab.kicad.symbol_lib import parse_symbol_library

    project = new_project(tmp_path)
    vendor_part(project, "C900001")  # resistor: pins "1", "2"
    vendor_part(project, "C900005")  # module: pins VCC, GND
    lib = read_symbol_library(project.directory / "lib/jlcpcb/jlcpcb.kicad_sym")
    resistor = lib.get("KF-R330-0603")
    module = lib.get("KF-MOD-1")
    assert resistor is not None and module is not None
    r = sexpr.dumps(resistor.node)
    assert sexpr.first(resistor.node, "pin_names") == ["pin_names", "hide"]
    assert sexpr.first(resistor.node, "pin_numbers") == ["pin_numbers", "hide"]
    assert '(name "~"' in r and '(name "1"' not in r
    m = sexpr.dumps(module.node)
    assert "pin_names" not in m and '(name "VCC"' in m and '(name "GND"' in m
    assert run_check(project).issues == []
    # mixed (a crystal: 1 and 3 numbered only, 2 and 4 GND): only the repeats go;
    # KiCad 9 syntax, and an existing (pin_names (offset ...)) is kept
    pin = '(pin passive line (at 0 0 0) (length 1) (name "{}") (number "{}"))'
    crystal = parse_symbol_library(
        '(kicad_symbol_lib (version 20241209) (generator "t") (symbol "X"'
        ' (pin_names (offset 0.254)) (symbol "X_1_1" '
        + " ".join(
            pin.format(n, k)
            for n, k in (("1", "1"), ("GND", "2"), ("3", "3"), ("GND", "4"))
        )
        + ")))"
    ).symbols[0]
    tidy = sexpr.dumps(crystal.with_tidy_pins("20241209").node)
    assert tidy.count('(name "~"') == 2 and tidy.count('(name "GND"') == 2
    assert "hide" not in tidy
    two = parse_symbol_library(
        '(kicad_symbol_lib (version 20241209) (generator "t") (symbol "C"'
        ' (pin_names (offset 0.254)) (symbol "C_1_1" '
        + " ".join(pin.format(n, n) for n in ("1", "2"))
        + ")))"
    ).symbols[0]
    node = two.with_tidy_pins("20241209").node
    assert sexpr.first(node, "pin_names") == [
        sexpr.Sym("pin_names"),
        [sexpr.Sym("offset"), sexpr.Sym("0.254")],
        [sexpr.Sym("hide"), sexpr.Sym("yes")],
    ]
    assert sexpr.first(node, "pin_numbers") == [
        sexpr.Sym("pin_numbers"),
        [sexpr.Sym("hide"), sexpr.Sym("yes")],
    ]
