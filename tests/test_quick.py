import pytest

from kifab.core.quick import (
    PassiveKind,
    QuickQuery,
    alternatives,
    format_value,
    parse_quick,
    parse_value,
    quick_search,
)
from kifab.fabs.jlcpcb.search import FixtureClient, JlcpcbCatalog
from tests.conftest import FIXTURES

R, C, L = PassiveKind.RESISTOR, PassiveKind.CAPACITOR, PassiveKind.INDUCTOR


@pytest.mark.parametrize(
    "text, value",
    [
        ("4k7", 4700),
        ("4K7", 4700),
        ("4.7k", 4700),
        ("4,7k", 4700),
        ("4700", 4700),
        ("1R5", 1.5),
        ("0R", 0),
        ("10Ω", 10),
        ("4.7kΩ", 4700),
        ("1M", 1e6),
        ("18p", 18e-12),
        ("18pF", 18e-12),
        ("100n", 100e-9),
        ("4u7", 4.7e-6),
        ("4.7µF", 4.7e-6),
        ("10uH", 10e-6),
    ],
)
def test_parse_value(text: str, value: float) -> None:
    assert parse_value(text) == pytest.approx(value, rel=1e-9, abs=1e-18)


@pytest.mark.parametrize("text", ["", "k", "4.7k7", "abc", "4kk", "1.2.3"])
def test_parse_value_rejects(text: str) -> None:
    assert parse_value(text) is None


def test_parse_quick() -> None:
    assert parse_quick("R 4k7") == QuickQuery(R, 4700)
    assert parse_quick("R4.7k") == QuickQuery(R, 4700)
    assert parse_quick("r 4700 0603 1%") == QuickQuery(R, 4700, ("0603", "1%"))
    assert parse_quick("C 18p") == QuickQuery(C, 18e-12)
    assert parse_quick("C18p 0402") == QuickQuery(C, 18e-12, ("0402",))
    assert parse_quick("L 10u") == QuickQuery(L, 10e-6)
    # ordinary searches stay ordinary
    for text in ("C900001", "C 100", "L7805", "100nF 0603", "RC0603", "R abc", "4k7"):
        assert parse_quick(text) is None, text


def test_format_and_keyword() -> None:
    assert [format_value(v) for v in (4700, 18e-12, 1.5, 999.9, 1e6, 0, 330)] == [
        "4.7k",
        "18p",
        "1.5",
        "1k",
        "1M",
        "0",
        "330",
    ]
    q = QuickQuery(R, 4700, ("0603",))
    assert q.label == "4.7kΩ" and q.keyword() == "4.7kΩ 0603"
    assert q.keyword(5100) == "5.1kΩ 0603"
    assert QuickQuery(C, 4.7e-6).keyword() == "4.7uF"


def test_alternatives() -> None:
    assert alternatives(4700) == [4700, 4300, 5100]
    assert alternatives(18e-12) == [18e-12]  # 16p and 20p are just outside ±10%
    assert alternatives(1000) == [1000, 910, 1100]  # across the decade
    assert alternatives(0) == [0]
    assert alternatives(4750) == [4750, 4700, 5100, 4300]  # nearest first


@pytest.fixture
def catalog() -> JlcpcbCatalog:
    return JlcpcbCatalog(FixtureClient(FIXTURES))


def test_quick_search(catalog: JlcpcbCatalog) -> None:
    query = parse_quick("R 4k7 0603")
    assert query is not None
    exact = quick_search(catalog, query)
    assert exact.searched == ("4.7kΩ 0603",)
    # the thermistor is not a resistor; Basic before out-of-stock Extended
    assert [h.hit.id for h in exact.hits] == ["C900002", "C900011"]
    wide = quick_search(catalog, query, with_alternatives=True)
    assert wide.searched == ("4.7kΩ 0603", "4.3kΩ 0603", "5.1kΩ 0603")
    assert [(h.hit.id, h.exact) for h in wide.hits] == [
        ("C900002", True),
        ("C900011", True),
        ("C900009", False),  # Basic 5.1k before Extended 4.3k
        ("C900010", False),
    ]
    assert wide.hits[2].deviation == pytest.approx(400 / 4700)
    basic = quick_search(catalog, query, with_alternatives=True, basic_only=True)
    assert [h.hit.id for h in basic.hits] == ["C900002", "C900009"]
    caps = quick_search(catalog, QuickQuery(C, 18e-12))
    assert [h.hit.id for h in caps.hits] == ["C900012"]
    coils = quick_search(catalog, QuickQuery(L, 10e-6))
    assert [h.hit.id for h in coils.hits] == ["C900013"]
    # a resistor query never returns capacitors and vice versa
    assert quick_search(catalog, QuickQuery(C, 4700, ("0603",))).hits == ()


def test_cli(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from kifab.cli.main import main

    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))
    assert main(["component", "search", "R", "4k7", "0603", "-a"]) == 0
    out = capsys.readouterr().out
    assert out.index("C900002") < out.index("C900009") < out.index("C900010")
    assert "alternative: 5.1kΩ (+8.5%)" in out and "C900014" not in out
    assert "searched '4.7kΩ 0603', '4.3kΩ 0603', '5.1kΩ 0603'" in out
    assert main(["component", "search", "C18p", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["quick"]["label"] == "18pF"
    assert [h["id"] for h in payload["hits"]] == ["C900012"]
    assert payload["hits"][0]["deviation"] == 0
    # --plain searches the text as typed
    assert main(["component", "search", "--plain", "R", "4k7"]) == 0
    assert "no JLCPCB parts found for 'R 4k7'" in capsys.readouterr().out
