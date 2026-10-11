"""AISLER as fab, LCSC as supplier, EasyEDA libraries: init to bundle."""

import csv
import json
import io
import zipfile
from pathlib import Path

import pytest

from kifab.core.bundle import BundleOptions, bundle
from kifab.core.check import run_check
from kifab.core.init import init_project
from kifab.core.issues import Code
from kifab.core.library import sync_parts, vendor_part
from kifab.core.lockfile import load_lockfile
from kifab.core.manufacturing import Bom, BomLine
from kifab.core.partref import PartRef
from kifab.core.project import Project
from kifab.fabs.aisler import AislerFab
from kifab.fabs.rules import RuleOptions
from kifab.suppliers.base import get_supplier
from tests.conftest import FIXTURES
from tests.helpers import board, footprint, schematic, symbol

EPOCH = "1767225600"  # 2026-01-01


@pytest.fixture
def env(kicad_cli_stub: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))
    monkeypatch.setenv("SOURCE_DATE_EPOCH", EPOCH)


def aisler_project(tmp_path: Path) -> Project:
    project = init_project(tmp_path / "p", "p", "aisler").project
    sourcing = project.sourcing()
    assert sourcing is not None
    vendor_part(project, "C900001", catalog=sourcing.library_catalog())
    sym = "aisler:KF-R330-0603"
    parts = (("MPN", "KF-R330-0603"), ("Manufacturer", "Example Components"))
    project.kicad.sch.write_text(
        schematic(
            symbol("R1", "C900001", "330", "aisler:R0603", lib_id=sym, fields=parts),
            symbol("R2", "C900001", "330", "aisler:R0603", lib_id=sym, fields=parts),
            symbol("R3", "", "1k", "aisler:R0603", lib_id=sym),
        )
    )
    model = "${KIPRJMOD}/lib/aisler/aisler.3dshapes/R0603.wrl"
    project.kicad.pcb.write_text(
        board(
            footprint("R1", "aisler:R0603", model=model, at="100 50 90"),
            footprint("R2", "aisler:R0603", model=model, at="104 50 0"),
            footprint("R3", "aisler:R0603", model=model, at="102 50 180"),
        )
    )
    return project


def test_defaults() -> None:
    from kifab.core.sourcing import sourcing_for

    sourcing = sourcing_for(AislerFab())
    assert [s.name for s in sourcing.suppliers] == ["lcsc"]
    assert sourcing.library.name == "easyeda" and sourcing.library_name == "aisler"
    assert AislerFab().profiles() == ("hasl", "enig")


def test_rules_per_finish() -> None:
    fab = AislerFab()
    hasl = fab.design_rules("hasl", RuleOptions())
    enig = fab.design_rules("enig", RuleOptions())
    enig6 = fab.design_rules("enig", RuleOptions(layers=6))
    assert hasl.constraints.min_track_width == 0.2
    assert hasl.constraints.min_clearance == 0.15
    assert hasl.constraints.min_through_hole_diameter == 0.3
    assert enig.constraints.min_track_width == 0.125
    assert enig.constraints.min_through_hole_diameter == 0.25
    assert enig6.constraints.min_track_width == 0.1
    with pytest.raises(ValueError, match="unknown AISLER rules profile"):
        fab.design_rules("osp", RuleOptions())


def test_lcsc_supplier_drops_jlcpcb_classes(env: None) -> None:
    catalog = get_supplier("lcsc").catalog()
    hit = catalog.lookup("C900001")
    assert hit is not None and hit.library_type.value == "unknown"
    assert hit.url == "https://www.lcsc.com/product-detail/C900001.html"
    page = catalog.search("KF-MOD-1", basic_only=True)  # no Basic at LCSC
    assert [h.id for h in page.hits] == ["C900005"]


def test_check_parts() -> None:
    bom = Bom(
        (
            BomLine("a", ("R1",), "f", PartRef("M1", "Mk")),
            BomLine("b", ("R2",), "f", PartRef.with_sku("lcsc", "C1")),
            BomLine("c", ("R3",), "f"),
        )
    )
    issues = AislerFab().check_parts(bom)
    assert [(i.severity.value, i.ref) for i in issues] == [
        ("info", "R2"),
        ("warning", "R3"),
    ]
    assert all(i.code is Code.NO_PART_NUMBER for i in issues)


