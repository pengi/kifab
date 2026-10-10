"""Capture raw online responses as offline test fixtures (run where the network works).

    uv run --with easyeda2kicad python scripts/fetch_fixtures.py

Writes under testdata/. Responses are stored as received (JSON pretty-printed) so
the parsers in kifab can be developed and tested without network access.
"""

from __future__ import annotations

import gzip
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "testdata"
LCSC_IDS = [
    "C1647", "C23138", "C23162", "C356849", "C7462182", "C7496855",
    "C18723546", "C19725278", "C20416422", "C41413841",
]  # fmt: skip
SEARCH_KEYWORDS = ["100nF 0603", "330R 0603", "STM32F103", "E73-2G4M08S1C", "24C32", "xyzzy-no-such-part"]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
JLC_SEARCH = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"


def request(url: str, data: bytes | None, headers: dict[str, str]) -> bytes:
    req = urllib.request.Request(
        url, data=data, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate", **headers}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body: bytes = resp.read()
        enc = resp.headers.get("Content-Encoding", "")
    if enc == "gzip":
        body = gzip.decompress(body)
    elif enc == "deflate":
        body = zlib.decompress(body)
    return body


def save_json(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(json.loads(raw), indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    except ValueError:
        path.with_suffix(".raw").write_bytes(raw)
        print(f"  not JSON, saved raw: {path.name}", file=sys.stderr)


def attempt(label: str, path: Path, fn: Any) -> None:
    try:
        save_json(path, fn())
        print(f"ok   {label}")
    except Exception as exc:  # fixtures are best effort; report and go on
        print(f"FAIL {label}: {exc}", file=sys.stderr)
    time.sleep(0.5)


def main() -> None:
    easyeda_h = {"Referer": "https://easyeda.com/", "Accept": "application/json"}
    jlc_h = {"Content-Type": "application/json", "Origin": "https://jlcpcb.com", "Referer": "https://jlcpcb.com/parts"}

    for lcsc in LCSC_IDS:
        attempt(
            f"easyeda component {lcsc}",
            OUT / "easyeda/components" / f"{lcsc}.json",
            lambda l=lcsc: request(f"https://easyeda.com/api/products/{l}/components", None, easyeda_h),
        )
        attempt(
            f"easyeda svgs {lcsc}",
            OUT / "easyeda/svgs" / f"{lcsc}.json",
            lambda l=lcsc: request(f"https://easyeda.com/api/products/{l}/svgs", None, easyeda_h),
        )
        body = json.dumps({"keyword": lcsc, "currentPage": 1, "pageSize": 10}).encode()
        attempt(
            f"jlcpcb search by id {lcsc}",
            OUT / "jlcpcb_search/by_id" / f"{lcsc}.json",
            lambda b=body: request(JLC_SEARCH, b, jlc_h),
        )
    numbers = urllib.parse.urlencode({"numbers": json.dumps(LCSC_IDS)}).encode()
    attempt(
        "easyeda searchByNumbers",
        OUT / "easyeda/searchByNumbers.json",
        lambda: request(
            "https://easyeda.com/api/components/searchByNumbers",
            numbers,
            {**easyeda_h, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        ),
    )
    for kw in SEARCH_KEYWORDS:
        slug = "".join(c if c.isalnum() else "_" for c in kw)
        for ptype in (None, "base"):
            payload: dict[str, Any] = {"keyword": kw, "currentPage": 1, "pageSize": 10}
            if ptype:
                payload["componentLibraryType"] = ptype
            name = f"{slug}{'_' + ptype if ptype else ''}.json"
            attempt(
                f"jlcpcb search {kw!r} {ptype or ''}",
                OUT / "jlcpcb_search/keyword" / name,
                lambda p=payload: request(JLC_SEARCH, json.dumps(p).encode(), jlc_h),
            )
        # EasyEDA's legacy component search: as of 2026-10 it returns empty lists for
        # everything (kept as a fixture of that behaviour; use the JLCPCB search instead)
        form = urllib.parse.urlencode(
            {"type": 3, "doc_type": 2, "uid": 0, "returnListStyle": "classifyarr", "wd": kw, "version": "6.5.51"}
        ).encode()
        attempt(
            f"easyeda search {kw!r}",
            OUT / "easyeda/search" / f"{slug}.json",
            lambda f=form: request(
                "https://easyeda.com/api/components/search",
                f,
                {**easyeda_h, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            ),
        )

    # What easyeda2kicad itself writes (symbol .kicad_sym, footprint .kicad_mod, 3D models).
    lib = OUT / "easyeda2kicad_output"
    lib.mkdir(parents=True, exist_ok=True)
    for lcsc in LCSC_IDS:
        (lib / lcsc).mkdir(parents=True, exist_ok=True)  # easyeda2kicad needs it to exist
        proc = subprocess.run(
            [sys.executable, "-m", "easyeda2kicad", f"--lcsc_id={lcsc}", "--full", "--overwrite", "--output", str(lib / lcsc / "jlcpcb")],
            capture_output=True,
            text=True,
        )
        (lib / lcsc / "easyeda2kicad.log").write_text(proc.stdout + proc.stderr)
        print(f"{'ok  ' if proc.returncode == 0 else 'FAIL'} easyeda2kicad {lcsc}")


if __name__ == "__main__":
    main()
