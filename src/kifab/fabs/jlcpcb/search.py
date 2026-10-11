"""JLCPCB's parts search (the anonymous endpoint the jlcpcb.com/parts page uses).

The endpoint is unofficial and may change; the parser is tolerant (missing
fields get defaults) and covered by synthetic answers in the same layout in
``tests/fixtures/jlcpcb_search``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from kifab.core.net import HttpClient, NetError
from kifab.core.parts import (
    CatalogError,
    LibraryType,
    PartHit,
    PriceBreak,
    SearchPage,
    SearchSort,
)

SEARCH_URL = (
    "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/"
    "selectSmtComponentList"
)
HEADERS = {"Origin": "https://jlcpcb.com", "Referer": "https://jlcpcb.com/parts"}
PART_ID = re.compile(r"^C\d+$", re.IGNORECASE)
#: request fields for a sorted search. UNVERIFIED (no access to JLCPCB here):
#: modelled on what the parts website offers; if JLCPCB ignores them, results
#: come back in its own order and the frontends sort what was loaded.
#: scripts/local_verify.py checks whether the order is honoured.
SORT_FIELDS: dict[SearchSort, dict[str, str]] = {
    SearchSort.STOCK: {"sortMode": "STOCK_SORT", "sortASC": "DESC"},
    SearchSort.PRICE: {"sortMode": "PRICE_SORT", "sortASC": "ASC"},
}


def _str(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    return str(value) if value is not None else ""


def _int(item: dict[str, Any], key: str, default: int = 0) -> int:
    value = item.get(key)
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return default


def _library_type(item: dict[str, Any]) -> LibraryType:
    kind = _str(item, "componentLibraryType")
    if kind == "base":
        return LibraryType.BASIC
    if kind == "expand":
        if item.get("preferredComponentFlag") is True:
            return LibraryType.PREFERRED
        return LibraryType.EXTENDED
    return LibraryType.UNKNOWN


def _prices(item: dict[str, Any]) -> tuple[PriceBreak, ...]:
    raw = item.get("componentPrices") or item.get("buyComponentPrices") or []
    out = []
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict) or not isinstance(
            p.get("productPrice"), (int, float)
        ):
            continue
        end = _int(p, "endNumber", -1)
        out.append(
            PriceBreak(
                min_qty=_int(p, "startNumber", 1),
                max_qty=None if end < 0 else end,
                unit_price=float(p["productPrice"]),
            )
        )
    return tuple(sorted(out, key=lambda b: b.min_qty))


#: image fields of a catalogue item, largest first. The answer's layout is not
#: documented; kifab takes whichever of these are present.
_IMAGE_LIST_KEYS = ("productBigImage", "productImage", "productMinImage")
_IMAGE_KEYS = ("componentImageUrl", "productBigImage", "productImage", "minImage")


def _images(item: dict[str, Any]) -> tuple[str, ...]:
    urls: list[str] = []
    entries = item.get("imageList")
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict):
            urls += [_str(entry, k) for k in _IMAGE_LIST_KEYS]
    urls += [_str(item, k) for k in _IMAGE_KEYS]
    out: list[str] = []
    for url in urls:
        if url.startswith("//"):
            url = "https:" + url
        if url.startswith(("https://", "http://")) and url not in out:
            out.append(url)
    return tuple(out)


def parse_part(item: dict[str, Any]) -> PartHit:
    attrs = []
    for a in item.get("attributes") or []:
        if isinstance(a, dict):
            attrs.append(
                (_str(a, "attribute_name_en"), _str(a, "attribute_value_name"))
            )
    return PartHit(
        id=_str(item, "componentCode").upper(),
        mpn=_str(item, "componentModelEn"),
        manufacturer=_str(item, "componentBrandEn"),
        package=_str(item, "componentSpecificationEn"),
        description=_str(item, "describe"),
        category=_str(item, "firstSortName") or _str(item, "componentTypeEn"),
        stock=_int(item, "stockCount"),
        library_type=_library_type(item),
        prices=_prices(item),
        datasheet=_str(item, "dataManualUrl"),
        url=_str(item, "lcscGoodsUrl"),
        min_order=max(1, _int(item, "minPurchaseNum", 1)),
        attributes=tuple(attrs),
        images=_images(item),
    )


def parse_search(answer: Any, query: str, page: int, page_size: int) -> SearchPage:
    if not isinstance(answer, dict):
        raise CatalogError("JLCPCB search: the answer is not a JSON object")
    if answer.get("code") not in (200, None):
        message = answer.get("message") or answer.get("msg") or answer.get("code")
        raise CatalogError(f"JLCPCB search failed: {message}")
    data = answer.get("data") or {}
    info = data.get("componentPageInfo") if isinstance(data, dict) else None
    if not isinstance(info, dict):
        raise CatalogError("JLCPCB search: no componentPageInfo in the answer")
    items = info.get("list") or []
    hits = tuple(parse_part(i) for i in items if isinstance(i, dict))
    return SearchPage(
        query=query,
        hits=hits,
        total=_int(info, "total", len(hits)),
        page=page,
        page_size=page_size,
    )


def search(
    client: HttpClient,
    query: str,
    *,
    page: int = 1,
    page_size: int = 10,
    basic_only: bool = False,
    sort: SearchSort = SearchSort.RELEVANCE,
) -> SearchPage:
    payload: dict[str, Any] = {
        "keyword": query,
        "currentPage": page,
        "pageSize": page_size,
    }
    if basic_only:
        payload["componentLibraryType"] = "base"
    payload.update(SORT_FIELDS.get(sort, {}))
    try:
        answer = client.post_json(SEARCH_URL, payload, HEADERS)
    except NetError as exc:
        raise CatalogError(f"JLCPCB search: {exc}") from exc
    return parse_search(answer, query, page, page_size)


def lookup(client: HttpClient, part_id: str) -> PartHit | None:
    """The part with exactly this LCSC number, or ``None``."""
    wanted = part_id.strip().upper()
    found = search(client, wanted, page_size=10)
    return next((h for h in found.hits if h.id == wanted), None)


def _slug(text: str) -> str:
    return "".join(c if c.isascii() and c.isalnum() else "_" for c in text)


def image(client: HttpClient, url: str) -> bytes:
    try:
        return client.get_bytes(url, {"Referer": HEADERS["Referer"]})
    except NetError as exc:
        raise CatalogError(f"JLCPCB image: {exc}") from exc


class FixtureClient:
    """Answers search requests from ``<root>/jlcpcb_search`` (offline mode).

    ``{"keyword": "C123456"}`` -> ``by_id/C123456.json``; text queries ->
    ``keyword/<slug>[_base].json``, later pages ``..._p<n>.json``. Anything
    else is an empty result (page 1) or an empty later page, like the real
    endpoint. Sorted requests are sorted here, as the fab would. Downloads are served from
    ``images/<last path component>``.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root) / "jlcpcb_search"

    def post_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> Any:
        keyword = str(payload.get("keyword", ""))
        base = payload.get("componentLibraryType") == "base"
        if PART_ID.match(keyword):
            path = self.root / "by_id" / f"{keyword.upper()}.json"
        else:
            path = (
                self.root
                / "keyword"
                / f"{_slug(keyword)}{'_base' if base else ''}.json"
            )
        page = int(payload.get("currentPage", 1))
        if page > 1:  # later pages: <slug>[_base]_p<page>.json
            path = path.with_name(f"{path.stem}_p{page}.json")
        if not path.is_file():
            return {
                "code": 200,
                "data": {"componentPageInfo": {"list": None, "total": 0}},
            }
        answer = json.loads(path.read_text(encoding="utf-8"))
        mode = payload.get("sortMode")
        items = answer["data"]["componentPageInfo"]["list"]
        if mode and items:  # emulate the fab sorting before it pages

            def key(item: dict[str, Any]) -> float:
                if mode == "STOCK_SORT":
                    return float(item.get("stockCount") or 0)
                prices = item.get("componentPrices") or [{}]
                return float(prices[0].get("productPrice", 1e9))

            items.sort(key=key, reverse=payload.get("sortASC") == "DESC")
        return answer

    def get_bytes(self, url: str, headers: dict[str, str]) -> bytes:
        path = self.root / "images" / url.rstrip("/").rsplit("/", 1)[-1]
        if not path.is_file():
            raise NetError(f"{url}: 404 Not Found (offline)")
        return path.read_bytes()


class JlcpcbCatalog:
    """:class:`kifab.fabs.base.Catalog` for JLCPCB; lookups are cached per instance."""

    def __init__(self, client: HttpClient) -> None:
        self.client = client
        self._cache: dict[str, PartHit | None] = {}

    def search(
        self,
        query: str,
        *,
        page: int = 1,
        page_size: int = 10,
        basic_only: bool = False,
        sort: SearchSort = SearchSort.RELEVANCE,
    ) -> SearchPage:
        return search(
            self.client,
            query,
            page=page,
            page_size=page_size,
            basic_only=basic_only,
            sort=sort,
        )

    def image(self, url: str) -> bytes:
        return image(self.client, url)

    def lookup(self, part_id: str) -> PartHit | None:
        key = part_id.strip().upper()
        if key not in self._cache:
            self._cache[key] = lookup(self.client, key)
        return self._cache[key]
