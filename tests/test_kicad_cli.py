import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from kifab.kicad.cli import (
    BomColumns,
    KicadCli,
    RunResult,
    ToolFailed,
    ToolMissing,
    kicad_cli_command,
    parse_drc_report,
)
from tests.conftest import DEMO


class FakeRunner:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stderr = stderr

    def run(self, argv: Sequence[str], cwd: Path | None = None) -> RunResult:
        self.calls.append(list(argv))
        return RunResult(self.returncode, "", self.stderr)


def test_command_lines() -> None:
    runner = FakeRunner()
    cli = KicadCli(["kicad-cli"], runner)
    cli.export_gerbers(Path("b.kicad_pcb"), Path("/out"), ["F.Cu", "B.Cu"])
    cli.export_pos(Path("b.kicad_pcb"), Path("p.csv"), smd_only=True)
    cli.export_bom(
        Path("b.kicad_sch"),
        Path("bom.csv"),
        BomColumns(("Value", "Reference"), ("Comment", "Designator"), ("Value",)),
    )
    gerbers, pos, bom = runner.calls
    assert gerbers[:4] == ["kicad-cli", "pcb", "export", "gerbers"]
    assert gerbers[gerbers.index("-o") + 1] == "/out/"
    assert gerbers[gerbers.index("--layers") + 1] == "F.Cu,B.Cu"
    assert "--smd-only" in pos and "--exclude-dnp" in pos
    assert bom[bom.index("--labels") + 1] == "Comment,Designator"
    assert bom[bom.index("--ref-range-delimiter") + 1] == ""


def test_failure_carries_last_line() -> None:
    cli = KicadCli(["kicad-cli"], FakeRunner(3, "first\nUnknown layer X"))
    with pytest.raises(ToolFailed, match="exit 3.*Unknown layer X"):
        cli.export_drill(Path("b.kicad_pcb"), Path("/out"))


def test_missing_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KIFAB_KICAD_CLI", raising=False)
    monkeypatch.setenv("PATH", "")
    with pytest.raises(ToolMissing, match="kicad-cli not found"):
        kicad_cli_command()


def test_parse_drc_report() -> None:
    report = parse_drc_report(
        json.dumps(
            {
                "violations": [
                    {
                        "type": "clearance",
                        "description": "Clearance violation",
                        "severity": "error",
                        "items": [{"description": "Track", "pos": {"x": 1, "y": 2.5}}],
                    },
                    {"type": "silk_overlap", "severity": "warning", "excluded": True},
                ],
                "unconnected_items": [{"type": "unconnected_items"}],
            }
        )
    )
    active = report.active()
    assert [v.type for v in active] == ["clearance", "unconnected_items"]
    assert active[0].location() == "(1, 2.5) mm"
    assert report.parity == ()


def test_stub_round_trip(kicad_cli_stub: str, tmp_path: Path) -> None:
    """The real subprocess path, against the stand-in."""
    cli = KicadCli()
    assert cli.version() == "10.0.6"
    report = cli.drc(DEMO / "demo-board.kicad_pcb", tmp_path / "drc.json")
    assert report.active() == []
    cli.export_pos(DEMO / "demo-board.kicad_pcb", tmp_path / "pos.csv", smd_only=True)
    lines = (tmp_path / "pos.csv").read_text().splitlines()
    assert lines[0] == "Ref,Val,Package,PosX,PosY,Rot,Side"
    assert not any(line.startswith('"J1"') for line in lines)  # through-hole


def test_stub_drc_violation(
    kicad_cli_stub: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_DRC_FAIL", "1")
    monkeypatch.setenv("STUB_DRC_EXCLUDED", "1")
    report = KicadCli().drc(DEMO / "demo-board.kicad_pcb", tmp_path / "d.json")
    assert [v.type for v in report.active()] == ["silk_overlap"]
