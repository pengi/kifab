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
from kifab.core.parts import CatalogError, LibraryType, PartHit, PriceBreak, SearchPage

SEARCH_URL = (
    "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/"
    "selectSmtComponentList"
)
HEADERS = {"Origin": "https://jlcpcb.com", "Referer": "https://jlcpcb.com/parts"}
PART_ID = re.compile(r"^C\d+$", re.IGNORECASE)


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
) -> SearchPage:
    payload: dict[str, Any] = {
        "keyword": query,
        "currentPage": page,
        "pageSize": page_size,
    }
    if basic_only:
        payload["componentLibraryType"] = "base"
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
    return "".join(c if c.isalnum() else "_" for c in text)


class FixtureClient:
    """Answers search requests from ``<root>/jlcpcb_search`` (offline mode).

    ``{"keyword": "C123456"}`` -> ``by_id/C123456.json``; text queries ->
    ``keyword/<slug>[_base].json``. Anything else is an empty result (page 1)
    or an empty later page, like the real endpoint.
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
        if payload.get("currentPage", 1) != 1 or not path.is_file():
            return {
                "code": 200,
                "data": {"componentPageInfo": {"list": None, "total": 0}},
            }
        return json.loads(path.read_text(encoding="utf-8"))


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
    ) -> SearchPage:
        return search(
            self.client, query, page=page, page_size=page_size, basic_only=basic_only
        )

    def lookup(self, part_id: str) -> PartHit | None:
        key = part_id.strip().upper()
        if key not in self._cache:
            self._cache[key] = lookup(self.client, key)
        return self._cache[key]
