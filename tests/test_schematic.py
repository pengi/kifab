from pathlib import Path

from kifab.kicad.libid import LibId
from kifab.kicad.schematic import find_lcsc, read_schematic, sorted_refs
from tests.conftest import DEMO
from tests.helpers import schematic, sheet_node, symbol


def test_collects_sub_sheets_and_ignores_power(tmp_path: Path) -> None:
    root = tmp_path / "root.kicad_sch"
    root.write_text(
        schematic(
            symbol("R1", "C900001", "330"),
            symbol("R3", "c25804", "4.7k", dnp="yes", field="LCSC Part #"),
            symbol("#PWR01", "", lib_id="kifab:GND"),
            symbol("U1", ""),
            sheet_node("sub.kicad_sch"),
            sheet_node("gone.kicad_sch"),
        )
    )
    (tmp_path / "sub.kicad_sch").write_text(
        schematic(
            symbol("C1", "C900003", "18pF", extra_refs=("C11",)),  # two sheet instances
            sheet_node("root.kicad_sch"),  # a cycle must not loop
        )
    )
    sch = read_schematic(root)
    assert [p.reference for p in sch.parts()] == ["C1", "C11", "R1", "R3", "U1"]
    by_ref = {p.reference: p for p in sch.parts()}
    assert by_ref["R3"].lcsc == "C25804"
    assert by_ref["R3"].dnp
    assert by_ref["U1"].lcsc is None
    assert by_ref["C11"].sheet.name == "sub.kicad_sch"
    power = [s for s in sch.symbols if s.reference == "#PWR01"]
    assert power and power[0].is_power and power[0].is_virtual
    assert len(sch.sheets) == 2
    assert sch.missing_sheets == ("gone.kicad_sch",)
    assert all(p.lcsc != "C999999" for p in sch.parts())


def test_instances_filtered_by_project(tmp_path: Path) -> None:
    root = tmp_path / "root.kicad_sch"
    root.write_text(
        schematic(symbol("R1", "C1", project="p"), symbol("R7", "C1", project="other"))
    )
    assert [p.reference for p in read_schematic(root, "p").parts()] == ["R1"]
    assert len(read_schematic(root).parts()) == 2


def test_find_lcsc_custom_fields() -> None:
    assert find_lcsc({"Supplier": "C42"}) is None
    assert find_lcsc({"Supplier": "C42"}, ["supplier"]) == "C42"
    assert find_lcsc({"jlcpcb part #": "LCSC: c1234"}) == "C1234"


def test_natural_sort() -> None:
    assert sorted_refs(["R10", "R2", "C1", "R2"]) == ["C1", "R2", "R10"]


def test_demo_project() -> None:
    sch = read_schematic(DEMO / "demo-board.kicad_sch", "demo-board")
    parts = {p.reference: p for p in sch.parts()}
    assert len(parts) == 11
    assert {p.lcsc for p in parts.values() if p.lcsc} == {
        "C900001",
        "C900002",
        "C900003",
        "C900004",
        "C900005",
        "C900006",
        "C900008",
    }
    assert [r for r, p in parts.items() if p.dnp] == ["R3"]
    assert not parts["TP1"].in_bom
    assert parts["C3"].sheet.name == "power.kicad_sch"  # from the sub-sheet
    assert parts["R1"].footprint_id == LibId("jlcpcb", "R0603")
    assert LibId("power", "GND") in {s.lib_id for s in sch.lib_symbols if s.is_power}
    assert len(sch.sheets) == 2
