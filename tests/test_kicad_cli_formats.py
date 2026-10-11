"""kicad-cli 10 output formats (samples in tests/fixtures/kicad_cli, synthetic
content in the layout real kicad-cli 10.0.6 writes; see tests/test_real_kicad.py)."""

from kifab.core.manufacturing import parse_kicad_bom, parse_kicad_pos
from kifab.fabs.jlcpcb.export import bom_csv, cpl_csv
from kifab.kicad.cli import parse_drc_report
from tests.conftest import DEMO_NAME, EXPECTED, FIXTURES

SAMPLES = FIXTURES / "kicad_cli"


def test_drc_report_format() -> None:
    report = parse_drc_report((SAMPLES / "drc.json").read_text())
    assert len(report.violations) == 2 and report.parity == ()
    first = report.violations[0]
    assert first.type == "lib_footprint_issues" and first.severity == "warning"
    assert first.items[0].uuid and first.location() == "(105, 80) mm"
    assert not first.excluded


def test_rule_violations_name_the_rule() -> None:
    report = parse_drc_report((SAMPLES / "rules_drc.json").read_text())
    by_type = {v.type: v.description for v in report.violations}
    assert "Track width, outer layer (1oz copper)" in by_type["track_width"]
    assert "rule 'Silkscreen text'" in by_type["text_height"]
    assert "rule 'Silkscreen text'" in by_type["text_thickness"]


def test_exports_convert_to_the_expected_order_files() -> None:
    """kicad-cli's BOM/pos CSV -> kifab -> byte-identical to the expected files."""
    bom = parse_kicad_bom((SAMPLES / "bom_raw.csv").read_text())
    every = parse_kicad_pos((SAMPLES / "pos_all.csv").read_text())
    smd = parse_kicad_pos((SAMPLES / "pos_smd.csv").read_text())
    through_hole = every.references() - smd.references()
    assert through_hole == {"J1", "TP1"}
    assert (
        bom_csv(bom.without(through_hole))
        == (EXPECTED / f"{DEMO_NAME}_bom_jlcpcb.csv").read_bytes()
    )
    assert cpl_csv(smd) == (EXPECTED / f"{DEMO_NAME}_cpl_jlcpcb.csv").read_bytes()


def test_inner_layer_gerber_names() -> None:
    names = (SAMPLES / "gerber_names_4layer.txt").read_text().split()
    assert "board-In1_Cu.g1" in names and "board-In2_Cu.g2" in names
