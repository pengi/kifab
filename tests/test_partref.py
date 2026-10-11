"""The part model: MPN, manufacturer and supplier numbers of a symbol."""

from pathlib import Path

import pytest

from kifab.core.bundle import bom_columns
from kifab.core.init import init_project
from kifab.core.library import vendor_part
from kifab.core.manufacturing import parse_kicad_bom
from kifab.core.partref import (
    MANUFACTURER_KEY,
    MPN_KEY,
    PartRef,
    field_specs,
    part_ref,
    sku_key,
)
from kifab.core.sourcing import sourcing_for
from kifab.fabs.base import get_fab
from kifab.kicad.schematic import read_schematic
from kifab.kicad.symbol_lib import read_symbol_library
from kifab.suppliers.base import get_supplier
from tests.conftest import FIXTURES
from tests.helpers import schematic, symbol


def test_part_ref_from_fields() -> None:
    ref = part_ref(
        {
            "Value": "10k",
            "LCSC Part": "LCSC: c900001",
            "mpn": " KF-R10K ",
            "Manufacturer": "Example Components",
        }
    )
    assert ref == PartRef("KF-R10K", "Example Components", (("lcsc", "C900001"),))
    assert ref.sku("lcsc") == "C900001" and ref.sku("mouser") == ""
    assert ref.label == "C900001" and not ref.empty
    assert PartRef.from_values(ref.values()) == ref
    assert part_ref({"Value": "x"}).empty
    assert part_ref({"LCSC": "none"}).skus == ()  # not an LCSC number


def test_field_specs_cover_registered_suppliers() -> None:
    keys = [s.key for s in field_specs()]
    assert keys[:2] == [MPN_KEY, MANUFACTURER_KEY]
    assert sku_key("lcsc") in keys


def test_bom_columns_follow_the_schematic(tmp_path: Path) -> None:
    sch = tmp_path / "x.kicad_sch"
    sch.write_text(
        schematic(
            symbol("R1", "C900001", "10k", fields=(("MPN", "KF-R10K"),)),
            symbol("R2", "C900001", "10k", fields=(("MPN", "KF-R10K"),)),
        )
    )
    columns, specs = bom_columns(read_schematic(sch), sourcing_for(get_fab("jlcpcb")))
    assert columns.fields == ("Value", "Reference", "Footprint", "MPN", "LCSC")
    assert columns.labels[3:] == (MPN_KEY, sku_key("lcsc"))
    assert columns.group_by == ("Value", "Footprint", "MPN", "LCSC")
    bom = parse_kicad_bom(
        '"Value","Reference","Footprint","mpn","sku:lcsc"\n'
        '"10k","R1,R2","jlcpcb:R0603","KF-R10K","LCSC C900001"\n',
        specs,
    )
    assert bom.lines[0].part == PartRef("KF-R10K", "", (("lcsc", "C900001"),))


def test_supplier_number_column_is_always_there(tmp_path: Path) -> None:
    sch = tmp_path / "x.kicad_sch"
    sch.write_text(schematic(symbol("R1", "", "10k")))
    columns, _ = bom_columns(read_schematic(sch), sourcing_for(get_fab("jlcpcb")))
    assert columns.fields == ("Value", "Reference", "Footprint", "LCSC")


def test_vendoring_writes_mpn_and_manufacturer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))
    project = init_project(tmp_path / "p", "p", "jlcpcb").project
    catalog = get_supplier("jlcpcb").catalog()
    vendor_part(project, "C900001", catalog=catalog)
    lib = read_symbol_library(project.directory / "lib/jlcpcb/jlcpcb.kicad_sym")
    sym = lib.get("KF-R330-0603")
    assert sym is not None
    assert sym.field_value("MPN") == "KF-R330-0603"
    assert sym.field_value("Manufacturer") == "Example Components"
    # refetching (refresh) keeps the fields kifab added, without "kept" notes
    again = vendor_part(project, "C900001", refresh=True)
    assert again.issues == []
    sym = read_symbol_library(project.directory / "lib/jlcpcb/jlcpcb.kicad_sym").get(
        "KF-R330-0603"
    )
    assert sym is not None and sym.field_value("MPN") == "KF-R330-0603"
