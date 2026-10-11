from pathlib import Path

from kifab.kicad.board import Side, read_board, read_footprint_file
from kifab.kicad.libid import LibId
from tests.conftest import DEMO, FIXTURES


def test_demo_board() -> None:
    board = read_board(DEMO / "demo-board.kicad_pcb")
    assert board.copper_layers == ("F.Cu", "B.Cu")
    parts = board.by_reference()
    assert "MH1" not in parts  # board_only
    assert len(parts) == 11
    c1 = parts["C1"]
    assert c1.lib_id == LibId("jlcpcb", "C0603")
    assert (c1.x, c1.y, c1.rotation, c1.side) == (120.0, 60.0, -90.0, Side.TOP)
    assert c1.footprint.smd and c1.lcsc == "C900003"
    assert parts["R4"].side is Side.BOTTOM
    assert parts["R3"].dnp
    assert parts["J1"].footprint.through_hole and parts["J1"].footprint.has_drilled_pads
    assert parts["J1"].footprint.models[0].startswith("${KICAD10_3DMODEL_DIR}/")
    assert parts["D1"].lib_id.nickname == "demo-board"


def test_layers_with_user_names() -> None:
    board = read_board(DEMO / "demo-board.kicad_pcb")
    silk = next(l for l in board.layers if l.name == "F.SilkS")
    assert silk.display_name == "F.Silkscreen"


def test_inner_layers_in_stack_order(tmp_path: Path) -> None:
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_text(
        '(kicad_pcb (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (6 "In2.Cu" signal) '
        '(4 "In1.Cu" power "GND") (25 "Edge.Cuts" user)))'
    )
    assert read_board(pcb).copper_layers == ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")


def test_legacy_module_footprint_file() -> None:
    fp = read_footprint_file(
        FIXTURES / "easyeda2kicad_output/C900004/easyeda.pretty/C0805.kicad_mod"
    )
    assert fp.lib_id.name == "C0805"
    assert fp.through_hole and not fp.has_drilled_pads  # the easyeda2kicad bug
    assert fp.models
    header = read_footprint_file(
        FIXTURES / "easyeda2kicad_output/C900006/easyeda.pretty" / "HDR-TH_8P.kicad_mod"
    )
    assert header.has_drilled_pads
