import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from kifab.cli.main import main
from kifab.core import gitignore
from kifab.core.basic_symbols import basic_library
from kifab.core.config import Config
from kifab.core.design_rules import DRU_MARKERS, apply_rules, check_rules, render_dru
from kifab.core.git import GitCli
from kifab.core.init import InitError, init_project
from kifab.core.issues import Code, Severity
from kifab.core.project import KicadOpen, Project
from kifab.fabs.base import available_fabs, get_fab
from kifab.fabs.rules import RuleOptions
from kifab.kicad import sexpr
from kifab.kicad.board import read_board
from kifab.kicad.libtable import TableKind, read_lib_table
from kifab.kicad.project import ProjectFile
from kifab.kicad.schematic import read_schematic
from kifab.kicad.symbol_lib import read_symbol_library


def test_registry() -> None:
    assert "jlcpcb" in available_fabs()
    assert get_fab("jlcpcb").library_name == "jlcpcb"


def test_profile_depends_on_layers_and_options() -> None:
    fab = get_fab("jlcpcb")
    two = fab.design_rules("standard", RuleOptions(layers=2))
    four = fab.design_rules(
        "standard", RuleOptions(layers=4, relax_fab_footprint_silk=False)
    )
    names2 = {r.name for r in two.rules}
    names4 = {r.name for r in four.rules}
    assert "Track width, inner layer" not in names2
    assert "Track width, inner layer" in names4
    assert "Pad to Silkscreen (jlcpcb library footprints)" in names2
    assert "Pad to Silkscreen (jlcpcb library footprints)" not in names4
    assert two.constraints.min_track_width == 0.127
    assert four.constraints.min_track_width == 0.09
    with pytest.raises(ValueError):
        fab.design_rules("cheap", RuleOptions())


def test_init_creates_a_self_contained_skeleton(tmp_path: Path) -> None:
    result = init_project(tmp_path / "demo", "demo", "jlcpcb", git=GitCli())
    d = result.project.directory
    assert result.git_initialised and (d / ".git").is_dir()
    for rel in (
        "kifab.toml",
        "kifab.lock",
        "demo.kicad_pro",
        "demo.kicad_sch",
        "demo.kicad_pcb",
        "demo.kicad_dru",
        "sym-lib-table",
        "fp-lib-table",
        "lib/kifab/kifab.kicad_sym",
        "lib/jlcpcb/jlcpcb.kicad_sym",
        "lib/jlcpcb/jlcpcb.pretty",
        "lib/jlcpcb/jlcpcb.3dshapes",
    ):
        assert (d / rel).exists(), rel
    project = Project.open(d)
    assert project.config is not None and project.config.fab == "jlcpcb"
    # every library is project-local and exists
    for kind in TableKind:
        table = read_lib_table(d, kind)
        assert table is not None
        for entry in table.entries:
            where = entry.resolve(d)
            assert where is not None and where.exists(), entry
    # the KiCad files parse
    assert read_schematic(d / "demo.kicad_sch").symbols == ()
    assert read_board(d / "demo.kicad_pcb").copper_layers == ("F.Cu", "B.Cu")
    assert '(layer "Edge.Cuts")' in (d / "demo.kicad_pcb").read_text()  # outline
    assert read_symbol_library(d / "lib/jlcpcb/jlcpcb.kicad_sym").symbols == ()
    # the rules are in sync from the start
    assert check_rules(project) == []
    assert gitignore.is_current((d / ".gitignore").read_text())


def test_init_four_layers_and_no_git(tmp_path: Path) -> None:
    result = init_project(tmp_path / "b", "b", "jlcpcb", layers=4)
    d = result.project.directory
    assert not result.git_initialised
    assert read_board(d / "b.kicad_pcb").copper_layers == (
        "F.Cu",
        "In1.Cu",
        "In2.Cu",
        "B.Cu",
    )
    assert "Track width, inner layer" in (d / "b.kicad_dru").read_text()


