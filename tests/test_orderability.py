import json
from datetime import date
from pathlib import Path

import pytest

from kifab.cli.main import main
from kifab.core import toml
from kifab.core.bundle import BundleOptions, bundle
from kifab.core.issues import Code, Severity
from kifab.core.partref import PartRef
from kifab.core.manufacturing import Bom, BomLine
from kifab.core.order import record_order
from kifab.core.orderability import check_availability, schematic_bom
from kifab.core.parts import CatalogError, LibraryType, PartHit, SearchPage, SearchSort
from kifab.core.project import Project
from kifab.suppliers.base import Catalog, SupplierCatalog
from kifab.suppliers.jlcpcb.search import FixtureClient, JlcpcbCatalog
from tests.conftest import DEMO, EXPECTED, FIXTURES
from tests.test_bundle import make_project

EXPECTED_BOM = EXPECTED / "demo-board_bom_jlcpcb.csv"


class FakeCatalog:
    def __init__(self, hits: dict[str, PartHit], fail: bool = False) -> None:
        self.hits = hits
        self.fail = fail
        self.calls = 0

    def search(
        self,
        query: str,
        *,
        page: int = 1,
        page_size: int = 10,
        basic_only: bool = False,
        sort: SearchSort = SearchSort.RELEVANCE,
    ) -> SearchPage:
        raise NotImplementedError

    def lookup(self, part_id: str) -> PartHit | None:
        self.calls += 1
        if self.fail:
            raise CatalogError("offline")
        return self.hits.get(part_id)

    def lookup_mpn(self, mpn: str, manufacturer: str = "") -> PartHit | None:
        self.calls += 1
        if self.fail:
            raise CatalogError("offline")
        return next((h for h in self.hits.values() if h.mpn == mpn), None)

    def image(self, url: str) -> bytes:
        raise CatalogError("no images")


def at(catalog: Catalog, name: str = "jlcpcb") -> list[SupplierCatalog]:
    return [SupplierCatalog(name, name.upper(), "lcsc", catalog)]


def lcsc(number: str) -> PartRef:
    return PartRef.with_sku("lcsc", number)


def hit(pn: str, stock: int, kind: LibraryType = LibraryType.BASIC) -> PartHit:
    return PartHit(pn, "mpn", "m", "0603", "", "", stock, kind)


def test_rules() -> None:
    bom = Bom(
        (
            BomLine("a", ("R1", "R2"), "f", lcsc("C1")),
            BomLine("b", ("R3",), "f", lcsc("C2")),
            BomLine("c", ("R4",), "f", lcsc("C3")),
            BomLine("d", ("R5",), "f", lcsc("C4")),
            BomLine("e", ("R6",), "f", lcsc("")),  # no part number: not looked up
        )
    )
    catalog = FakeCatalog(
        {
            "C1": hit("C1", 100),
            "C2": hit("C2", 4),
            "C3": hit("C3", 25, LibraryType.EXTENDED),
        }
    )
    report = check_availability(bom, at(catalog), boards=10)
    by_code = {i.code: i for i in report.issues}
    assert by_code[Code.OUT_OF_STOCK].ref == "R3"  # 4 < 10
    assert by_code[Code.LOW_STOCK].ref == "R4"  # 25 < 3 * 10
    assert by_code[Code.EXTENDED_PART].ref == "R4"
    assert by_code[Code.PART_NOT_FOUND].ref == "R5"
    assert Code.LOW_STOCK not in {i.code for i in report.issues if i.ref == "R1,R2"}
    assert report.extended_types == ["C3"]
    assert catalog.calls == 4
    assert report.snapshot()["C1"]["needed"] == 20


def test_catalog_failure_stops_lookups() -> None:
    bom = Bom(
        (BomLine("a", ("R1",), "f", lcsc("C1")), BomLine("b", ("R2",), "f", lcsc("C2")))
    )
    catalog = FakeCatalog({}, fail=True)
    report = check_availability(bom, at(catalog))
    assert [i.code for i in report.issues] == [Code.CATALOG_ERROR]
    assert catalog.calls == 1


def test_schematic_bom_matches_the_expected_bom() -> None:
    """Grouping from the schematic alone equals kicad-cli's BOM for the demo."""
    bom = schematic_bom(Project.open(DEMO))
    rows = EXPECTED_BOM.read_text().splitlines()[1:]
    expected = [r.split('","')[3].rstrip('"') for r in rows]
    assert [line.part.sku("lcsc") for line in bom.lines] == expected
    assert bom.part_count == 8
    with_tht = schematic_bom(Project.open(DEMO), assemble_through_hole=True)
    assert "J1" in {r for line in with_tht.lines for r in line.references}


