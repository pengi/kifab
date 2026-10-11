"""Integration tests against a real kicad-cli 10 (skipped when there is none).

Uses ``$KIFAB_REAL_KICAD_CLI`` (a command, e.g. ``scripts/kicad-cli-docker``) or
``kicad-cli`` on PATH. Run them with ``pytest tests/test_real_kicad.py``.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from kifab.core.bundle import BundleOptions, bundle
from kifab.core.check import run_check
from kifab.core.git import GitCli
from kifab.core.init import init_project
from kifab.core.library import vendor_part
from kifab.core.project import Project
from kifab.kicad import sexpr
from kifab.kicad.cli import ENV_KICAD_CLI, BomColumns, KicadCli
from tests.conftest import DEMO_NAME, EXPECTED, FIXTURES

_COMMAND = os.environ.get("KIFAB_REAL_KICAD_CLI") or shutil.which("kicad-cli")
pytestmark = pytest.mark.skipif(not _COMMAND, reason="no real kicad-cli")


@pytest.fixture
def real_cli(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    assert _COMMAND
    command = shlex.split(_COMMAND)
    if Path(command[0]).name != "kicad-cli":
        command[0] = str(Path(command[0]).resolve())
    monkeypatch.setenv(ENV_KICAD_CLI, shlex.join(command))
    return command


def commit_all(d: Path) -> None:
    git = ["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@e"]
    subprocess.run(["git", "-C", str(d), "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "x"], check=True)


def test_version(real_cli: list[str]) -> None:
    assert KicadCli().version().startswith("10.")


@pytest.mark.parametrize(
    "fab, layers", [("jlcpcb", 2), ("jlcpcb", 4), ("aisler", 2), ("aisler", 4)]
)
def test_new_project_bundles_clean(
    real_cli: list[str], tmp_path: Path, fab: str, layers: int
) -> None:
    project = init_project(
        tmp_path / "p", "p", fab, layers=layers, git=GitCli()
    ).project
    commit_all(project.directory)
    result = bundle(project, git=GitCli())
    assert result.ok, result.issues
    names = {p.name for p in result.files}
    assert ("p-In1_Cu.g1" in names) == (layers == 4)
    first = {p.name: p.read_bytes() for p in result.files}
    again = bundle(project, git=GitCli())
    assert {p.name: p.read_bytes() for p in again.files} == first  # reproducible


def test_demo_bundle_matches_the_expected_files(
    real_cli: list[str], demo_copy: Path
) -> None:
    # no global KiCad libraries here: the demo's stock footprints fail DRC
    result = bundle(
        Project.open(demo_copy), BundleOptions(skip_check=True, skip_drc=True)
    )
    assert result.ok, result.issues
    for suffix in ("bom_jlcpcb.csv", "cpl_jlcpcb.csv"):
        mine = (demo_copy / "build" / f"{DEMO_NAME}_{suffix}").read_bytes()
        assert mine == (EXPECTED / f"{DEMO_NAME}_{suffix}").read_bytes(), suffix


def test_exports_match_the_format_samples(
    real_cli: list[str], demo_copy: Path, tmp_path: Path
) -> None:
    """tests/fixtures/kicad_cli/*.csv are what kicad-cli writes for the demo."""
    cli = KicadCli()
    pcb, sch = (
        demo_copy / f"{DEMO_NAME}.kicad_pcb",
        demo_copy / f"{DEMO_NAME}.kicad_sch",
    )
    cli.export_pos(pcb, tmp_path / "all.csv", smd_only=False)
    cli.export_pos(pcb, tmp_path / "smd.csv", smd_only=True)
    cli.export_bom(
        sch,
        tmp_path / "bom.csv",
        BomColumns(
            ("Value", "Reference", "Footprint", "LCSC"),
            ("Value", "Reference", "Footprint", "sku:lcsc"),
            ("Value", "Footprint", "LCSC"),
        ),
    )
    samples = FIXTURES / "kicad_cli"
    for mine, sample in (("all", "pos_all"), ("smd", "pos_smd"), ("bom", "bom_raw")):
        assert (tmp_path / f"{mine}.csv").read_text() == (
            samples / f"{sample}.csv"
        ).read_text(), sample
    report = cli.drc(pcb, tmp_path / "drc.json")
    assert {v.type for v in report.violations} >= {"lib_footprint_issues"}


def test_files_saved_by_kicad_round_trip(real_cli: list[str], demo_copy: Path) -> None:
    """KiCad re-saves the demo files; kifab's writer reproduces them byte for byte."""
    for kind, path in (
        ("pcb", demo_copy / f"{DEMO_NAME}.kicad_pcb"),
        ("sch", demo_copy / f"{DEMO_NAME}.kicad_sch"),
    ):
        subprocess.run(
            [*real_cli, kind, "upgrade", "--force", str(path)],
            check=True,
            capture_output=True,
        )
        text = path.read_text(encoding="utf-8")
        assert sexpr.dumps(sexpr.parse(text)) == text, path.name


def test_vendored_library_is_readable(
    real_cli: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))
    project = init_project(tmp_path / "v", "v", "jlcpcb").project
    for part in ("C900001", "C900004", "C900005", "C900006"):
        vendor_part(project, part)
    assert run_check(project).issues == []
    lib = project.directory / "lib"
    out = tmp_path / "svg"
    for kind, target in (
        ("sym", lib / "jlcpcb/jlcpcb.kicad_sym"),
        ("sym", lib / "kifab/kifab.kicad_sym"),
        ("fp", lib / "jlcpcb/jlcpcb.pretty"),
    ):
        dest = out / target.name
        dest.mkdir(parents=True)
        subprocess.run(
            [*real_cli, kind, "export", "svg", "-o", str(dest), str(target)],
            check=True,
            capture_output=True,
        )
        assert list(dest.glob("*.svg")), target


def test_kicad_reads_the_description(
    real_cli: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kifab.suppliers.base import get_supplier
    from kifab.kicad.symbol_lib import read_symbol_library

    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))
    project = init_project(tmp_path / "d", "d", "jlcpcb").project
    vendor_part(project, "C900001", catalog=get_supplier("jlcpcb").catalog())
    lib = project.directory / "lib/jlcpcb/jlcpcb.kicad_sym"
    assert "ki_description" in lib.read_text()  # the file's 20211014 syntax
    upgraded = tmp_path / "upgraded.kicad_sym"
    subprocess.run(
        [*real_cli, "sym", "upgrade", "--force", "-o", str(upgraded), str(lib)],
        check=True,
        capture_output=True,
    )
    sym = read_symbol_library(upgraded).get("KF-R330-0603")
    assert sym is not None
    assert sym.description == "330R 0603 Chip Resistor (synthetic test part)"
    assert '(property "Description"' in upgraded.read_text()
    # kifab's hidden pin names/numbers (20211014 syntax) survive in KiCad's own
    pin_names = sexpr.first(sym.node, "pin_names")
    assert pin_names is not None and ["hide", "yes"] in pin_names
    pin_numbers = sexpr.first(sym.node, "pin_numbers")
    assert pin_numbers is not None and ["hide", "yes"] in pin_numbers


def test_readme_images(real_cli: list[str], tmp_path: Path) -> None:
    from kifab.core.pngdiff import compare
    from kifab.core.readme import generate_readme

    project = tmp_path / DEMO_NAME
    shutil.copytree(FIXTURES / DEMO_NAME, project)
    result = generate_readme(Project.open(project))
    assert result.ok, result.issues
    top = project / "images" / "pcb-top.png"
    png = top.read_bytes()
    assert png.startswith(b"\x89PNG")
    svg = (project / "images" / "schematic-demo-board.svg").read_text()
    assert "<svg" in svg and "<title>demo-board</title>" in svg
    readme = (project / "README.md").read_bytes()
    # Ray traced renders differ per run; only noise, so the files are kept.
    again = generate_readme(Project.open(project))
    assert again.ok
    assert top.read_bytes() == png
    assert (project / "README.md").read_bytes() == readme
    # The noise is really there, and below the floor; a changed label is not.
    fresh = project / "fresh.png"
    KicadCli().render_pcb(
        project / "demo-board.kicad_pcb", fresh, side="top", width=1200, height=900
    )
    assert fresh.read_bytes() != png and not compare(png, fresh.read_bytes()).real()
    pcb = project / "demo-board.kicad_pcb"
    pcb.write_text(pcb.read_text().replace('"R3"', '"R9"'))
    KicadCli().render_pcb(pcb, fresh, side="top", width=1200, height=900)
    assert compare(png, fresh.read_bytes()).real()
    assert generate_readme(Project.open(project)).ok
    assert top.read_bytes() != png
