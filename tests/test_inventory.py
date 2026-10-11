from pathlib import Path

import pytest

from kifab.cli.main import main
from kifab.core.check import run_check
from kifab.core.init import init_project
from kifab.core.inventory import RemoveError, library_contents, remove_part
from kifab.core.library import vendor_part
from kifab.core.lockfile import load_lockfile
from kifab.core.project import Project
from kifab.kicad.symbol_lib import read_symbol_library
from tests.conftest import FIXTURES
from tests.helpers import board, footprint, schematic, symbol

R0603 = "lib/jlcpcb/jlcpcb.pretty/R0603.kicad_mod"
R0603_MODEL = "lib/jlcpcb/jlcpcb.3dshapes/R0603.wrl"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))
    project = init_project(tmp_path / "p", "p", "jlcpcb").project
    for part in ("C900001", "C900002", "C900004", "C900005"):
        vendor_part(project, part)
    project.kicad.sch.write_text(
        schematic(
            symbol("R1", "C900001"),
            symbol("R2", "C900001"),
            symbol("U1", "", lib_id="jlcpcb:KF-MOD-1"),  # symbol, no number
            symbol("C1", "C900003"),  # used, not vendored
            symbol("J1", ""),
        )
    )
    project.kicad.pcb.write_text(
        board(
            footprint("R1", "jlcpcb:R0603", lcsc="C900001"),
            footprint("R2", "jlcpcb:R0603", lcsc="C900001"),
            footprint("R9", "jlcpcb:R0603"),  # on the board only
        )
    )
    return project


def test_contents(project: Project) -> None:
    contents = library_contents(project)
    assert [p.id for p in contents.parts] == [
        "C900001",
        "C900002",
        "C900004",
        "C900005",
    ]
    r330 = contents.get("c900001")
    assert r330 is not None
    assert r330.schematic_refs == ("R1", "R2")
    assert r330.board_refs == ("R1", "R2", "R9")
    assert r330.usages == 3 and r330.footprints == ("R0603",)
    assert r330.models == (R0603_MODEL,)
    r4k7 = contents.get("C900002")  # shares R0603: R9 could be it, R1/R2 are not
    assert r4k7 is not None and r4k7.board_refs == ("R9",)
    module = contents.get("C900005")
    assert module is not None and module.schematic_refs == ("U1",)
    unused = contents.get("C900004")
    assert unused is not None and not unused.used and unused.usages == 0
    assert [(m.id, m.refs) for m in contents.not_vendored] == [("C900003", ("C1",))]
    assert contents.without_number == ("J1", "U1")


def test_remove_unused_keeps_shared_files(project: Project) -> None:
    lib = project.directory / "lib/jlcpcb"
    project.kicad.pcb.write_text(board())  # nothing on the board
    result = remove_part(project, "C900002")
    assert result.kept == [R0603_MODEL, R0603]  # C900001 still uses them
    assert result.deleted == [] and result.symbols == ["jlcpcb:KF-R4K7-0603"]
    assert (lib / "jlcpcb.pretty/R0603.kicad_mod").is_file()
    names = read_symbol_library(lib / "jlcpcb.kicad_sym").names()
    assert "KF-R4K7-0603" not in names and "KF-R330-0603" in names
    lock = load_lockfile(project.lock_path)
    assert lock is not None and "C900002" not in {p.id for p in lock.parts}
    result = remove_part(project, "C900004")
    assert result.deleted == [
        "lib/jlcpcb/jlcpcb.3dshapes/C0805.wrl",
        "lib/jlcpcb/jlcpcb.pretty/C0805.kicad_mod",
    ]
    assert not (lib / "jlcpcb.pretty/C0805.kicad_mod").exists()
    lock = load_lockfile(project.lock_path)
    assert lock is not None
    assert "lib/jlcpcb/jlcpcb.pretty/C0805.kicad_mod" not in {
        f.path for f in lock.files
    }
    # the lock still matches the library exactly
    codes = {i.code.value for i in run_check(project).issues}
    assert not {c for c in codes if c.startswith(("lock", "unlocked"))}


def test_remove_refuses_used_parts(project: Project) -> None:
    with pytest.raises(RemoveError, match="used by R1, R2, R9"):
        remove_part(project, "C900001")
    with pytest.raises(RemoveError, match="used by U1"):
        remove_part(project, "C900005")
    with pytest.raises(RemoveError, match="not vendored"):
        remove_part(project, "C900003")
    forced = remove_part(project, "C900005", force=True)
    assert forced.used_by == ["U1"]
    assert "lib/jlcpcb/jlcpcb.pretty/MOD-SMD_KF1.kicad_mod" in forced.deleted


def test_cli(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    d = str(project.directory)
    assert main(["component", "list", d]) == 0
    out = capsys.readouterr().out
    assert "C900001" in out and "3 used: R1,R2,R9" in out
    assert "C900004" in out and "(unused)" in out
    assert "not vendored: C900003" in out
    assert main(["component", "remove", "C900001", "--project", d]) == 1
    assert "used by R1, R2, R9" in capsys.readouterr().err
    assert main(["component", "remove", "C900004", "--project", d]) == 0
    out = capsys.readouterr().out
    assert "C900004    removed" in out and "deleted lib/jlcpcb" in out
    assert main(["component", "remove", "C900001", "--force", "--project", d]) == 0
    assert "still referenced by R1,R2,R9" in capsys.readouterr().out