def test_init_inside_existing_repo_does_not_nest(tmp_path: Path) -> None:
    GitCli().init(tmp_path)
    result = init_project(tmp_path / "boards" / "x", "x", "jlcpcb", git=GitCli())
    assert not result.git_initialised
    assert not (tmp_path / "boards" / "x" / ".git").exists()


def test_init_refuses(tmp_path: Path) -> None:
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "keep.txt").write_text("mine")
    with pytest.raises(InitError, match="not empty"):
        init_project(tmp_path / "full", "full", "jlcpcb")
    assert (tmp_path / "full" / "keep.txt").read_text() == "mine"
    with pytest.raises(InitError, match="invalid project name"):
        init_project(tmp_path / "x", "bad name", "jlcpcb")
    with pytest.raises(InitError, match="unknown fab"):
        init_project(tmp_path / "x", "x", "nofab")
    with pytest.raises(InitError, match="layers"):
        init_project(tmp_path / "x", "x", "jlcpcb", layers=3)
    assert not (tmp_path / "x").exists()


def test_basic_library() -> None:
    lib = basic_library()
    assert {"GND", "+3V3", "VDD", "PWR_FLAG"} <= lib.names()
    assert all(s.is_power for s in lib.symbols)
    # written sorted, and parses back to the same thing
    text = lib.dumps()
    names = [sexpr.atom(n) or "" for n in sexpr.children(sexpr.parse(text), "symbol")]
    assert names == sorted(names)


def test_gitignore_keeps_user_lines() -> None:
    user = "my-notes.txt\n"
    first = gitignore.update(user)
    assert first.startswith(user)
    assert gitignore.is_current(first)
    stale = first.replace("fp-info-cache\n", "")
    assert not gitignore.is_current(stale)
    again = gitignore.update(stale + "after.txt\n")
    assert again == first + "after.txt\n"
    assert gitignore.is_ignored("build/x_gerbers.zip")
    assert gitignore.is_ignored("~b.kicad_pcb.lck")
    assert gitignore.is_ignored("sub/fp-info-cache")
    assert gitignore.is_ignored("b-backups/b-2026.zip")
    assert not gitignore.is_ignored("b.kicad_pcb")
    assert not gitignore.is_ignored("lib/jlcpcb/jlcpcb.pretty/R0603.kicad_mod")


def _adopt(demo_copy: Path) -> Project:
    """Turn the copied example into a kifab project (no rules applied yet)."""
    Config(fab="jlcpcb", project="demo-board").save(demo_copy)
    return Project.open(demo_copy)


def test_apply_rules_to_existing_project(demo_copy: Path) -> None:
    project = _adopt(demo_copy)
    old_dru = (demo_copy / "demo-board.kicad_dru").read_text()
    issues = check_rules(project)
    codes = {i.code for i in issues}
    assert Code.RULES_MISSING in codes  # no managed block yet
    assert Code.RULES_LOOSENED in codes  # board setup min_clearance is 0
    result = apply_rules(project)
    assert {p.name for p in result.changed} == {
        "demo-board.kicad_dru",
        "demo-board.kicad_pro",
    }
    assert result.backup is not None
    assert (result.backup / "demo-board.kicad_dru").read_text() == old_dru
    dru = (demo_copy / "demo-board.kicad_dru").read_text()
    assert dru.count("(version 1)") == 1
    assert dru.startswith("(version 1)\n" + DRU_MARKERS.begin)
    # the old hand-written rules survive after the block
    assert "# TODO demo: a rule the designer keeps." in dru.split(DRU_MARKERS.end)[1]
    rules = ProjectFile.load(project.kicad.pro).board_rules()
    assert rules["min_clearance"] == 0.127
    assert rules["min_resolved_spokes"] == 1  # untouched
    # only errors are gone; the stricter edge clearance (0.5) is a note
    assert [i.severity for i in check_rules(project)] == [Severity.INFO]
    assert apply_rules(project).changed == ()  # idempotent


