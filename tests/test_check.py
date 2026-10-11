import json
import subprocess
from pathlib import Path

import pytest

from kifab.cli.main import main
from kifab.core.check import run_check
from kifab.core.git import GitCli
from kifab.core.init import init_project
from kifab.core.issues import Code, Issue, Severity
from kifab.core.project import Project
from kifab.core.reporter import RecordingReporter
from kifab.kicad.symbol_lib import SymbolLibrary
from tests.conftest import DEMO
from tests.helpers import (
    board,
    footprint,
    lock_everything,
    schematic,
    symbol,
    vendor_r0603,
)


def codes(issues: list[Issue], severity: Severity | None = None) -> set[Code]:
    return {i.code for i in issues if severity is None or i.severity is severity}


def new_project(tmp_path: Path, git: bool = False) -> Project:
    result = init_project(tmp_path / "p", "p", "jlcpcb", git=GitCli() if git else None)
    return result.project


def write_design(project: Project, sch: str, pcb: str) -> None:
    project.kicad.sch.write_text(sch)
    project.kicad.pcb.write_text(pcb)


def test_fresh_project_is_clean(tmp_path: Path) -> None:
    project = new_project(tmp_path)
    reporter = RecordingReporter()
    result = run_check(project, reporter=reporter)
    assert result.issues == []
    assert "design rules" in result.checks
    assert [name for name, _ in reporter.steps] == result.checks


def test_demo_project_findings() -> None:
    result = run_check(Project.open(DEMO))
    found = codes(result.issues, Severity.ERROR)
    assert {
        Code.LIB_TABLE_MISSING,  # no sym-lib-table
        Code.SYMBOL_LIB_UNKNOWN,  # power:GND, Device:R, ...
        Code.FOOTPRINT_LIB_UNKNOWN,  # MountingHole:..., Connector_PinHeader...
        Code.MODEL_OUTSIDE_PROJECT,  # ${KICAD10_3DMODEL_DIR}
        Code.MODEL_MISSING,  # the demo's jlcpcb-lib has no 3D models
    } <= found
    by_code = {i.code: i for i in result.issues}
    assert Code.NO_CONFIG in by_code
    gnd = [
        i
        for i in result.issues
        if i.code is Code.SYMBOL_LIB_UNKNOWN and "power:GND" in i.message
    ]
    assert {i.ref for i in gnd} == {"#PWR01", "#PWR02"}  # root sheet and sub-sheet
    assert all(i.hint and "kifab" in i.hint for i in gnd)
    # jlcpcb and the project's own footprint library are fine
    assert not any(
        i.code is Code.FOOTPRINT_LIB_UNKNOWN and "jlcpcb:" in i.message
        for i in result.issues
    )


def test_library_references(tmp_path: Path) -> None:
    project = new_project(tmp_path)
    vendor_r0603(project)
    lock_everything(project)
    write_design(
        project,
        schematic(
            symbol("#PWR1", "", lib_id="kifab:GND", footprint="", project="p"),
            symbol(
                "R1",
                "C900001",
                lib_id="jlcpcb:KF-R330-0603",
                footprint="jlcpcb:R0603",
                project="p",
            ),
            symbol(
                "R2", "", lib_id="kifab:NOPE", footprint="jlcpcb:R9999", project="p"
            ),
        ),
        board(
            footprint(
                "R1",
                "jlcpcb:R0603",
                model="${KIPRJMOD}/lib/jlcpcb/jlcpcb.3dshapes/R0603.wrl",
            ),
            footprint("R2", "jlcpcb:R0603", model="/home/me/R0603.step"),
            footprint(
                "R3",
                "jlcpcb:R0603",
                model="${KIPRJMOD}/lib/jlcpcb/jlcpcb.3dshapes/gone.wrl",
            ),
        ),
    )
    issues = run_check(project).issues
    by_code = {i.code: i for i in issues}
    assert by_code[Code.SYMBOL_NOT_IN_LIB].ref == "R2"
    assert by_code[Code.FOOTPRINT_NOT_IN_LIB].ref == "R2"
    assert by_code[Code.MODEL_OUTSIDE_PROJECT].ref == "R2"
    assert by_code[Code.MODEL_MISSING].ref == "R3"
    assert by_code[Code.NOT_IN_SCHEMATIC].ref == "R3"
    assert by_code[Code.FOOTPRINT_MISMATCH].ref == "R2"
    assert Code.SYMBOL_LIB_UNKNOWN not in by_code


