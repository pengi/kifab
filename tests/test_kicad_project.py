import json
from pathlib import Path

import pytest

from kifab.kicad.libtable import (
    LibTable,
    LibTableEntry,
    TableKind,
    parse_lib_table,
    project_uri,
    read_lib_table,
)
from kifab.kicad.project import (
    InputError,
    KicadProject,
    ProjectFile,
    find_projects,
    locked_files,
)
from kifab.kicad.symbol_lib import LibrarySymbol, parse_symbol_library
from kifab.kicad import sexpr
from tests.conftest import DEMO, FIXTURES


def make_project(where: Path, name: str = "b") -> Path:
    where.mkdir(parents=True, exist_ok=True)
    for ext in ("kicad_pro", "kicad_sch", "kicad_pcb"):
        (where / f"{name}.{ext}").write_text("{}" if ext == "kicad_pro" else "(x)")
    return where


def test_resolve_variants(tmp_path: Path) -> None:
    d = make_project(tmp_path).resolve()
    for given in (d / "b.kicad_pro", d / "b.kicad_sch", d / "b.kicad_pcb", d):
        project = KicadProject.resolve(given)
        assert project.directory == d
        assert project.name == "b"
        assert project.pcb == d / "b.kicad_pcb"


def test_resolve_errors(tmp_path: Path) -> None:
    d = make_project(tmp_path)
    with pytest.raises(InputError, match="does not exist"):
        KicadProject.resolve(d / "nope.kicad_pro")
    (d / "readme.txt").write_text("x")
    with pytest.raises(InputError, match="expected"):
        KicadProject.resolve(d / "readme.txt")
    make_project(d, "c")
    with pytest.raises(InputError, match="several projects"):
        KicadProject.resolve(d)
    (d / "c.kicad_sch").unlink()
    with pytest.raises(InputError, match="root schematic"):
        KicadProject.resolve(d / "c.kicad_pro")


def test_lock_files(tmp_path: Path) -> None:
    (tmp_path / "~b.kicad_pcb.lck").write_text("")
    assert locked_files(tmp_path) == ["b.kicad_pcb"]


def test_find_projects_skips_backups_and_hidden(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    make_project(tmp_path / "one", "one")
    make_project(tmp_path / "boards" / "two", "two")
    make_project(tmp_path / "one-backups" / "old", "old")
    make_project(tmp_path / ".history" / "hid", "hid")
    found = find_projects(tmp_path / "one")
    assert [p.name for p in found] == ["two.kicad_pro", "one.kicad_pro"]


def test_project_file_round_trip_and_rules() -> None:
    text = (DEMO / "demo-board.kicad_pro").read_text(encoding="utf-8")
    pro = ProjectFile.parse(text)
    assert pro.dumps() == text
    rules = pro.board_rules()
    assert rules["min_via_annular_width"] == 0.25
    assert rules["min_resolved_spokes"] == 1
    pro.set_board_rules({"min_track_width": 0.127, "min_resolved_spokes": 2})
    data = json.loads(pro.dumps())
    assert data["board"]["design_settings"]["rules"]["min_track_width"] == 0.127
    assert '"min_resolved_spokes": 2,' in pro.dumps()


def test_new_project_file() -> None:
    pro = ProjectFile.new("x")
    pro.set_board_rules({"min_clearance": 0.127})
    data = json.loads(pro.dumps())
    assert data["meta"]["filename"] == "x.kicad_pro"
    assert pro.board_rules() == {"min_clearance": 0.127}


def test_lib_table_round_trip_and_edit(tmp_path: Path) -> None:
    text = (DEMO / "fp-lib-table").read_text(encoding="utf-8")
    table = parse_lib_table(text, TableKind.FOOTPRINT)
    assert table.dumps() == text
    assert table.names() == {"demo-board", "jlcpcb"}
    entry = table.get("jlcpcb")
    assert entry is not None
    assert str(entry.project_relative) == "jlcpcb-lib/jlcpcb.pretty"
    assert entry.resolve(tmp_path) == tmp_path / "jlcpcb-lib" / "jlcpcb.pretty"
    new = table.with_entry(LibTableEntry("jlcpcb", "${KIPRJMOD}/lib/x.pretty"))
    assert len(new.entries) == 2
    assert new.get("jlcpcb") == LibTableEntry("jlcpcb", "${KIPRJMOD}/lib/x.pretty")
    assert read_lib_table(tmp_path, TableKind.SYMBOL) is None
    (tmp_path / "sym-lib-table").write_text(LibTable(TableKind.SYMBOL).dumps())
    sym = read_lib_table(tmp_path, TableKind.SYMBOL)
    assert sym is not None and sym.entries == ()


@pytest.mark.parametrize(
    "uri",
    [
        "/usr/share/kicad/footprints/R.pretty",
        "${KICAD10_FOOTPRINT_DIR}/Resistor_SMD.pretty",
        "${KIPRJMOD}/../shared/x.pretty",
    ],
)
def test_entries_outside_the_project(uri: str) -> None:
    assert LibTableEntry("x", uri).project_relative is None


def test_project_uri(tmp_path: Path) -> None:
    assert project_uri(tmp_path, tmp_path / "lib" / "a.pretty") == (
        "${KIPRJMOD}/lib/a.pretty"
    )


def test_symbol_library_sorted_and_digest() -> None:
    text = (FIXTURES / "easyeda2kicad_output/C900001/easyeda.kicad_sym").read_text()
    lib = parse_symbol_library(text)
    assert lib.names() == {"KF-R330-0603"}
    sym = lib.get("KF-R330-0603")
    assert sym is not None and len(sym.digest()) == 64
    other = LibrarySymbol("Z", [sexpr.Sym("symbol"), "Z"])
    merged = type(lib)(symbols=(*lib.symbols, other))
    reparsed = parse_symbol_library(merged.dumps())
    assert [s.name for s in reparsed.symbols] == ["KF-R330-0603", "Z"]
    assert reparsed.get("KF-R330-0603") is not None
    assert reparsed.symbols[0].digest() == sym.digest()
