import gzip
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from kifab.cli.main import main
from kifab.core.net import NetError, UrllibClient
from kifab.core.parts import CatalogError, LibraryType
from kifab.fabs.base import get_fab
from kifab.suppliers.jlcpcb.search import (
    FixtureClient,
    JlcpcbCatalog,
    lookup,
    parse_part,
    parse_search,
    search,
)
from tests.conftest import FIXTURES


@pytest.fixture
def client() -> FixtureClient:
    return FixtureClient(FIXTURES)


def test_parse_part_fields(client: FixtureClient) -> None:
    page = search(client, "C900001")
    hit = page.hits[0]
    assert (hit.id, hit.mpn, hit.package) == ("C900001", "KF-R330-0603", "0603")
    assert hit.manufacturer == "Example Components"
    assert hit.library_type is LibraryType.BASIC and not hit.library_type.setup_fee
    assert hit.stock == 500000
    assert hit.unit_price(1) == 0.0021 and hit.unit_price(5000) == 0.00168
    assert hit.unit_price(10**6) == 0.00126  # open-ended last break
    assert hit.prices[0].min_qty == 1  # sorted
    assert ("Value", "330R") in hit.attributes
    assert hit.datasheet.endswith(".pdf")


def test_images(client: FixtureClient) -> None:
    hit = lookup(client, "C900001")
    assert hit is not None and hit.images == (
        "https://example.invalid/images/C900001.png",
    )
    catalog = JlcpcbCatalog(client)
    assert catalog.image(hit.images[0]).startswith(b"\x89PNG")
    with pytest.raises(CatalogError, match="404"):
        catalog.image("https://example.invalid/images/nothing.png")
    no_image = lookup(client, "C900007")
    assert no_image is not None and no_image.images == ()
    # every known image field, protocol-relative URLs, duplicates dropped
    item = {
        "componentCode": "C1",
        "imageList": [{"productBigImage": "//img.invalid/big.jpg", "productImage": ""}],
        "componentImageUrl": "https://img.invalid/big.jpg",
        "minImage": "https://img.invalid/min.jpg",
        "productImage": "javascript:alert(1)",
    }
    assert parse_part(item).images == (
        "https://img.invalid/big.jpg",
        "https://img.invalid/min.jpg",
    )


def test_keyword_search_and_basic_filter(client: FixtureClient) -> None:
    page = search(client, "100nF 0603")
    assert page.total == 1534 and len(page.hits) == 2 and page.pages == 154
    basic = search(client, "100nF 0603", basic_only=True)
    assert basic.hits and all(h.library_type is LibraryType.BASIC for h in basic.hits)
    assert search(client, "KF-MOD-1", basic_only=True).hits == ()
    empty = search(client, "xyzzy-no-such-part")
    assert empty.hits == () and empty.total == 0


def test_lookup_exact(client: FixtureClient) -> None:
    hit = lookup(client, "c900003")  # the answer lists 2 parts, only one matches
    assert hit is not None and hit.id == "C900003"
    module = lookup(client, "C900005")
    assert module is not None and module.library_type is LibraryType.EXTENDED
    assert lookup(client, "C9999999999") is None
    catalog = JlcpcbCatalog(client)
    assert catalog.lookup("C900003") is catalog.lookup("C900003")  # cached


@pytest.mark.parametrize(
    "answer, message",
    [
        ([], "not a JSON object"),
        ({"code": 500, "message": "boom"}, "boom"),
        ({"code": 200, "data": {}}, "componentPageInfo"),
    ],
)
def test_bad_answers(answer: Any, message: str) -> None:
    with pytest.raises(CatalogError, match=message):
        parse_search(answer, "q", 1, 10)


def test_preferred_extended() -> None:
    item = {"componentCode": "C1", "componentLibraryType": "expand"}
    page = parse_search(
        {
            "code": 200,
            "data": {
                "componentPageInfo": {
                    "total": 2,
                    "list": [item, {**item, "preferredComponentFlag": True}],
                }
            },
        },
        "q",
        1,
        10,
    )
    assert [h.library_type for h in page.hits] == [
        LibraryType.EXTENDED,
        LibraryType.PREFERRED,
    ]


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        data = gzip.compress(b"image " + self.path.encode())
        self.send_response(200)
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        data = gzip.compress(json.dumps({"echo": body}).encode())
        self.send_response(200)
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def local_server() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


def test_urllib_client_gzip(local_server: str) -> None:
    answer = UrllibClient().post_json(local_server, {"keyword": "x"}, {})
    assert answer == {"echo": {"keyword": "x"}}
    with pytest.raises(NetError):
        UrllibClient(timeout=2).post_json("http://127.0.0.1:9/", {}, {})
    assert UrllibClient().get_bytes(local_server + "x.png", {}) == b"image /x.png"
    with pytest.raises(NetError, match="not an http"):
        UrllibClient().get_bytes("file:///etc/passwd", {})


def test_offline_env_uses_fixtures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))
    hit = get_fab("jlcpcb").catalog().lookup("C900002")
    assert hit is not None and hit.mpn == "KF-R4K7-0603"


def test_cli_search(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))
    monkeypatch.chdir(tmp_path)
    assert main(["component", "search", "330R", "0603", "--basic"]) == 0
    out = capsys.readouterr().out
    assert "C900001" in out and "Basic" in out and "page 1 of 1 (1 parts)" in out
    assert main(["component", "search", "xyzzy-no-such-part"]) == 0
    assert "no JLCPCB parts found" in capsys.readouterr().out
    assert main(["component", "search", "KF-MOD-1", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 1 and payload["hits"][0]["type"] == "extended"


def test_sorted_search(client: FixtureClient) -> None:
    from kifab.core.parts import SearchSort

    sent: list[dict[str, Any]] = []

    class Recording(FixtureClient):
        def post_json(
            self, url: str, payload: dict[str, Any], headers: dict[str, str]
        ) -> Any:
            sent.append(dict(payload))
            return super().post_json(url, payload, headers)

    catalog = JlcpcbCatalog(Recording(FIXTURES))
    plain = catalog.search("100nF 0603")
    assert [h.id for h in plain.hits] == ["C900007", "C900003"]
    by_stock = catalog.search("100nF 0603", sort=SearchSort.STOCK)
    assert [h.id for h in by_stock.hits] == ["C900003", "C900007"]
    by_price = catalog.search("100nF 0603", sort=SearchSort.PRICE)
    assert [h.id for h in by_price.hits] == ["C900003", "C900007"]  # $0.0031 first
    assert "sortMode" not in sent[0]
    assert (sent[1]["sortMode"], sent[1]["sortASC"]) == ("STOCK_SORT", "DESC")
    assert (sent[2]["sortMode"], sent[2]["sortASC"]) == ("PRICE_SORT", "ASC")
    page2 = catalog.search("100nF 0603", page=2)
    assert [h.id for h in page2.hits] == ["C900012", "C900004"] and page2.page == 2
    assert catalog.search("100nF 0603", page=3).hits == ()