def test_parity_not_on_board(tmp_path: Path) -> None:
    project = new_project(tmp_path)
    write_design(
        project,
        schematic(
            symbol("#PWR1", "", lib_id="kifab:GND", footprint="", project="p"),
            symbol("U1", "", lib_id="Device:X", footprint="jlcpcb:X", project="p"),
        ),
        board(),
    )
    issues = run_check(project).issues
    missing = [i.ref for i in issues if i.code is Code.NOT_ON_BOARD]
    assert missing == ["U1"]  # the power symbol is not a part


def test_raw_easyeda2kicad_output_has_absolute_model_path(tmp_path: Path) -> None:
    project = new_project(tmp_path)
    vendor_r0603(project, project_relative=False)
    issues = run_check(project).issues
    outside = [i for i in issues if i.code is Code.MODEL_OUTSIDE_PROJECT]
    assert [i.path for i in outside] == ["lib/jlcpcb/jlcpcb.pretty/R0603.kicad_mod"]


def test_lock_verification(tmp_path: Path) -> None:
    project = new_project(tmp_path)
    vendor_r0603(project)
    found = codes(run_check(project).issues)
    assert {Code.UNLOCKED_FILE, Code.UNLOCKED_SYMBOL} <= found
    lock_everything(project)
    assert run_check(project).issues == []
    mod = project.directory / "lib/jlcpcb/jlcpcb.pretty/R0603.kicad_mod"
    mod.write_text(mod.read_text().replace("(attr smd)", "(attr through_hole)"))
    issues = run_check(project).issues
    assert [i.path for i in issues if i.code is Code.LOCK_HASH_MISMATCH] == [
        "lib/jlcpcb/jlcpcb.pretty/R0603.kicad_mod"
    ]
    mod.unlink()
    assert Code.LOCK_HASH_MISMATCH in codes(run_check(project).issues)
    (project.directory / "lib/jlcpcb/jlcpcb.kicad_sym").write_text(
        SymbolLibrary(()).dumps()
    )
    assert Code.LOCK_SYMBOL_MISSING in codes(run_check(project).issues)
    project.lock_path.unlink()
    assert Code.LOCK_FILE_MISSING in codes(run_check(project).issues)


def test_git_hygiene(tmp_path: Path) -> None:
    project = new_project(tmp_path, git=True)
    git = GitCli()
    d = project.directory
    issues = run_check(project, git=git).issues
    untracked = {i.path for i in issues if i.code is Code.UNTRACKED_SOURCE}
    assert {"p.kicad_sch", "kifab.toml", "lib/kifab/kifab.kicad_sym"} <= untracked
    assert all(i.severity is Severity.WARNING for i in issues)
    git.add(d, [Path(".")])
    assert run_check(project, git=git).issues == []
    (d / "fp-info-cache").write_text("x")
    (d / "build").mkdir()
    (d / "build" / "p_gerbers.zip").write_text("x")
    subprocess.run(["git", "-C", str(d), "add", "-f", "fp-info-cache"], check=True)
    issues = run_check(project, git=git).issues
    assert [i.path for i in issues if i.code is Code.TRACKED_GENERATED] == [
        "fp-info-cache"
    ]
    assert Code.UNTRACKED_SOURCE not in codes(issues)  # build/ is ignored
    (d / ".gitignore").write_text("")
    assert Code.GITIGNORE_OUTDATED in codes(run_check(project, git=git).issues)


def test_not_a_repo(tmp_path: Path) -> None:
    project = new_project(tmp_path)
    issues = run_check(project, git=GitCli()).issues
    assert codes(issues) == {Code.NOT_A_GIT_REPO}


def test_cli_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = new_project(tmp_path, git=True)
    d = str(project.directory)
    assert main(["check", d]) == 0  # only warnings (untracked files)
    out = capsys.readouterr().out
    assert "should be checked in" in out and "-> ok" in out
    assert main(["check", d, "--strict"]) == 1
    capsys.readouterr()
    assert main(["check", d, "--json", "--no-git"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["issues"] == []
    assert "git hygiene" not in payload["checks"]
    (project.directory / ".gitignore").write_text("mine\n")
    assert main(["check", d, "--fix-gitignore", "--no-git"]) == 0
    assert "updated .gitignore" in capsys.readouterr().out
    text = (project.directory / ".gitignore").read_text()
    assert text.startswith("mine\n") and "build/" in text
    assert main(["check", str(DEMO), "--no-git", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["issues"][0]["severity"] == "error"
    assert main(["check", str(tmp_path / "missing")]) == 2