def test_drift_detection(demo_copy: Path) -> None:
    project = _adopt(demo_copy)
    apply_rules(project)
    dru = project.kicad.dru
    dru.write_text(dru.read_text().replace("(min 0.127mm)", "(min 0.1mm)", 1))
    pro = ProjectFile.load(project.kicad.pro)
    pro.set_board_rules({"min_track_width": 0.1})
    pro.save(project.kicad.pro)
    codes = sorted(i.code.value for i in check_rules(project) if i.severity.rank == 2)
    assert codes == ["rules-drift", "rules-loosened"]
    dru.unlink()
    assert any(i.code is Code.RULES_MISSING for i in check_rules(project))


def test_apply_refuses_while_kicad_open(demo_copy: Path) -> None:
    project = _adopt(demo_copy)
    (demo_copy / "~demo-board.kicad_pro.lck").write_text("")
    with pytest.raises(KicadOpen, match="kicad_pro"):
        apply_rules(project)
    assert apply_rules(project, force=True).changed


def test_render_dru_user_rules_after_block() -> None:
    profile = get_fab("jlcpcb").design_rules("standard", RuleOptions())
    first = render_dru(profile, None)
    with_user = first + '\n(rule "mine"\n\t(constraint clearance (min 1mm))\n)\n'
    again = render_dru(profile, with_user)
    assert again == with_user
    assert render_dru(profile, "(version 1)\n(rule x)\n").endswith("\n\n(rule x)\n")


