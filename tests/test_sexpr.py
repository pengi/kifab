from pathlib import Path

import pytest

from kifab.kicad import sexpr
from tests.conftest import DEMO


def test_parse_nested_and_escapes() -> None:
    tree = sexpr.parse('(a (b "x \\"q\\" y") c 1.5)')
    assert sexpr.head(tree) == "a"
    assert sexpr.atom(sexpr.first(tree, "b")) == 'x "q" y'
    assert isinstance(tree[2], sexpr.Sym)
    assert not isinstance(tree[1], sexpr.Sym)


def test_properties_and_flags() -> None:
    tree = sexpr.parse(
        '(symbol (property "A" "1") (property "B" "2" (at 0 0 0)) (dnp yes) (in_bom no))'
    )
    assert sexpr.properties(tree) == {"A": "1", "B": "2"}
    assert sexpr.flag(tree, "dnp")
    assert not sexpr.flag(tree, "in_bom", default=True)
    assert sexpr.flag(tree, "missing", default=True)


@pytest.mark.parametrize("bad", ["(a (b)", "(a))", "", "(a) (b)", "atom"])
def test_malformed(bad: str) -> None:
    with pytest.raises(sexpr.SexprError):
        sexpr.parse(bad)


def test_quote_round_trip() -> None:
    text = 'a "quoted" \\ back\nnew'
    node: sexpr.Node = [sexpr.Sym("x"), text]
    assert sexpr.atom(sexpr.parse(sexpr.dumps(node))) == text


def test_number_format() -> None:
    assert sexpr.number(1.0) == "1"
    assert sexpr.number(0.127) == "0.127"
    assert sexpr.number(-0.0) == "0"
    assert sexpr.number(1e-7) == "0.0000001"


def test_writer_layout() -> None:
    node = sexpr.parse(
        '(top (version 1) (sym "n" (pts (xy 0 0) (xy 1 1)) (font (size 1 1))))'
    )
    assert sexpr.dumps(node) == (
        "(top\n"
        "\t(version 1)\n"
        '\t(sym "n"\n'
        "\t\t(pts\n"
        "\t\t\t(xy 0 0) (xy 1 1)\n"
        "\t\t)\n"
        "\t\t(font\n"
        "\t\t\t(size 1 1)\n"
        "\t\t)\n"
        "\t)\n"
        ")\n"
    )


def test_long_point_lists_wrap_like_kicad() -> None:
    pts = " ".join(f"(xy {i}.254 -{i}.508)" for i in range(30))
    text = sexpr.dumps(sexpr.parse(f"(a (polyline (pts {pts})))"))
    lines = [ln for ln in text.splitlines() if "(xy" in ln]
    assert len(lines) > 2
    assert all(len(ln.expandtabs(1)) < 99 + 20 for ln in lines)
    assert sexpr.parse(text) == sexpr.parse(f"(a (polyline (pts {pts})))")


@pytest.mark.parametrize(
    "path",
    [
        DEMO / "demo-board.kicad_sch",
        DEMO / "demo-board.kicad_pcb",
        DEMO / "demo-board.pretty" / "LED_0603.kicad_mod",
    ],
    ids=lambda p: Path(p).name,
)
def test_fixture_files_round_trip(path: Path) -> None:
    """KiCad-format files come out of parse + dumps unchanged.

    (Against files saved by KiCad itself: tests/test_real_kicad.py.)
    """
    text = path.read_text(encoding="utf-8")
    assert sexpr.dumps(sexpr.parse(text)) == text
