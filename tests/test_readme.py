import json
from pathlib import Path

import pytest

from kifab.cli.main import main
from kifab.core.issues import Code
from kifab.core.manufacturing import Bom, BomLine
from kifab.core.partref import PartRef
from kifab.core.project import Project
from kifab.core.reporter import RecordingReporter
from kifab.core.readme import (
    BEGIN,
    END,
    ReadmeOptions,
    bom_table,
    generate_readme,
    merge_readme,
    normalize_svg,
)

pytestmark = pytest.mark.usefixtures("kicad_cli_stub")


def snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(directory)): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def test_generates_readme_and_images(demo_copy: Path) -> None:
    result = generate_readme(Project.open(demo_copy))
    assert result.ok and result.created
    text = (demo_copy / "README.md").read_text()
    assert text.startswith("# demo-board\n")
    assert BEGIN in text and text.rstrip().endswith(END)
    for image in (
        "pcb-top.png",
        "pcb-bottom.png",
        "schematic-demo-board.svg",
        "schematic-demo-board-power.svg",
    ):
        assert (demo_copy / "images" / image).is_file()
        assert f"images/{image}" in text
    assert "### power" in text and "## Bill of materials" in text
    assert "| Qty | Value | References | Footprint | Part |" in text
    assert result.bom is not None and result.bom.lines


def test_svg_title_has_no_date(demo_copy: Path) -> None:
    generate_readme(Project.open(demo_copy))
    svg = (demo_copy / "images" / "schematic-demo-board.svg").read_text()
    assert "<title>demo-board</title>" in svg and "2026" not in svg


def test_idempotent(demo_copy: Path) -> None:
    generate_readme(Project.open(demo_copy))
    first = snapshot(demo_copy)
    again = generate_readme(Project.open(demo_copy))
    assert again.ok and not again.created and not again.appended
    assert snapshot(demo_copy) == first


def test_text_around_the_block_is_kept(demo_copy: Path) -> None:
    generate_readme(Project.open(demo_copy))
    path = demo_copy / "README.md"
    path.write_text("Intro I wrote.\n\n" + path.read_text() + "\nOutro.\n")
    generate_readme(Project.open(demo_copy), ReadmeOptions(bom=False))
    text = path.read_text()
    assert text.startswith("Intro I wrote.\n") and text.endswith("Outro.\n")
    assert "Bill of materials" not in text and text.count(BEGIN) == 1


def test_readme_without_block_gets_it_appended(demo_copy: Path) -> None:
    (demo_copy / "README.md").write_text("# My board\n\nHand written.\n")
    result = generate_readme(Project.open(demo_copy))
    text = (demo_copy / "README.md").read_text()
    assert result.appended and Code.README_APPENDED in {i.code for i in result.issues}
    assert text.startswith("# My board\n\nHand written.\n\n" + BEGIN)


def test_stale_images_removed_other_files_kept(demo_copy: Path) -> None:
    images = demo_copy / "images"
    images.mkdir()
    (images / "pcb-iso.png").write_bytes(b"old")
    (images / "logo.png").write_bytes(b"mine")
    generate_readme(Project.open(demo_copy))
    assert not (images / "pcb-iso.png").exists() and (images / "logo.png").exists()
    generate_readme(Project.open(demo_copy), ReadmeOptions(views=("top", "iso")))
    assert (images / "pcb-iso.png").is_file() and not (
        images / "pcb-bottom.png"
    ).exists()


def test_failure_keeps_old_files(
    demo_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generate_readme(Project.open(demo_copy))
    before = snapshot(demo_copy)
    monkeypatch.setenv("STUB_FAIL", "pcb render")
    result = generate_readme(Project.open(demo_copy))
    assert not result.ok and Code.TOOL_FAILED in {i.code for i in result.issues}
    assert snapshot(demo_copy) == before


def test_kicad_open_and_bad_view(demo_copy: Path) -> None:
    (demo_copy / "~demo-board.kicad_sch.lck").write_text("")
    project = Project.open(demo_copy)
    assert Code.KICAD_OPEN in {i.code for i in generate_readme(project).issues}
    assert generate_readme(project, ReadmeOptions(force=True)).ok
    bad = generate_readme(project, ReadmeOptions(views=("side",), force=True))
    assert Code.BAD_INPUT in {i.code for i in bad.issues}


def test_bom_table_links_lcsc() -> None:
    bom = Bom(
        (
            BomLine(
                "4k7|x",
                ("R1", "R2"),
                "kifab:R_0603",
                PartRef.with_sku("lcsc", "C23162", mpn="RC0603"),
            ),
        )
    )
    row = bom_table(bom).splitlines()[2]
    assert row.startswith("| 2 | 4k7\\|x | R1, R2 | R_0603 |")
    assert "[C23162](https://www.lcsc.com/product-detail/C23162.html), RC0603" in row


def test_merge_and_normalize() -> None:
    block = f"{BEGIN}\nx\n{END}"
    assert merge_readme(None, block, "t") == (f"# t\n\n{block}\n", False)
    old = f"a\n{BEGIN}\nold\n{END}\nb\n"
    assert merge_readme(old, block, "t") == (f"a\n{block}\nb\n", False)
    assert normalize_svg("<title>a\nb 2026</title>", "n") == "<title>n</title>"


def test_cli(demo_copy: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["readme", str(demo_copy), "--views", "top", "--no-bom"]) == 0
    assert "images/pcb-top.png" in (demo_copy / "README.md").read_text()
    assert not (demo_copy / "images" / "pcb-bottom.png").exists()
    capsys.readouterr()
    assert main(["readme", str(demo_copy), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] and data["readme"].endswith("README.md")
    assert main(["readme", str(demo_copy), "--views", "nope"]) == 1


def render_steps(directory: Path) -> list[str]:
    reporter = RecordingReporter()
    assert generate_readme(Project.open(directory), reporter=reporter).ok
    return [detail for name, detail in reporter.steps if name == "render"]


def test_render_noise_keeps_the_old_image(
    demo_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_RENDER_SEED", "1")
    assert render_steps(demo_copy) == ["Top (new)", "Bottom (new)"]
    first = snapshot(demo_copy)
    monkeypatch.setenv("STUB_RENDER_SEED", "5")  # different jitter, same board
    assert render_steps(demo_copy) == ["Top (unchanged)", "Bottom (unchanged)"]
    assert snapshot(demo_copy) == first  # byte for byte, README too


def test_a_real_change_updates_the_image(
    demo_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_RENDER_SEED", "1")
    render_steps(demo_copy)
    before = (demo_copy / "images" / "pcb-top.png").read_bytes()
    monkeypatch.setenv("STUB_RENDER_SEED", "5")
    monkeypatch.setenv("STUB_RENDER_MARK", "3")  # a label-sized difference
    assert render_steps(demo_copy) == ["Top (updated)", "Bottom (updated)"]
    assert (demo_copy / "images" / "pcb-top.png").read_bytes() != before


def test_unreadable_old_image_is_replaced(demo_copy: Path) -> None:
    (demo_copy / "images").mkdir()
    (demo_copy / "images" / "pcb-top.png").write_bytes(b"garbage")
    assert render_steps(demo_copy) == ["Top (updated)", "Bottom (new)"]


def test_renders_use_perspective(
    demo_copy: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "cli.log"
    monkeypatch.setenv("STUB_CLI_LOG", str(log))
    generate_readme(Project.open(demo_copy))
    renders = [ln for ln in log.read_text().splitlines() if "pcb render" in ln]
    assert len(renders) == 2 and all("--perspective" in ln for ln in renders)
    assert all("--quality high" in ln for ln in renders)