def test_cli_init_and_rules(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "p"
    assert main(["init", "p", "jlcpcb", "--dir", str(target), "--no-git"]) == 0
    assert "created p (jlcpcb)" in capsys.readouterr().out
    assert main(["init", "p", "jlcpcb", "--dir", str(target)]) == 2
    assert "not empty" in capsys.readouterr().err
    assert main(["rules", "apply", str(target)]) == 0
    assert "already match" in capsys.readouterr().out
    pro = ProjectFile.load(target / "p.kicad_pro")
    pro.set_board_rules({"min_clearance": 0.05})
    pro.save(target / "p.kicad_pro")
    assert main(["rules", "apply", str(target)]) == 0
    out = capsys.readouterr().out
    assert "updated p.kicad_pro" in out and "backup in kifab-backups/" in out
    data = json.loads((target / "p.kicad_pro").read_text())
    assert data["board"]["design_settings"]["rules"]["min_clearance"] == 0.127
    (target / "~p.kicad_pcb.lck").write_text("")
    assert main(["rules", "apply", str(target)]) == 1
    assert "KiCad has p.kicad_pcb open" in capsys.readouterr().err


def test_cli_rules_apply_needs_config(
    demo_copy: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["rules", "apply", str(demo_copy)]) == 2
    assert "kifab.toml" in capsys.readouterr().err


def _edit_pro(project: Project, edit: Callable[[dict[str, Any]], None]) -> None:
    data = json.loads(project.kicad.pro.read_text())
    edit(data)
    project.kicad.pro.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _messages(project: Project) -> list[tuple[Severity, str]]:
    return [
        (i.severity, i.message)
        for i in check_rules(project)
        if i.code in (Code.RULES_LOOSENED, Code.RULES_DRIFT)
    ]


def test_net_classes_and_sizes_against_the_fab(tmp_path: Path) -> None:
    project = init_project(tmp_path / "p", "p", "jlcpcb", git=None).project
    assert check_rules(project) == []

    def edit(data: dict[str, Any]) -> None:
        # KiCad 10's layout: net_settings.classes, design_settings.*_dimensions
        data["net_settings"] = {
            "classes": [
                {"name": "Default", "clearance": 0.2, "track_width": 0.25,
                 "via_diameter": 0.8, "via_drill": 0.3, "diff_pair_width": 0.2,
                 "diff_pair_gap": 0.25},
                {"name": "Fine", "clearance": 0.1, "track_width": 0.1,
                 "via_diameter": 0.45, "via_drill": 0.2},
                {"name": "Power", "track_width": 0.5},  # the rest inherited
            ]
        }  # fmt: skip
        ds = data["board"]["design_settings"]
        ds["track_widths"] = [0.0, 0.2, 0.1]
        ds["via_dimensions"] = [
            {"diameter": 0.0, "drill": 0.0},
            {"diameter": 0.8, "drill": 0.3},
            {"diameter": 0.6, "drill": 0.4},  # ring 0.1 mm
        ]
        ds["diff_pair_dimensions"] = [{"gap": 0.1, "via_gap": 0.25, "width": 0.2}]

    _edit_pro(project, edit)
    found = _messages(project)
    assert all(severity is Severity.ERROR for severity, _ in found)
    text = "\n".join(m for _, m in found)
    assert "net class 'Fine': clearance 0.1 mm is below the fab's 0.127 mm" in text
    assert "net class 'Fine': track width 0.1 mm" in text
    assert "net class 'Fine': via diameter 0.45 mm" in text
    assert "net class 'Fine': via drill 0.2 mm" in text
    assert "'Default'" not in text and "'Power'" not in text
    assert "pre-defined track width 0.1 mm" in text and "track width 0.2 mm" not in text
    assert "pre-defined via 0.6/0.4 mm: annular ring 0.1 mm" in text
    assert "pre-defined via 0.8/0.3" not in text
    assert "pre-defined diff pair 0.2/0.1 mm" in text
    assert len(found) == 8  # the Fine via also leaves a 0.125 mm ring
    # bundle and the CLI see it too
    assert main(["check", str(project.directory), "--no-git"]) == 1


def test_custom_rules_that_loosen_the_fab_are_warned(tmp_path: Path) -> None:
    project = init_project(tmp_path / "p", "p", "jlcpcb", git=None).project
    dru = project.kicad.dru
    dru.write_text(
        dru.read_text()
        + "\n# my own rules\n"
        + '(rule "tight BGA"\n\t(condition "A.Footprint == \'U1\'")\n'
        + "\t(constraint clearance (min 0.1mm))\n\t(constraint track_width (min 4mil)))\n"
        + '(rule "fine" (constraint hole_size (min 0.3mm)))\n'
    )
    found = _messages(project)
    assert [s for s, _ in found] == [Severity.WARNING, Severity.WARNING]
    assert found[0][1] == (
        "custom rule 'tight BGA' allows clearance 0.1 mm, below the fab's 0.127 mm"
    )
    assert "track_width 0.1016 mm" in found[1][1]  # 4 mil
    assert main(["check", str(project.directory), "--no-git"]) == 0  # warnings only
    assert main(["check", str(project.directory), "--no-git", "--strict"]) == 1


def test_layer_count_must_match_the_config(tmp_path: Path) -> None:
    project = init_project(tmp_path / "p", "p", "jlcpcb", layers=4, git=None).project
    assert check_rules(project) == []
    cfg = project.directory / "kifab.toml"
    cfg.write_text(cfg.read_text().replace("layers = 4", "layers = 2"))
    found = _messages(Project.open(project.directory))
    assert (
        Severity.ERROR,
        "the board has 4 copper layers but kifab.toml says 2; the fab's limits "
        "depend on it",
    ) in found


def test_parse_length() -> None:
    from kifab.core.design_rules import parse_length

    assert parse_length("0.1mm") == 0.1
    assert parse_length("4mil") == pytest.approx(0.1016)
    assert parse_length("0.004in") == pytest.approx(0.1016)
    assert parse_length("100um") == pytest.approx(0.1)
    assert parse_length("0.2") == 0.2
    assert parse_length("wide") is None