def test_demo_against_the_fixture_catalogue() -> None:
    catalog = JlcpcbCatalog(FixtureClient(FIXTURES))
    report = check_availability(
        schematic_bom(Project.open(DEMO)), at(catalog), boards=5
    )
    assert not [i for i in report.issues if i.severity is Severity.ERROR]
    assert report.extended_types == ["C900004", "C900005"]  # C900008 is preferred
    assert 32 < report.parts_cost < 33


def test_bundle_with_stock_and_order_snapshot(
    kicad_cli_stub: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))
    project = make_project(tmp_path)
    result = bundle(project, BundleOptions(stock=True, boards=2))
    assert result.ok, result.issues
    assert result.availability is not None
    manifest = json.loads((project.directory / "build/p_manifest.json").read_text())
    assert manifest["stock"]["C900001"]["needed"] == 2  # R1 only (R2 has no number)
    assert manifest["boards"] == 2
    order = record_order(project, None, when=date(2026, 1, 1))
    assert order.issues == [] and order.directory is not None
    data = toml.loads((order.directory / "order.toml").read_text())
    assert data["stock"]["C900001"]["type"] == "basic"
    assert data["stock_snapshot"].startswith("at bundle time")


def test_cli(
    monkeypatch: pytest.MonkeyPatch,
    demo_copy: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))
    assert main(["component", "check", str(demo_copy), "--boards", "5"]) == 0
    out = capsys.readouterr().out
    assert "C900005" in out and "2 Extended type(s)" in out and "-> ok" in out
    assert main(["component", "check", str(demo_copy), "--boards", "2000"]) == 1
    out = capsys.readouterr().out
    assert "error: C900005 (KF-MOD-1): 1200 in stock, 2000 needed" in out
    assert main(["component", "check", str(demo_copy), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["parts"]["C900005"]["type"] == "extended"


def test_suppliers_in_order_of_preference() -> None:
    bom = Bom(
        (
            BomLine("a", ("R1",), "f", PartRef("MPN-A")),
            BomLine("b", ("R2",), "f", PartRef("MPN-B")),
        )
    )
    first = FakeCatalog(
        {"X1": PartHit("X1", "MPN-A", "m", "", "", "", 1, LibraryType.UNKNOWN)}
    )
    second = FakeCatalog(
        {
            "Y1": PartHit("Y1", "MPN-A", "m", "", "", "", 500, LibraryType.UNKNOWN),
            "Y2": PartHit("Y2", "MPN-B", "m", "", "", "", 5, LibraryType.UNKNOWN),
        }
    )
    sources = at(first, "one") + at(second, "two")
    report = check_availability(bom, sources, boards=10)
    chosen = {
        s.line.part.mpn: (s.supplier, s.hit.id if s.hit else "") for s in report.lines
    }
    # MPN-A: not enough at "one", enough at "two"; MPN-B: only "two", too few
    assert chosen == {"MPN-A": ("two", "Y1"), "MPN-B": ("two", "Y2")}
    assert [(i.code, i.ref) for i in report.issues] == [(Code.OUT_OF_STOCK, "R2")]
    assert "at TWO" in report.issues[0].message


def test_unreachable_supplier_is_skipped() -> None:
    bom = Bom(
        (
            BomLine("a", ("R1",), "f", PartRef("MPN-A")),
            BomLine("b", ("R2",), "f", PartRef("MPN-A")),
        )
    )
    down = FakeCatalog({}, fail=True)
    up = FakeCatalog(
        {"Y1": PartHit("Y1", "MPN-A", "m", "", "", "", 500, LibraryType.UNKNOWN)}
    )
    report = check_availability(bom, at(down, "down") + at(up, "up"))
    assert [i.code for i in report.issues] == [Code.CATALOG_ERROR]
    assert down.calls == 1 and [s.supplier for s in report.lines] == ["up", "up"]


def test_lookup_by_mpn_in_the_fixture_catalogue() -> None:
    catalog = JlcpcbCatalog(FixtureClient(FIXTURES))
    found = catalog.lookup_mpn("KF-R330-0603")  # the answer also has C900002
    assert found is not None and found.id == "C900001"
    assert catalog.lookup_mpn("KF-R330-0603", "example components") is not None
    assert catalog.lookup_mpn("KF-R330-0603", "Someone Else") is None
    assert catalog.lookup_mpn("xyzzy no such part") is None
