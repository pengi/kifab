import io
import json
import os
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from kifab.cli.main import main
from kifab.core.bundle import BundleOptions, BundleResult, bundle
from kifab.core.git import GitCli
from kifab.core.init import init_project
from kifab.core.issues import Code, Severity
from kifab.core.partref import PartRef
from kifab.core.manufacturing import (
    Bom,
    BomLine,
    ParseError,
    PlacedPart,
    Placement,
    parse_kicad_bom,
    parse_kicad_pos,
)
from kifab.core.project import Project
from kifab.core.reporter import RecordingReporter
from kifab.core.reproducible import deterministic_zip, normalize_timestamps
from kifab.fabs.jlcpcb.export import bom_csv, cpl_csv
from kifab.kicad.board import Side
from tests.conftest import EXPECTED
from tests.helpers import (
    board,
    footprint,
    lock_everything,
    schematic,
    symbol,
    vendor_r0603,
)

NAME = "demo-board"
EPOCH = "1767225600"  # 2026-01-01T00:00:00Z


@pytest.fixture
def stub(kicad_cli_stub: str, monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", EPOCH)
    return monkeypatch


def lcsc(number: str) -> PartRef:
    return PartRef.with_sku("lcsc", number)


def run(project_dir: Path, **options: Any) -> BundleResult:
    return bundle(Project.open(project_dir), BundleOptions(**options))


def codes(result: BundleResult, severity: Severity | None = None) -> set[Code]:
    return {i.code for i in result.issues if severity is None or i.severity is severity}


# ---------------------------------------------------------------- golden example


def test_golden_demo(stub: pytest.MonkeyPatch, demo_copy: Path) -> None:
    """The demo board: BOM and CPL equal the hand-written expected files."""
    reporter = RecordingReporter()
    result = bundle(
        Project.open(demo_copy), BundleOptions(skip_check=True), reporter=reporter
    )
    assert result.ok, result.issues
    build = demo_copy / "build"
    for name in (f"{NAME}_bom_jlcpcb.csv", f"{NAME}_cpl_jlcpcb.csv"):
        assert (build / name).read_bytes() == (EXPECTED / name).read_bytes(), name
    assert result.left_out == ["J1", "TP1"]
    assert result.dnp == ["R3"]
    assert [s for s, _ in reporter.steps] == [
        "check",
        "DRC",
        "gerbers",
        "drill",
        "BOM",
        "placement",
        "zip",
        "report",
    ]
    # the zip holds gerbers, drill and job file, with normalised time stamps
    with zipfile.ZipFile(build / f"{NAME}_gerbers.zip") as z:
        names = z.namelist()
        assert names == sorted(names)
        assert f"{NAME}-F_Cu.gtl" in names and f"{NAME}-PTH.drl" in names
        assert f"{NAME}-job.gbrjob" in names
        assert not [n for n in names if n.endswith((".pdf", ".csv"))]
        for name in names:
            assert "2026-01-01T00:00:00+00:00" in z.read(name).decode(), name
    assert not list(build.glob("*.tmp"))


def test_reproducible(stub: pytest.MonkeyPatch, demo_copy: Path) -> None:
    run(demo_copy, skip_check=True)
    first = {p.name: p.read_bytes() for p in (demo_copy / "build").iterdir()}
    run(demo_copy, skip_check=True)
    second = {p.name: p.read_bytes() for p in (demo_copy / "build").iterdir()}
    assert first == second


def test_check_errors_stop_the_bundle(
    stub: pytest.MonkeyPatch, demo_copy: Path
) -> None:
    result = run(demo_copy)
    assert not result.ok
    assert Code.CHECK_FAILED in codes(result)
    assert not (demo_copy / "build").exists()


def test_drc_failure_keeps_old_outputs(
    stub: pytest.MonkeyPatch, demo_copy: Path
) -> None:
    build = demo_copy / "build"
    build.mkdir()
    (build / f"{NAME}-F_Cu.gtl").write_text("keep me")
    stub.setenv("STUB_DRC_FAIL", "1")
    stub.setenv("STUB_DRC_EXCLUDED", "1")  # excluded ones never count
    result = run(demo_copy, skip_check=True)
    drc = [i for i in result.issues if i.code is Code.DRC_VIOLATION]
    assert len(drc) == 1 and "silk_overlap" in drc[0].message
    assert "(1.5, 2) mm" in drc[0].message
    assert (build / f"{NAME}-F_Cu.gtl").read_text() == "keep me"
    assert run(demo_copy, skip_check=True, skip_drc=True).ok
    assert (build / f"{NAME}-F_Cu.gtl").read_text() != "keep me"


def test_failed_step_keeps_old_outputs(
    stub: pytest.MonkeyPatch, demo_copy: Path
) -> None:
    assert run(demo_copy, skip_check=True).ok
    build = demo_copy / "build"
    before = {p.name: p.read_bytes() for p in build.iterdir()}
    stub.setenv("STUB_FAIL", "export drill")
    result = run(demo_copy, skip_check=True)
    assert Code.TOOL_FAILED in codes(result)
    assert "simulated failure" in next(
        i.message for i in result.issues if i.code is Code.TOOL_FAILED
    )
    assert {p.name: p.read_bytes() for p in build.iterdir()} == before


def test_only_this_boards_results_are_replaced(
    stub: pytest.MonkeyPatch, demo_copy: Path
) -> None:
    build = demo_copy / "build"
    build.mkdir()
    (build / f"{NAME}-stale.gtl").write_text("old")
    (build / "other-F_Cu.gtl").write_text("other board")
    (build / "notes.txt").write_text("mine")
    assert run(demo_copy, skip_check=True).ok
    assert not (build / f"{NAME}-stale.gtl").exists()
    assert (build / "other-F_Cu.gtl").read_text() == "other board"
    assert (build / "notes.txt").read_text() == "mine"


def test_kicad_open(stub: pytest.MonkeyPatch, demo_copy: Path) -> None:
    (demo_copy / f"~{NAME}.kicad_pcb.lck").write_text("")
    result = run(demo_copy, skip_check=True)
    assert codes(result, Severity.ERROR) == {Code.KICAD_OPEN}
    assert run(demo_copy, skip_check=True, force=True).ok


def test_kicad_cli_missing(monkeypatch: pytest.MonkeyPatch, demo_copy: Path) -> None:
    monkeypatch.delenv("KIFAB_KICAD_CLI", raising=False)
    monkeypatch.setenv("PATH", "")
    result = run(demo_copy, skip_check=True)
    assert Code.TOOL_MISSING in codes(result)


# ---------------------------------------------------------------- kifab project


def make_project(tmp_path: Path, layers: int = 2) -> Project:
    project = init_project(tmp_path / "p", "p", "jlcpcb", layers=layers).project
    vendor_r0603(project)
    lock_everything(project)
    sym = "jlcpcb:KF-R330-0603"
    project.kicad.sch.write_text(
        schematic(
            symbol("R1", "C900001", "330", footprint="jlcpcb:R0603", lib_id=sym),
            symbol("R2", "", "1k", footprint="jlcpcb:R0603", lib_id=sym),
            symbol(
                "R3", "C900001", "330", footprint="jlcpcb:R0603", lib_id=sym, dnp="yes"
            ),
            symbol("J1", "C900006", "hdr", footprint="jlcpcb:R0603", lib_id=sym),
        ).replace('(project "p"', '(project "p"')
    )
    model = "${KIPRJMOD}/lib/jlcpcb/jlcpcb.3dshapes/R0603.wrl"
    layer_decl = (
        '(layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal) '
        '(2 "B.Cu" signal) (25 "Edge.Cuts" user))'
        if layers == 4
        else None
    )
    pcb = board(
        footprint("R1", "jlcpcb:R0603", model=model, at="100 50 90"),
        footprint("R2", "jlcpcb:R0603", model=model, at="102 50 -90", layer="B.Cu"),
        footprint("R3", "jlcpcb:R0603", model=model, attr="smd dnp"),
        footprint("J1", "jlcpcb:R0603", model=model, attr="through_hole", at="90 40 0"),
    )
    if layer_decl:
        pcb = pcb.replace(
            '(layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))',
            layer_decl,
        )
    project.kicad.pcb.write_text(pcb)
    return project


def test_kifab_project(stub: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = make_project(tmp_path)
    result = bundle(project)
    assert result.ok, result.issues
    assert Code.NO_PART_NUMBER in codes(result, Severity.WARNING)
    assert result.left_out == ["J1"] and result.dnp == ["R3"]
    build = project.directory / "build"
    bom = (build / "p_bom_jlcpcb.csv").read_text()
    assert bom.splitlines() == [
        '"Comment","Designator","Footprint","LCSC Part #"',
        '"330","R1","jlcpcb:R0603","C900001"',
        '"1k","R2","jlcpcb:R0603",""',
    ]
    cpl = (build / "p_cpl_jlcpcb.csv").read_text().splitlines()
    assert cpl == [
        "Designator,Mid X,Mid Y,Layer,Rotation",
        "R1,100.0000mm,-50.0000mm,Top,90",
        "R2,102.0000mm,-50.0000mm,Bottom,270",
    ]
    manifest = json.loads((build / "p_manifest.json").read_text())
    assert manifest["ok"] and manifest["fab"] == "jlcpcb"
    assert set(manifest["inputs"]) >= {"p.kicad_sch", "p.kicad_pcb", "kifab.lock"}
    assert "p_gerbers.zip" in manifest["files"]
    report = (build / "p_report.md").read_text()
    assert "no-part-number" in report and "J1" in report


def test_assemble_through_hole(stub: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = make_project(tmp_path)
    result = bundle(project, BundleOptions(assemble_through_hole=True))
    assert result.ok and result.left_out == []
    assert result.placement is not None
    assert "J1" in result.placement.references()


def test_inner_layers_are_plotted(stub: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = make_project(tmp_path, layers=4)
    result = bundle(project)
    assert result.ok, result.issues
    with zipfile.ZipFile(project.directory / "build" / "p_gerbers.zip") as z:
        names = z.namelist()
    assert "p-In1_Cu.g1" in names and "p-In2_Cu.g2" in names


def test_version_from_git_and_artifacts(
    stub: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stub.delenv("SOURCE_DATE_EPOCH")
    project = make_project(tmp_path)
    d = project.directory
    git = GitCli()
    git.init(d)
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-02-03T04:05:06Z",
        "GIT_COMMITTER_DATE": "2026-02-03T04:05:06Z",
    }
    for cmd in (
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "x"],
        ["tag", "v1.0"],
    ):
        subprocess.run(["git", "-C", str(d), *cmd], check=True, env=env)
    release = tmp_path / "release"
    result = bundle(project, BundleOptions(artifacts_dir=release), git=git)
    assert result.ok, result.issues
    assert result.version == "v1.0"
    assert sorted(p.name for p in release.iterdir()) == [
        "p-v1.0-bom_jlcpcb.csv",
        "p-v1.0-cpl_jlcpcb.csv",
        "p-v1.0-gerbers.zip",
        "p-v1.0-report.md",
    ]
    gtl = (d / "build" / "p-F_Cu.gtl").read_text()
    assert "%TF.CreationDate,2026-02-03T04:05:06+00:00*%" in gtl
    with zipfile.ZipFile(release / "p-v1.0-gerbers.zip") as z:
        assert {i.date_time for i in z.infolist()} == {(2026, 2, 3, 4, 5, 6)}


# ---------------------------------------------------------------- pieces


def test_normalize_timestamps() -> None:
    stamp = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    text = (
        "%TF.CreationDate,2026-10-10T19:55:02+02:00*%\n"
        "G04 Created by KiCad (PCBNEW 10.0.6) date 2026-10-10 19:55:02*\n"
        "X2026Y-1D01*\n"
        "; DRILL file KiCad 10.0.6 date 2026-10-10T19:55:02\n"
        "; #@! TF.CreationDate,2026-10-10T19:55:02+02:00\n"
        '    "CreationDate": "2026-10-10T19:55:02+02:00"\n'
    )
    assert normalize_timestamps(text, stamp) == (
        "%TF.CreationDate,2026-01-02T03:04:05+00:00*%\n"
        "G04 Created by KiCad (PCBNEW 10.0.6) date 2026-01-02 03:04:05*\n"
        "X2026Y-1D01*\n"
        "; DRILL file KiCad 10.0.6 date 2026-01-02T03:04:05\n"
        "; #@! TF.CreationDate,2026-01-02T03:04:05+00:00\n"
        '    "CreationDate": "2026-01-02T03:04:05+00:00"\n'
    )


def test_deterministic_zip() -> None:
    a = deterministic_zip([("b.txt", b"2"), ("a.txt", b"1")], None)
    b = deterministic_zip([("a.txt", b"1"), ("b.txt", b"2")], None)
    assert a == b
    with zipfile.ZipFile(io.BytesIO(a)) as z:
        assert z.namelist() == ["a.txt", "b.txt"]
        assert z.infolist()[0].date_time == (1980, 1, 1, 0, 0, 0)


def test_jlcpcb_formats() -> None:
    bom = Bom(
        (
            BomLine("4.7uF", ("C1", "C5"), "jlcpcb:C0805", lcsc("C900004")),
            BomLine("a,b", ("R1",), "f", lcsc("")),
        )
    )
    assert bom_csv(bom) == (
        b'"Comment","Designator","Footprint","LCSC Part #"\r\n'
        b'"4.7uF","C1,C5","jlcpcb:C0805","C900004"\r\n'
        b'"a,b","R1","f",""\r\n'
    )
    placement = Placement(
        (
            PlacedPart("R10", "v", "p", 0, 0, 450, Side.BOTTOM),
            PlacedPart("C1", "v", "p", 1.5, -2, -90.0, Side.TOP),
        )
    )
    assert cpl_csv(placement).decode().splitlines() == [
        "Designator,Mid X,Mid Y,Layer,Rotation",
        "C1,1.5000mm,-2.0000mm,Top,270",
        "R10,0.0000mm,0.0000mm,Bottom,90",
    ]
    assert bom.without({"C1", "R1"}).lines == (
        BomLine("4.7uF", ("C5",), "jlcpcb:C0805", lcsc("C900004")),
    )


def test_kicad_csv_parsers() -> None:
    bom = parse_kicad_bom(
        '"Value","Reference","Footprint","Part"\n"330","R10,R2","f","C1"\n'
    )
    assert bom.lines[0].references == ("R2", "R10")
    pos = parse_kicad_pos(
        "Ref,Val,Package,PosX,PosY,Rot,Side\n"
        '"C1","4.7uF","C0805",115.0,-58.0,-90.0,top\n'
        '"R9","10k","R0603",101.5,-51.25,450,bottom\n'
    )
    assert [p.side for p in pos.parts] == [Side.TOP, Side.BOTTOM]
    with pytest.raises(ParseError):
        parse_kicad_bom('"Value"\n"x"\n')
    with pytest.raises(ParseError):
        parse_kicad_pos("Ref,Val\nx,y\n")


# ---------------------------------------------------------------- CLI


def test_cli_bundle(
    stub: pytest.MonkeyPatch, demo_copy: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["bundle", str(demo_copy), "--skip-check"]) == 0
    out = capsys.readouterr().out
    for text in ("DRC", "gerbers", "zip", "J1,TP1", "placement preview"):
        assert text in out
    assert main(["bundle", str(demo_copy), "--skip-check", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] and payload["bom_lines"] == 6 and payload["placed"] == 8
    assert main(["bundle", str(demo_copy)]) == 1
    assert "FAILED" in capsys.readouterr().out
    assert main(["bundle", str(demo_copy / "nope")]) == 2