def test_bundle(env: None, tmp_path: Path) -> None:
    project = aisler_project(tmp_path)
    assert run_check(project, git=None).issues == []
    lib = (project.directory / "lib/aisler/aisler.kicad_sym").read_text()
    assert '"MPN"' in lib and '"aisler:R0603"' in lib
    result = bundle(project, BundleOptions(stock=True, boards=2))
    assert result.ok, result.issues
    build = project.directory / "build"
    zipfile_bytes = (build / "p_aisler.zip").read_bytes()
    rows = list(csv.reader(io.StringIO((build / "p_bom_aisler.csv").read_text())))
    assert rows == [
        ["Designator", "Quantity", "Value", "Footprint", "Manufacturer", "MPN", "LCSC"],
        [
            "R1,R2",
            "2",
            "330",
            "aisler:R0603",
            "Example Components",
            "KF-R330-0603",
            "C900001",
        ],
        ["R3", "1", "1k", "aisler:R0603", "", "", ""],
    ]
    cpl = (build / "p_cpl_aisler.csv").read_text().splitlines()
    assert cpl[0] == "Ref,Val,Package,PosX,PosY,Rot,Side"
    assert cpl[1].startswith("R1,") and cpl[1].endswith(",90.0000,top")
    assert not (build / "p_gerbers.zip").exists()
    # the layout AISLER's KiCad plugin uploads (and AISLER accepts)
    with zipfile.ZipFile(build / "p_aisler.zip") as zf:
        assert zf.namelist() == [
            "components.json",
            "netlist.ipc",
            "odb.zip",
            "p-B_Cu.gbr",
            "p-B_Mask.gbr",
            "p-B_Paste.gbr",
            "p-B_SilkS.gbr",
            "p-Edge_Cuts.gbr",
            "p-F_Cu.gbr",
            "p-F_Mask.gbr",
            "p-F_Paste.gbr",
            "p-F_SilkS.gbr",
            "p-NPTH.drl",
            "p-PTH.drl",
        ]
        components = json.loads(zf.read("components.json"))
        with zipfile.ZipFile(io.BytesIO(zf.read("odb.zip"))) as odb:
            assert "user/" in odb.namelist()  # empty directories are kept
            info = odb.read("misc/info").decode()
            assert "CREATION_DATE=20260101.000000" in info  # the build's stamp
            assert odb.read("steps/pcb/eda/data").startswith(b"# 2026-01-01T00:00:00")
    assert components == [
        {
            "pos_x": 100.0,
            "pos_y": -50.0,
            "rotation": 90.0,
            "side": "top",
            "designator": "R1",
            "mpn": "KF-R330-0603",
            "pack": "R0603",
            "value": "v",
            "mount_type": "smt",
            "place": True,
        },
        {
            "pos_x": 104.0,
            "pos_y": -50.0,
            "rotation": 0.0,
            "side": "top",
            "designator": "R2",
            "mpn": "KF-R330-0603",
            "pack": "R0603",
            "value": "v",
            "mount_type": "smt",
            "place": True,
        },
        {
            "pos_x": 102.0,
            "pos_y": -50.0,
            "rotation": 180.0,
            "side": "top",
            "designator": "R3",
            "mpn": None,
            "pack": "R0603",
            "value": "v",
            "mount_type": "smt",
            "place": True,
        },
    ]
    again = bundle(project, BundleOptions())
    assert again.ok
    assert (build / "p_aisler.zip").read_bytes() == zipfile_bytes  # reproducible
    assert result.availability is not None
    assert [s.supplier for s in result.availability.lines] == ["lcsc"]
    codes = {i.code for i in result.issues}
    assert Code.EXTENDED_PART not in codes  # a JLCPCB notion
    assert Code.NO_PART_NUMBER in codes  # R3: no MPN, no number


def test_add_by_mpn(
    env: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from kifab.cli.main import main

    project = init_project(tmp_path / "m", "m", "aisler").project
    assert (
        main(["component", "add", "KF-R330-0603", "--project", str(project.directory)])
        == 0
    )
    assert "C900001" in capsys.readouterr().out
    lock = load_lockfile(project.lock_path)
    assert lock is not None and [p.id for p in lock.parts] == ["C900001"]
    code = main(
        ["component", "add", "NO-SUCH-MPN", "--project", str(project.directory)]
    )
    assert code == 1 and "no part with MPN 'NO-SUCH-MPN'" in capsys.readouterr().err


def test_sync_resolves_mpn_only_symbols(env: None, tmp_path: Path) -> None:
    project = init_project(tmp_path / "s", "s", "aisler").project
    mpn = (("MPN", "KF-R330-0603"),)
    project.kicad.sch.write_text(
        schematic(
            symbol("R1", "", "330", fields=mpn, project="s"),
            symbol("R2", "", "330", fields=(("MPN", "NOPE-1"),), project="s"),
        )
    )
    sourcing = project.sourcing()
    assert sourcing is not None
    result = sync_parts(project, catalog=sourcing.library_catalog())
    assert [r.part_id for r in result.vendored] == ["C900001"]
    assert [(i.code, i.ref) for i in result.issues] == [(Code.FETCH_FAILED, "R2")]
    without = sync_parts(project)  # no catalogue: MPN-only parts are not vendored
    assert {i.ref for i in without.issues if i.code is Code.NO_PART_NUMBER} == {"R1,R2"}
